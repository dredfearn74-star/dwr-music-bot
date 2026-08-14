"""
DWR MUSIC — Auto-Publisher (Facebook + Instagram)
=================================================
Posts David Wayne Redfearn Music content — images AND video reels — to the
DWR Music Facebook Page and Instagram, on a schedule, hands-free.

Three files run the whole thing:
  - brands.csv          -> account directory (FB Page ID + Instagram ID for DWR Music)
  - content_queue.csv   -> the post list; one row per post, each tagged with a date
  - META_TOKEN (secret) -> a long-lived Meta token for the DWR Music page (a GitHub Actions secret)

Clickable tagging (optional, per-row):
  - fb_page_tags -> Facebook PAGE IDs to mention (e.g. the venue's Page). The bot wraps
                    each as @[ID] in the post text so Facebook renders a clickable mention.
                    (Meta blocks tagging personal PROFILES via the API — those stay a manual
                    edit-after-post; only Pages can be tagged hands-free.)
  - ig_mentions  -> Instagram @handles to mention. Instagram auto-links any @handle in the
                    caption, so these become real clickable mentions on IG.
  Leave either cell blank to skip. Older queues without these columns still work unchanged.

Link-in-first-comment (Facebook reach protection):
  Facebook throttles posts that put an off-site link in the body, so the bot posts the
  link as the FIRST COMMENT instead (Meta's own guidance). Two places set it:
  - brands.csv column `default_comment_link` -> the brand's standing link (e.g. MotiveAF's
    Printify store, DWR's YouTube channel). Auto-commented on every FB post for that brand.
  - content_queue.csv column `first_comment`  -> per-post override: a specific link (a track,
    an event page), or `none` to suppress the comment on that post. Blank = use brand default.
  Instagram is excluded on purpose (links in IG comments/captions are never clickable).

Safety gates (added 2026-08-14, after two live failures):
  - CAPTION GATE  -> a row with an empty caption is REFUSED, never posted. Put the word
                     `none` in the caption cell if a post is meant to be wordless on purpose.
  - ASPECT GATE   -> every video is measured with ffprobe before posting. Anything that
                     isn't vertical 9:16 (e.g. a 1920x1080 landscape export with the
                     vertical footage pillarboxed inside it) is REFUSED, because Meta
                     publishes it visibly squished. If ffprobe isn't available the gate
                     logs a warning and stands down rather than blocking a good post.
  A refused row is marked FAILED and the reason is written to autopublish_log.txt.

Each run: for every QUEUED row whose date is today (US Central) or earlier, it posts to
Facebook and/or Instagram, then marks the row POSTED. A .mp4/.mov/.m4v media file is posted
as a reel/video; anything else is posted as a photo. A row is skipped safely if the
brand/account/token is missing, so it never posts to the wrong place.

Nothing here ever asks for a password. Runs on GitHub Actions (Facebook's API is not
reachable from the Cowork sandbox, so the bot lives on GitHub).
"""
import csv
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import time

import requests

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Chicago")
except Exception:
    TZ = None

HERE = pathlib.Path(__file__).parent
GRAPH_VERSION_DEFAULT = "v23.0"


def now_ct():
    return datetime.datetime.now(TZ) if TZ else datetime.datetime.utcnow()


def read_env():
    # GitHub Actions secrets arrive as environment variables. A local .env can override for testing.
    e = dict(os.environ)
    p = HERE / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                e.setdefault(k.strip(), v.strip())
    return e


def read_brands():
    d = {}
    p = HERE / "brands.csv"
    if not p.exists():
        return d
    with open(p, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            d[r["brand"].strip().lower()] = r
    return d


def log(m):
    line = f"[{now_ct():%Y-%m-%d %H:%M:%S}] {m}"
    print(line, flush=True)
    with open(HERE / "autopublish_log.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def is_video(url):
    return url.lower().split("?")[0].endswith((".mp4", ".mov", ".m4v"))


def _split_tokens(s):
    """Split a tag/mention cell on commas, pipes, or newlines into clean tokens."""
    if not s:
        return []
    return [p.strip() for p in re.split(r"[,\|\n]+", str(s)) if p.strip()]


def build_fb_caption(cap, fb_page_tags):
    """Append clickable Facebook PAGE-mentions to a caption.

    Facebook turns the token @[PAGE_ID] inside a post's text into a clickable
    mention of that Page (works for Pages you manage or that allow it — e.g. the
    venue's Page, another band's Page). It does NOT work for personal profiles;
    Meta blocks tagging people via the API, so those stay a manual edit-after-post.

    fb_page_tags: a cell like "1234567890, @[1112223334]" (numeric IDs and/or
    already-wrapped @[id] tokens). Anything non-numeric is passed through as-is so
    a hand-written @[id] still works. Mentions are placed at the END of the caption.
    """
    cap = cap or ""
    mentions = []
    for t in _split_tokens(fb_page_tags):
        if t.startswith("@[") and t.endswith("]"):
            mentions.append(t)               # already wrapped
        elif t.isdigit():
            mentions.append("@[%s]" % t)      # bare Page ID -> wrap it
        else:
            mentions.append(t)                # pass through (defensive)
    # de-dupe, keep order
    seen, uniq = set(), []
    for m in mentions:
        if m not in seen:
            seen.add(m); uniq.append(m)
    if not uniq:
        return cap
    tail = " ".join(uniq)
    return (cap.rstrip() + "\n\n" + tail).strip() if cap.strip() else tail


def build_ig_caption(cap, ig_mentions):
    """Append Instagram @mentions to a caption.

    Instagram auto-links any @handle in a caption to that account (for business/
    creator accounts). ig_mentions: a cell like "@handle1, handle2" — the @ is
    optional and normalized. Mentions are placed at the END of the caption.
    """
    cap = cap or ""
    handles, seen = [], set()
    for h in _split_tokens(ig_mentions):
        h = "@" + h.lstrip("@")
        if h != "@" and h.lower() not in seen:
            seen.add(h.lower()); handles.append(h)
    if not handles:
        return cap
    tail = " ".join(handles)
    return (cap.rstrip() + "\n\n" + tail).strip() if cap.strip() else tail


def fb_post(ver, pid, tok, cap, media):
    base = f"https://graph.facebook.com/{ver}"
    if media and is_video(media):
        url = f"{base}/{pid}/videos"
        data = {"description": cap, "file_url": media, "access_token": tok}
    elif media:
        url = f"{base}/{pid}/photos"
        data = {"caption": cap, "url": media, "access_token": tok}
    else:
        url = f"{base}/{pid}/feed"
        data = {"message": cap, "access_token": tok}
    r = requests.post(url, data=data, timeout=300)
    r.raise_for_status()
    j = r.json()
    return j.get("id") or j.get("post_id")


def ig_post(ver, igid, tok, cap, media):
    if not media:
        raise ValueError("Instagram needs a public image or video URL")
    base = f"https://graph.facebook.com/{ver}"
    c = {"caption": cap, "access_token": tok}
    if is_video(media):
        c.update({"media_type": "REELS", "video_url": media})
    else:
        c.update({"image_url": media})
    r = requests.post(f"{base}/{igid}/media", data=c, timeout=300)
    r.raise_for_status()
    cid = r.json()["id"]
    # Wait for Instagram to finish processing (video reels take a while).
    for _ in range(50):
        s = requests.get(f"{base}/{cid}", params={"fields": "status_code", "access_token": tok}, timeout=60)
        if s.ok and s.json().get("status_code") == "FINISHED":
            break
        if s.ok and s.json().get("status_code") == "ERROR":
            raise RuntimeError(f"IG media processing error: {s.json()}")
        time.sleep(6)
    p = requests.post(f"{base}/{igid}/media_publish", data={"creation_id": cid, "access_token": tok}, timeout=120)
    p.raise_for_status()
    return p.json().get("id")



def resolve_page_token(ver, pid, tok):
    """Exchange the account/system-user token for the PAGE's own access token.
    Posting to a Page requires the Page token, not the top-level token — using the
    top-level token directly is what caused the 403 Forbidden on /photos."""
    try:
        base = f"https://graph.facebook.com/{ver}"
        r = requests.get(f"{base}/{pid}", params={"fields": "access_token", "access_token": tok}, timeout=60)
        if r.ok:
            return (r.json() or {}).get("access_token") or ""
    except Exception:
        pass
    return ""


def resolve_ig(ver, pid, tok):
    """Look up the Instagram Business account id connected to a Facebook Page.
    Lets brands.csv leave ig_user_id blank — the bot fills it in at runtime."""
    try:
        base = f"https://graph.facebook.com/{ver}"
        r = requests.get(f"{base}/{pid}", params={"fields": "instagram_business_account", "access_token": tok}, timeout=60)
        if r.ok:
            iba = r.json().get("instagram_business_account") or {}
            return iba.get("id") or ""
    except Exception:
        pass
    return ""


def fb_comment(ver, post_id, tok, message):
    """Post a comment on a Facebook post — as the Page.

    Facebook REDUCES the reach of posts that have an off-site link in the body
    (Meta's own data: ~97% of views go to posts with NO external link). So the
    link goes in the FIRST COMMENT instead: the photo/caption gets full reach up
    top, and the link sits in the comments where it isn't penalized."""
    base = f"https://graph.facebook.com/{ver}"
    r = requests.post(f"{base}/{post_id}/comments", data={"message": message, "access_token": tok}, timeout=120)
    r.raise_for_status()
    return r.json().get("id")


# ---------------------------------------------------------------------------
# SAFETY GATES — added 2026-08-14 after two live failures:
#   (1) reels published with a completely BLANK caption, and
#   (2) reels published SQUISHED because a landscape (pillarboxed) export was
#       force-crammed into 1080x1920 with a bare `scale=1080:1920`.
# Both slipped through because nothing checked the post before it went out.
# These two functions are the check. A row that fails a gate is marked FAILED
# with the reason written to the log — it is never posted.
# ---------------------------------------------------------------------------

BLANK_OK = ("none", "-", "no", "skip", "off", "blank")


def caption_gate(row):
    """Refuse to post a row with an empty caption.

    A blank caption is almost always a mistake (it is exactly what produced the
    'Your reel' posts with no text). If a post is MEANT to carry no words —
    a flyer where the image says everything — put the word `none` in the
    caption cell to say so on purpose.

    Returns '' when the row is fine, or a plain-English reason to refuse.
    """
    cap = (row.get("caption") or "").strip()
    if cap.lower() in BLANK_OK:
        return ""          # deliberately wordless — allowed
    if not cap:
        return ("caption is EMPTY. Nothing posts without words. Write a caption, "
                "or put `none` in the caption cell if it is meant to be wordless.")
    return ""


def probe_video(url):
    """Read a remote video's real pixel size with ffprobe (no download).

    Returns (width, height) or None when it can't tell — ffprobe missing, a
    network hiccup, or an unreadable file. Unknown is NOT treated as failure:
    the bot warns and carries on rather than blocking a good post over tooling.
    """
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", url],
            capture_output=True, text=True, timeout=180,
        )
        if out.returncode != 0:
            return None
        st = (json.loads(out.stdout).get("streams") or [{}])[0]
        w, h = int(st.get("width") or 0), int(st.get("height") or 0)
        return (w, h) if w and h else None
    except Exception:
        return None


def aspect_gate(url):
    """Refuse to post a video that is not vertical 9:16.

    THE BUG THIS CATCHES: some DaVinci exports came out 1920x1080 LANDSCAPE with
    the vertical footage pillarboxed (black bars) inside them. Pushed to Meta as
    a reel, that publishes visibly squished. The fix is a genuine 9:16 re-export.

    If you ever need to convert, NEVER use a bare `scale=W:H` — it force-crams
    the picture into the box and crushes it. Use:
      -vf "scale=1080:1920:force_original_aspect_ratio=decrease,\\
            pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1"
    which fits the picture and pads the gap instead of distorting it.

    Returns '' when the clip is fine (or unknowable), or a reason to refuse.
    """
    if not is_video(url):
        return ""                       # photos are not gated
    dims = probe_video(url)
    if not dims:
        log(f"WARN: could not read the video size for {url} — posting anyway (aspect gate skipped).")
        return ""
    w, h = dims
    target = 9.0 / 16.0
    ratio = w / float(h)
    if abs(ratio - target) <= 0.02:     # ~9:16 within rounding
        return ""
    shape = "landscape" if w > h else "the wrong vertical shape"
    return (f"video is {w}x{h} ({shape}), not 9:16. Publishing it would look SQUISHED. "
            f"Re-export vertical from DaVinci, or pad it with "
            f"scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1 "
            f"— never a bare scale=1080:1920.")


def resolve_comment_link(row, cfg):
    """Decide the link to drop as the first comment on a post.

    - row 'first_comment' has a value  -> use it (e.g. a specific track or event link).
    - row 'first_comment' = none/-/skip/no/off -> suppress (no comment this post).
    - row 'first_comment' blank        -> fall back to the brand's default_comment_link
                                          (brands.csv) — e.g. MotiveAF's store, DWR's YouTube.
    Returns '' when there's nothing to comment. Instagram is intentionally excluded:
    links in IG comments/captions are never clickable, so IG posts stay link-free."""
    v = (row.get("first_comment") or "").strip()
    if v:
        return "" if v.lower() in ("none", "-", "no", "skip", "off") else v
    return (cfg.get("default_comment_link") or "").strip()


def main():
    env = read_env()
    brands = read_brands()
    ver = env.get("GRAPH_VERSION", GRAPH_VERSION_DEFAULT)
    gtok = env.get("META_TOKEN") or env.get("GLOBAL_TOKEN") or env.get("FB_TOKEN") or ""
    qp = HERE / "content_queue.csv"
    if not qp.exists():
        log("No content_queue.csv found. Nothing to do.")
        return
    with open(qp, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        log("Queue empty. Nothing to do.")
        return
    fieldnames = list(rows[0].keys())
    today = now_ct().date()
    changed = 0
    for row in rows:
        st = (row.get("status") or "").strip().upper()
        if st in ("POSTED", "SKIPPED", "DRAFT", "HOLD", "FAILED"):
            continue  # only QUEUED (or blank) rows are eligible
        try:
            due = datetime.datetime.strptime(row["date"].strip(), "%Y-%m-%d").date()
        except Exception:
            log(f"Bad/missing date on a row ({row.get('date')!r}); skipping that row.")
            continue
        if due > today:
            continue  # not due yet
        bkey = (row.get("brand") or "").strip().lower()
        cfg = brands.get(bkey)
        if not cfg:
            row["status"] = "SKIPPED"; changed += 1
            log(f"SKIPPED {row.get('brand')} {due}: brand not in brands.csv")
            continue
        tok = (cfg.get("token") or "").strip() or gtok
        if not tok:
            row["status"] = "SKIPPED"; changed += 1
            log(f"SKIPPED {row.get('brand')} {due}: no token configured (set the META_TOKEN secret)")
            continue
        # Posting to a Page needs the PAGE token (not the top-level token) — resolve it, fall back if unavailable.
        page_tok = resolve_page_token(ver, (cfg.get("fb_page_id") or "").strip(), tok) if cfg.get("fb_page_id") else ""
        post_tok = page_tok or tok
        plat = (row.get("platform") or "both").strip().lower()
        cap = row.get("caption", "")
        media = (row.get("media_url") or "").strip()

        # ---- SAFETY GATES: check the post BEFORE it goes anywhere near Meta ----
        stop = caption_gate(row) or (aspect_gate(media) if media else "")
        if stop:
            row["status"] = "FAILED"; changed += 1
            log(f"REFUSED [{row.get('brand')}] {due} {plat}: {stop}")
            continue
        if (cap or "").strip().lower() in BLANK_OK:
            cap = ""       # `none` means deliberately wordless

        # Optional tagging columns (absent in older queues -> treated as blank):
        fb_cap = build_fb_caption(cap, row.get("fb_page_tags", ""))   # clickable Page-mentions
        ig_cap = build_ig_caption(cap, row.get("ig_mentions", ""))    # auto-linking @handles
        comment_link = resolve_comment_link(row, cfg)                 # off-site link -> first comment (FB only)
        res = []
        try:
            if plat in ("facebook", "fb", "both") and cfg.get("fb_page_id"):
                fb_id = fb_post(ver, cfg["fb_page_id"].strip(), post_tok, fb_cap, media)
                res.append("FB:" + str(fb_id))
                if comment_link and fb_id:
                    # Link-in-first-comment (Facebook throttles links in the body).
                    try:
                        res.append("cmt:" + str(fb_comment(ver, fb_id, post_tok, comment_link)))
                    except Exception as ce:
                        log(f"WARN [{row.get('brand')}] {due}: post OK but first-comment failed: {ce}")
            if plat in ("instagram", "ig", "both"):
                igid = (cfg.get("ig_user_id") or "").strip() or (resolve_ig(ver, cfg.get("fb_page_id","").strip(), post_tok) if cfg.get("fb_page_id") else "")
                if igid:
                    res.append("IG:" + str(ig_post(ver, igid, post_tok, ig_cap, media)))
            row["status"] = "POSTED"; changed += 1
            log(f"POSTED [{row.get('brand')}] {due} {plat} -> {', '.join(res) or '(nothing matched platform)'}")
        except Exception as e:
            row["status"] = "FAILED"; changed += 1
            log(f"FAILED [{row.get('brand')}] {due} {plat}: {e}")
    if changed:
        # Write the queue SAFELY. The old version handed a bare open() to DictWriter and
        # never closed it, so the buffer could be thrown away and content_queue.csv was
        # left 0 BYTES — the whole queue gone. Now: write a temp file, close it, verify it
        # actually has every row, and only then swap it into place. A crash mid-write
        # damages the temp file, never the real queue.
        tmp = qp.with_suffix(".csv.tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        with open(tmp, encoding="utf-8") as fh:
            written = list(csv.DictReader(fh))
        if len(written) != len(rows):
            tmp.unlink(missing_ok=True)
            log(f"ABORTED queue save: wrote {len(written)} rows but expected {len(rows)}. "
                f"content_queue.csv left untouched — nothing was lost.")
            return
        os.replace(tmp, qp)
        log(f"Queue updated ({changed} row(s) changed, {len(written)} rows saved).")
    else:
        log("Nothing due today. Exiting cleanly.")


if __name__ == "__main__":
    main()
