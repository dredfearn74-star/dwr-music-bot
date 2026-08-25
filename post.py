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
  link as the FIRST COMMENT instead (Meta's own guidance), adds a "link in the comments"
  line to the caption so people know to look, and then READS THE COMMENT BACK off the
  live post to prove it actually landed. Ways to set it:
  - content_queue.csv `first_comment` = yt:Song Title  -> the bot LOOKS THE SONG UP on the
    brand's YouTube channel and comments the link to THAT SPECIFIC SONG. This is the rule
    for song reels (David, 2026-08-18): point at the song, never at the channel front door.
    If it cannot find the song with confidence it publishes NOTHING and says why, because
    a comment pointing at the wrong song is worse than no comment.
  - content_queue.csv `first_comment` = https://...   -> that exact link, no lookup.
  - content_queue.csv `first_comment` = none          -> no comment on this post, on purpose
    (the evergreen quote cards use this: they are not selling anything).
  - brands.csv `default_comment_link`                 -> the brand's standing link, used only
    when first_comment is blank (e.g. MotiveAF's store). DWR's is blank on purpose.
  Instagram gets the comment too, at David's request. Note that Instagram does not make
  links in comments clickable — it still puts the exact song in front of people, and an IG
  comment failure is a warning, never a red run. Facebook is the clickable one.

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


# =============================================================================
# YOUTUBE — POINT AT THE SONG, NOT THE CHANNEL
# =============================================================================
# David's rule (2026-08-18): when a REEL of a song drops, the first comment must
# link to THAT SONG on YouTube — not the channel front door. So the bot looks the
# song up on the DWR channel itself and comes back with the real watch URL.
#
# How a row asks for it:  first_comment = yt:Get On Up
#                                          ^^^ the song title, as it is on YouTube
#
# Two ways it finds the song, in order:
#   1. YOUTUBE_API_KEY secret set -> YouTube Data API search, whole catalogue.
#   2. No key -> the channel's public RSS feed (last 15 uploads), no key needed.
#
# It REFUSES to guess. If nothing matches the title well enough, it posts NO
# comment and says so loudly, because a comment pointing at the wrong song is
# worse than no comment at all.
# =============================================================================

YT_WATCH = "https://www.youtube.com/watch?v={}"
YT_MATCH_FLOOR = 0.72          # below this, we do not believe it is the same song
_YT_CACHE = {}                 # channel_id -> [(title, video_id), ...] (RSS only)
_YT_CHOSEN = {}                # watch_url -> the video title we picked (so the caption can be honest)


def _yt_is_short(title):
    return bool(re.search(r"#?\bshorts?\b", title or "", re.I))


def _yt_norm(s):
    """Squash a title down to comparable words: lowercase, no punctuation, no filler."""
    s = (s or "").lower()
    s = re.sub(r"[‘’“”]", "'", s)
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)              # drop "(live)", "[official video]"
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    # Words that describe the UPLOAD, not the SONG. Stripping them is what lets the
    # bot see that "Trouble #Shorts" and "Trouble (Taylor Swift) Live Open Mic" are
    # the same song — which they are, and which David's channel has two of.
    drop = {"official", "video", "audio", "lyric", "lyrics", "live", "cover", "hd", "4k",
            "the", "a", "an", "by", "feat", "ft", "david", "wayne", "redfearn", "dwr",
            "short", "shorts", "original", "originals", "open", "mic", "night", "nights",
            "session", "sessions", "acoustic", "full", "song", "music", "performance",
            "performing", "at", "and", "with", "murph", "murphs", "mary", "marys",
            "newton", "iowa", "clip", "version", "take"}
    words = [w for w in s.split() if w and w not in drop]
    return " ".join(words)


def _yt_score(want, have):
    """0..1 — how confident are we that `have` is the song `want` asked for."""
    w, h = _yt_norm(want), _yt_norm(have)
    if not w or not h:
        return 0.0
    if w == h:
        return 1.0
    if h.startswith(w) or w.startswith(h):
        return 0.93
    if w in h or h in w:
        return 0.85
    ws, hs = set(w.split()), set(h.split())
    if not ws or not hs:
        return 0.0
    return len(ws & hs) / float(len(ws | hs))          # Jaccard on the words


def _yt_from_api(channel_id, api_key, query):
    """Search the whole channel via the YouTube Data API. Returns [(title, video_id)]."""
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={"part": "snippet", "channelId": channel_id, "q": query, "type": "video",
                "maxResults": 25, "key": api_key},
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"YouTube API -> HTTP {r.status_code}: {(r.text or '')[:250]}")
    out = []
    for it in (r.json() or {}).get("items", []):
        vid = ((it.get("id") or {}).get("videoId") or "").strip()
        title = ((it.get("snippet") or {}).get("title") or "").strip()
        if vid and title:
            out.append((title, vid))
    return out


def _yt_from_rss(channel_id):
    """No-key fallback: the channel's public feed (most recent ~15 uploads)."""
    if channel_id in _YT_CACHE:
        return _YT_CACHE[channel_id]
    r = requests.get("https://www.youtube.com/feeds/videos.xml",
                     params={"channel_id": channel_id}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"YouTube feed -> HTTP {r.status_code}")
    pairs = []
    for entry in re.split(r"<entry>", r.text)[1:]:
        vid = re.search(r"<yt:videoId>(.*?)</yt:videoId>", entry)
        title = re.search(r"<title>(.*?)</title>", entry, re.S)
        if vid and title:
            t = re.sub(r"&amp;", "&", title.group(1))
            t = re.sub(r"&(quot|#39|apos);", "'", t).strip()
            pairs.append((t, vid.group(1).strip()))
    _YT_CACHE[channel_id] = pairs
    return pairs


def yt_lookup(song, cfg, env):
    """Find `song` on the brand's YouTube channel.

    Returns (watch_url, explanation). watch_url is '' when we are not confident —
    and the explanation always says why, in plain English, so the log is useful.
    """
    song = (song or "").strip()
    if not song:
        return "", "no song title given after 'yt:'"
    channel_id = (cfg.get("youtube_channel_id") or "").strip()
    if not channel_id:
        return "", ("no youtube_channel_id for brand '%s' — add it to brands.csv"
                    % cfg.get("brand", "?"))
    api_key = (env.get("YOUTUBE_API_KEY") or "").strip()
    how = "YouTube Data API" if api_key else "channel RSS feed (last ~15 uploads)"
    try:
        pairs = _yt_from_api(channel_id, api_key, song) if api_key else _yt_from_rss(channel_id)
    except Exception as e:
        return "", f"lookup via {how} failed: {e}"
    if not pairs:
        return "", f"{how} returned no videos for this channel"
    scored = sorted(((_yt_score(song, t), t, v) for t, v in pairs), reverse=True)
    best, runner = scored[0], (scored[1] if len(scored) > 1 else (0.0, "", ""))
    if best[0] < YT_MATCH_FLOOR:
        near = ", ".join(f"{t!r}" for _, t, _ in scored[:3])
        return "", (f"no confident match for {song!r} on the channel via {how} "
                    f"(best was {best[1]!r} at {best[0]:.2f}, floor {YT_MATCH_FLOOR}). "
                    f"Closest titles: {near}. Put the full YouTube URL in first_comment to be sure.")
    # ---- TIES -------------------------------------------------------------
    # David's channel has TWO uploads of most songs (found live 2026-08-18: e.g.
    # "Trouble 🎸 open mic night #Shorts" AND "Trouble (Taylor Swift) 🎸 Live Open
    # Mic #Shorts"). Refusing every tie would hold back every single reel.
    #
    # The refusal exists to stop us linking the WRONG SONG. If the tied titles are
    # the SAME song once the upload-noise is stripped, there is no wrong answer —
    # so pick one, say which, and list the alternates so David can override with a
    # full URL. Only a tie between genuinely DIFFERENT songs still refuses.
    tied = [c for c in scored if best[0] - c[0] < 0.05]
    if len(tied) > 1:
        same_song = {_yt_norm(t) for _, t, _ in tied}
        if len(same_song) > 1:
            names = " vs ".join(repr(t) for _, t, _ in tied[:3])
            return "", (f"two DIFFERENT songs match {song!r} equally well ({names}) — "
                        f"refusing to guess. Put the full YouTube URL in first_comment.")
        # Same song, more than one upload. Prefer the full version over a #Shorts
        # clip — the caption promises "full song on YouTube", so honour that. Then
        # prefer the more descriptive title, then feed order, so the choice is stable.
        def rank(c):
            return (1 if _yt_is_short(c[1]) else 0, -len(c[1]))
        tied.sort(key=rank)
        chosen = tied[0]
        others = ", ".join(repr(t) for _, t, _ in tied[1:3])
        _YT_CHOSEN[YT_WATCH.format(chosen[2])] = chosen[1]
        # Say what we ACTUALLY did. Claiming "picked the full version" when every
        # upload is a #Shorts clip would be a lie in the log, and the log is the
        # only thing standing between us and "I thought it posted."
        if not _yt_is_short(chosen[1]) and any(_yt_is_short(t) for _, t, _ in tied[1:]):
            why_this = "picked the full version over a #Shorts clip"
        elif all(_yt_is_short(t) for _, t, _ in tied):
            why_this = "they are ALL #Shorts clips — picked the more descriptive title"
        else:
            why_this = "picked the more descriptive title"
        return YT_WATCH.format(chosen[2]), (
            f"matched {chosen[1]!r} at {chosen[0]:.2f} via {how}. "
            f"NOTE: your channel has {len(tied)} uploads of this song ({others}) — "
            f"{why_this}. Put a full YouTube URL in first_comment if you want a different one.")
    _YT_CHOSEN[YT_WATCH.format(best[2])] = best[1]
    return YT_WATCH.format(best[2]), f"matched {best[1]!r} at {best[0]:.2f} via {how}"


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


def graph_raise(r, what):
    """Turn a Graph API failure into an error that actually says what went wrong.

    `r.raise_for_status()` only ever produced "400 Client Error: Bad Request",
    which is useless — it cost us a whole day of guessing when Micah Spurlock's
    spotlight failed on 2026-08-17. Meta puts the real reason in the JSON body,
    so we read it out and put it in the log where a human can act on it.
    """
    if r.ok:
        return
    detail = ""
    try:
        e = (r.json() or {}).get("error") or {}
        bits = [e.get("message") or "", ]
        if e.get("error_user_title"):  bits.append("user_title=" + str(e["error_user_title"]))
        if e.get("error_user_msg"):    bits.append("user_msg=" + str(e["error_user_msg"]))
        if e.get("type") is not None:  bits.append("type=" + str(e["type"]))
        if e.get("code") is not None:  bits.append("code=" + str(e["code"]))
        if e.get("error_subcode") is not None: bits.append("subcode=" + str(e["error_subcode"]))
        if e.get("fbtrace_id"):        bits.append("fbtrace=" + str(e["fbtrace_id"]))
        detail = " | ".join(b for b in bits if b)
    except Exception:
        detail = (r.text or "")[:400]
    raise RuntimeError(f"{what} -> HTTP {r.status_code}: {detail or '(no error body returned)'}")


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
    graph_raise(r, f"Facebook post to {url.rsplit('/',1)[-1]}")
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
    graph_raise(r, "Instagram media container")
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
    graph_raise(p, "Instagram publish")
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
    graph_raise(r, "Facebook first comment")
    return r.json().get("id")


# =============================================================================
# FIRST COMMENT — POST IT, THEN PROVE IT LANDED
# =============================================================================
# "Bots finish the job — save, publish, verify." Posting a comment and walking
# away is how a link quietly goes missing. Every comment is now read back off the
# live post before we call it done.
# =============================================================================

LINK_IN_COMMENTS = "🔗 Full song on YouTube — link in the comments 👇"
LINK_IN_COMMENTS_SHORT = "🔗 Hear it on YouTube — link in the comments 👇"


def caption_says_link_in_comments(cap):
    return "link in the comment" in (cap or "").lower() or "link in comments" in (cap or "").lower()


def add_link_line(cap, line=LINK_IN_COMMENTS):
    """Tell people the link is down in the comments — but only say it once.

    Facebook throttles a post with an off-site link in the body, so the link goes
    in the first comment. That only works if the post TELLS people to look there.
    Idempotent: a caption that already says it is left exactly as written.
    """
    cap = cap or ""
    if caption_says_link_in_comments(cap):
        return cap
    return (cap.rstrip() + "\n\n" + line).strip() if cap.strip() else line


def comment_caption_line(link, cfg):
    """Work out what the caption should SAY about the comment — or say nothing.

    The line has to match what the link actually is. A song reel earns "full song
    on YouTube"; MotiveAF's standing store link is a different animal and keeps
    posting exactly as it does today (no line) unless the brand sets its own via
    a `comment_caption_line` column in brands.csv. Saying "full song on YouTube"
    over a t-shirt store is exactly the kind of small wrongness that erodes trust.
    """
    override = (cfg.get("comment_caption_line") or "").strip()
    if override:
        return override
    low = (link or "").lower()
    if "youtube.com" in low or "youtu.be" in low:
        # Only promise a FULL song when we actually linked one. David's channel is
        # mostly #Shorts clips right now, and a caption that says "full song" over a
        # 60-second clip is a small lie the whole system is supposed to prevent.
        title = _YT_CHOSEN.get(link, "")
        return LINK_IN_COMMENTS_SHORT if _yt_is_short(title) else LINK_IN_COMMENTS
    return ""


def verify_fb_comment(ver, post_id, tok, comment_id, message):
    """Read the comments back off the live post and prove ours is really there.

    Returns True (confirmed on the post), False (published but NOT found — a human
    needs to look), or None (we could not check; treat as unproven, not as failed).
    """
    base = f"https://graph.facebook.com/{ver}"
    try:
        r = requests.get(f"{base}/{post_id}/comments",
                         params={"fields": "id,message", "limit": 50, "access_token": tok},
                         timeout=60)
        if not r.ok:
            return None
        for c in (r.json() or {}).get("data", []):
            if str(c.get("id")) == str(comment_id):
                return True
            if message and (message.strip() in (c.get("message") or "")):
                return True
        return False
    except Exception:
        return None


def ig_comment(ver, media_id, tok, message):
    """Comment on an Instagram post as the account.

    NOTE (told to David 2026-08-18): Instagram does NOT make links in comments
    clickable — people have to copy it. It still puts the exact song in front of
    them, which is why he asked for it on both platforms. Needs the
    instagram_manage_comments permission; if that is missing this raises and the
    run logs it as a warning rather than pretending it worked.
    """
    base = f"https://graph.facebook.com/{ver}"
    r = requests.post(f"{base}/{media_id}/comments",
                      data={"message": message, "access_token": tok}, timeout=120)
    graph_raise(r, "Instagram first comment")
    return r.json().get("id")


def verify_ig_comment(ver, media_id, tok, comment_id, message):
    base = f"https://graph.facebook.com/{ver}"
    try:
        r = requests.get(f"{base}/{media_id}/comments",
                         params={"fields": "id,text", "limit": 50, "access_token": tok},
                         timeout=60)
        if not r.ok:
            return None
        for c in (r.json() or {}).get("data", []):
            if str(c.get("id")) == str(comment_id):
                return True
            if message and (message.strip() in (c.get("text") or "")):
                return True
        return False
    except Exception:
        return None


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


# A URL in the post BODY is what kills reach: Facebook throttles any post
# carrying an off-Facebook link, and roughly 97% of views go to posts with no
# external link. The link belongs in the FIRST COMMENT — Meta's own guidance,
# and the bot already does that. David's rule, 2026-08-25: "the YouTube link is
# supposed to be the first comment after the post goes out. Hard stop."
#
# CAREFUL — this matches URL SHAPES, never the bare word "YouTube". The caption
# line the bot legitimately adds is "Full song on YouTube — link in the comments".
# A naive check for the substring "youtu" would match that and refuse every song
# reel, killing the exact feature this is meant to protect.
CAPTION_URL_RE = re.compile(
    r"https?://|\bwww\.[a-z0-9-]|\byoutu\.be/|\byoutube\.com/", re.I)


def caption_has_url(text):
    """Return the offending snippet if a caption carries a URL, else ''."""
    m = CAPTION_URL_RE.search(text or "")
    if not m:
        return ""
    start = max(0, m.start() - 20)
    return text[start:m.start() + 45].replace("\n", " ").strip()


def caption_gate(row):
    """Refuse to post a row with an empty caption, or one carrying a URL.

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
    hit = caption_has_url(cap)
    if hit:
        return ("caption contains a LINK — Facebook throttles any post with an "
                "off-site link in the body, so this would publish crippled. "
                "Found: " + hit + ". Take the URL out of the caption and put it "
                "in this row's `first_comment` cell instead; the bot will post it "
                "as the first comment, which is where it belongs.")
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


def verify_fb_tags(ver, post_id, tok, wanted_tags):
    """After posting, check whether Facebook actually kept the Page mentions.

    PROVEN 2026-08-17: it does not. A probe posting "@[110966485223915]" came
    back as plain text with message_tags = 0 — Facebook DELETES the mention
    silently. That is the "Page Mentioning" restriction; it stays that way until
    the app passes App Review. Instagram @handles are unaffected (Instagram
    auto-links them in the caption).

    So rather than pretend, the bot now checks and, when the tag was stripped,
    writes the post link into needs_tagging.txt so the tags can be added by hand
    in about twenty seconds. The moment App Review lands, tags start sticking and
    this stops firing on its own — nothing to undo.

    Returns True if the tags stuck, False if they were stripped, None if unknown.
    """
    if not wanted_tags or not post_id:
        return None
    base = f"https://graph.facebook.com/{ver}"
    for fields in ("message,message_tags", "caption,message_tags", "name,message_tags"):
        try:
            r = requests.get(f"{base}/{post_id}", params={"fields": fields, "access_token": tok}, timeout=60)
            if r.ok:
                return bool((r.json() or {}).get("message_tags"))
        except Exception:
            pass
    return None


def resolve_comment_link(row, cfg, env=None):
    """Decide the exact text to drop as the first comment on a post.

    A row's `first_comment` cell can say four different things:
      yt:Get On Up          -> LOOK THE SONG UP on the brand's YouTube channel and
                               comment the link to THAT SONG (David's rule, 2026-08-18).
      https://...           -> use that link exactly as written.
      none / - / no / skip  -> post NO comment on this one. (The evergreen quote
                               cards use this: they point at nothing on purpose.)
      (blank)               -> fall back to the brand's default_comment_link in
                               brands.csv — e.g. MotiveAF's store. DWR's is blank
                               on purpose, so a blank DWR row gets no comment.

    Returns (text, explanation). text is '' when nothing should be commented.
    """
    env = env or {}
    v = (row.get("first_comment") or "").strip()
    if v.lower() in ("none", "-", "no", "skip", "off"):
        return "", "first_comment says 'none' — no comment on this one, on purpose"
    if v.lower().startswith("yt:"):
        url, why = yt_lookup(v[3:], cfg, env)
        return url, why
    if v:
        return v, "using the first_comment cell exactly as written"
    d = (cfg.get("default_comment_link") or "").strip()
    if d:
        return d, "using the brand's default_comment_link from brands.csv"
    return "", "nothing to comment (blank first_comment, brand has no default link)"


# --- retry bookkeeping --------------------------------------------------------
# Two optional columns keep a retry safe. Older queues without them still work.
#   posted_to -> which platforms have ALREADY gone live for this row ("FB", "IG")
#   attempts  -> how many times we have tried
MAX_ATTEMPTS = 3


def done_platforms(row):
    return {p.strip().upper() for p in (row.get("posted_to") or "").split(",") if p.strip()}


def mark_done(row, platform):
    d = done_platforms(row); d.add(platform.upper())
    row["posted_to"] = ",".join(sorted(d))


def attempts_of(row):
    try:
        return int(str(row.get("attempts") or "0").strip() or 0)
    except ValueError:
        return 0


def bump_attempts(row):
    row["attempts"] = str(attempts_of(row) + 1)


# =============================================================================
# YTCHECK MODE  —  python post.py --ytcheck ["Song Title" ...]
# =============================================================================
# Proves the song lookup works BEFORE anything is published. With no arguments it
# walks content_queue.csv and resolves every `yt:` row, showing exactly which
# YouTube video each post would link to. It publishes nothing and comments nothing.
# =============================================================================

def ytcheck(songs=None):
    env = read_env()
    brands = read_brands()
    key = (env.get("YOUTUBE_API_KEY") or "").strip()
    log("YTCHECK — publishing nothing, just resolving song links.")
    log("Lookup method: " + ("YouTube Data API (full catalogue)" if key
                             else "channel RSS feed — last ~15 uploads only. "
                                  "Set the YOUTUBE_API_KEY secret to search everything."))
    bad = 0
    if songs:
        cfg = brands.get("dwr music") or {}
        for s in songs:
            url, why = yt_lookup(s, cfg, env)
            log(f"  {'✅' if url else '🔴'} {s!r} -> {url or 'NO MATCH'}   ({why})")
            bad += 0 if url else 1
    else:
        qp = HERE / "content_queue.csv"
        if not qp.exists():
            log("No content_queue.csv to check."); return
        with open(qp, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        hits = 0
        for row in rows:
            fc = (row.get("first_comment") or "").strip()
            if not fc.lower().startswith("yt:"):
                continue
            hits += 1
            cfg = brands.get((row.get("brand") or "").strip().lower()) or {}
            url, why = yt_lookup(fc[3:], cfg, env)
            log(f"  {'✅' if url else '🔴'} {row.get('date')}  {fc} -> {url or 'NO MATCH'}   ({why})")
            bad += 0 if url else 1
        if not hits:
            log("  (no rows in the queue are using yt: lookups right now)")
    if bad:
        log(f"🔴 {bad} song(s) did not resolve. Those rows would be REFUSED, not posted blind.")
        raise SystemExit(1)
    log("✅ Every song resolved.")


# =============================================================================
# DIAGNOSE MODE  —  python post.py --diagnose [YYYY-MM-DD]
# =============================================================================
# Answers "why did that row fail?" WITHOUT putting anything on the page.
#
# Facebook lets you create a Page photo with published=false. It runs the exact
# same validation as a real post — token, permissions, caption, image fetch —
# and returns the exact same error, but nothing appears on the Page. We create
# it unpublished, read the result, then delete it again.
#
# Built 2026-08-17 because Micah Spurlock's spotlight failed with a bare
# "400 Bad Request" and there was no way to find out why without posting.
# =============================================================================

def diagnose(target_date=None):
    env = read_env()
    brands = read_brands()
    ver = env.get("GRAPH_VERSION", GRAPH_VERSION_DEFAULT)
    gtok = env.get("META_TOKEN") or env.get("GLOBAL_TOKEN") or env.get("FB_TOKEN") or ""
    log(f"DIAGNOSE starting. Graph version {ver}. Nothing will be published.")
    if not gtok:
        log("  NO TOKEN. Set the META_TOKEN secret."); return

    with open(HERE / "content_queue.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    targets = [r for r in rows if (not target_date or r.get("date", "").strip() == target_date)]
    if target_date and not targets:
        log(f"  No row dated {target_date}."); return
    if not target_date:
        targets = [r for r in rows if (r.get("status") or "").strip().upper() == "FAILED"]
        log(f"  No date given — checking the {len(targets)} FAILED row(s).")

    for row in targets:
        cfg = brands.get((row.get("brand") or "").strip().lower())
        if not cfg:
            log(f"  [{row.get('date')}] brand not in brands.csv"); continue
        pid = (cfg.get("fb_page_id") or "").strip()
        base = f"https://graph.facebook.com/{ver}"

        # 1) Can we even get a Page token?
        page_tok = resolve_page_token(ver, pid, gtok)
        log(f"  [{row.get('date')}] page token: {'OK' if page_tok else 'COULD NOT RESOLVE — this alone breaks posting'}")
        tok = page_tok or gtok

        # 2) Is the image actually reachable, and is it really an image?
        media = (row.get("media_url") or "").strip()
        if media:
            try:
                h = requests.get(media, stream=True, timeout=60)
                log(f"  [{row.get('date')}] image: HTTP {h.status_code}, "
                    f"{h.headers.get('content-type')}, {h.headers.get('content-length')} bytes")
                h.close()
            except Exception as e:
                log(f"  [{row.get('date')}] image FETCH FAILED: {e}")

        # 3) The real thing, unpublished.
        cap = build_fb_caption(row.get("caption", ""), row.get("fb_page_tags", ""))
        log(f"  [{row.get('date')}] caption {len(cap)} chars / {len(cap.encode('utf-8'))} bytes")
        data = {"caption": cap, "url": media, "published": "false", "access_token": tok}
        try:
            r = requests.post(f"{base}/{pid}/photos", data=data, timeout=180)
            graph_raise(r, "UNPUBLISHED test post")
            new_id = (r.json() or {}).get("id")
            log(f"  [{row.get('date')}] ✅ ACCEPTED by Facebook (unpublished id {new_id}). "
                f"The row itself is fine — the earlier failure was transient.")
            if new_id:
                d = requests.delete(f"{base}/{new_id}", params={"access_token": tok}, timeout=60)
                log(f"  [{row.get('date')}] cleaned up the test photo: HTTP {d.status_code}")
        except Exception as e:
            log(f"  [{row.get('date')}] ❌ REJECTED — this is the real reason it failed:")
            log(f"        {e}")

    log("DIAGNOSE finished. Nothing was published.")


# =============================================================================
# TAG CHECK  —  python post.py --tagcheck
# =============================================================================
# Answers "why do the @ mentions work on some posts and not others?" with
# evidence instead of theory.
#
# It does three things, and publishes NOTHING:
#   1. Reads message_tags back off the posts we already made. A real mention
#      shows up there; text that Facebook stripped does not.
#   2. Creates an UNPUBLISHED test post containing @[PAGE_ID] and reads its
#      message_tags back, then deletes it. That is the definitive test of
#      whether the API is allowed to tag at all right now.
#   3. Lists what the token is actually permitted to do.
# =============================================================================

def tagcheck(post_ids=None):
    env = read_env()
    brands = read_brands()
    ver = env.get("GRAPH_VERSION", GRAPH_VERSION_DEFAULT)
    gtok = env.get("META_TOKEN") or ""
    if not gtok:
        log("NO TOKEN."); return
    cfg = brands.get("dwr music") or {}
    pid = (cfg.get("fb_page_id") or "").strip()
    base = f"https://graph.facebook.com/{ver}"
    tok = resolve_page_token(ver, pid, gtok) or gtok
    log("TAGCHECK — nothing will be published.")

    # 1) What does the token actually have?
    try:
        r = requests.get(f"{base}/me/permissions", params={"access_token": tok}, timeout=60)
        if r.ok:
            granted = [d.get("permission") for d in (r.json().get("data") or []) if d.get("status") == "granted"]
            log(f"  granted permissions: {', '.join(sorted(granted)) or '(none reported)'}")
    except Exception as e:
        log(f"  permissions lookup failed: {e}")

    # 2) Existing posts — do they carry real tags?
    for post_id in (post_ids or []):
        try:
            r = requests.get(f"{base}/{post_id}",
                             params={"fields": "id,message,message_tags,created_time,is_eligible_for_promotion",
                                     "access_token": tok}, timeout=60)
            if not r.ok:
                log(f"  [{post_id}] could not read: HTTP {r.status_code} {r.text[:160]}"); continue
            j = r.json() or {}
            tags = j.get("message_tags") or []
            msg = (j.get("message") or "").replace("\n", " ")
            log(f"  [{post_id}] tags={len(tags)}  " +
                (", ".join(f"{t.get('name')}({t.get('type')})" for t in tags) if tags else "NO TAGS"))
            log(f"      text ends: ...{msg[-70:]}")
        except Exception as e:
            log(f"  [{post_id}] error: {e}")

    # 3) The decisive test: does an API tag register at all, right now?
    venue = "110966485223915"
    probe = f"TAG TEST please ignore @[{venue}]"
    try:
        r = requests.post(f"{base}/{pid}/feed",
                          data={"message": probe, "published": "false", "access_token": tok}, timeout=120)
        if not r.ok:
            log(f"  UNPUBLISHED tag probe rejected: HTTP {r.status_code} {r.text[:220]}")
        else:
            new_id = (r.json() or {}).get("id")
            g = requests.get(f"{base}/{new_id}",
                             params={"fields": "message,message_tags", "access_token": tok}, timeout=60)
            jj = g.json() if g.ok else {}
            tags = jj.get("message_tags") or []
            log(f"  UNPUBLISHED tag probe -> message_tags={len(tags)} :: {jj.get('message')!r}")
            if tags:
                log("      ==> the API CAN tag Pages. The @[id] syntax is being accepted.")
            else:
                log("      ==> the API CANNOT tag Pages. Facebook stripped @[id] silently.")
                log("          This is the Page Mentioning restriction — it needs App Review.")
            if new_id:
                d = requests.delete(f"{base}/{new_id}", params={"access_token": tok}, timeout=60)
                log(f"      cleaned up the test post: HTTP {d.status_code}")
    except Exception as e:
        log(f"  tag probe error: {e}")
    log("TAGCHECK finished. Nothing was published.")


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
    for extra_col in ("first_comment", "posted_to", "attempts"):
        if extra_col not in fieldnames:
            fieldnames.insert(len(fieldnames) - 1, extra_col)   # keep `status` last
            for r_ in rows:
                r_.setdefault(extra_col, "")
    today = now_ct().date()
    changed = 0
    failures = []          # anything in here at the end makes this run exit RED
    needs_tagging = []     # posts that went out but had their @ mentions stripped
    needs_comment = []     # posts that went out but whose first comment is missing/unproven
    for row in rows:
        st = (row.get("status") or "").strip().upper()
        if st in ("POSTED", "SKIPPED", "DRAFT", "HOLD"):
            continue  # these are finished or deliberately parked
        if st == "REFUSED":
            continue  # a safety gate said no; a human must fix the row first
        if st == "FAILED":
            # A FAILED row used to sit there dead for ever. That is how Micah
            # Spurlock's 2026-08-17 spotlight quietly never went out. Now a
            # failure is retried on the next run, up to MAX_ATTEMPTS, and the
            # `posted_to` column guarantees we never re-post to a platform that
            # already succeeded.
            if attempts_of(row) >= MAX_ATTEMPTS:
                continue
            log(f"RETRY [{row.get('brand')}] {row.get('date')}: attempt "
                f"{attempts_of(row) + 1} of {MAX_ATTEMPTS} (already live on: {'+'.join(sorted(done_platforms(row))) or 'nothing yet'})")
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
            failures.append(f"{row.get('brand')} {due} — REFUSED: {stop}")
            continue
        if (cap or "").strip().lower() in BLANK_OK:
            cap = ""       # `none` means deliberately wordless

        # Optional tagging columns (absent in older queues -> treated as blank):
        # ---- FIRST COMMENT is worked out BEFORE the caption, because a post only
        # earns the "link in the comments" line if a comment is really going to fire.
        comment_link, comment_why = resolve_comment_link(row, cfg, env)
        asked_for_song = (row.get("first_comment") or "").strip().lower().startswith("yt:")
        if asked_for_song and not comment_link:
            # The row asked for a specific song and we could not find it. Publishing a
            # song reel with no song link is a half-done job, so nothing goes out —
            # the row is fixable and will be retried.
            row["status"] = "FAILED"; bump_attempts(row); changed += 1
            log(f"REFUSED [{row.get('brand')}] {due}: {comment_why}")
            log("         Nothing was published. Fix first_comment (or paste the full YouTube URL) and re-run.")
            failures.append(f"{row.get('brand')} {due} — song link not found, post held back: {comment_why}")
            continue
        if comment_link:
            log(f"  first comment for {due}: {comment_link}   ({comment_why})")
            line = comment_caption_line(comment_link, cfg)
            if line:
                cap = add_link_line(cap, line)
        fb_cap = build_fb_caption(cap, row.get("fb_page_tags", ""))   # clickable Page-mentions
        ig_cap = build_ig_caption(cap, row.get("ig_mentions", ""))    # auto-linking @handles

        # LAST LINE OF DEFENCE. caption_gate() checked the row as written; this
        # checks the text genuinely about to hit Meta, after the link line, the
        # Page-mentions and the @handles have been folded in. If a URL reached
        # the body by any route, nothing publishes and the run goes red — a
        # throttled post cannot be un-throttled by editing it afterwards.
        leak = caption_has_url(fb_cap) or caption_has_url(ig_cap)
        if leak:
            row["status"] = "FAILED"; changed += 1
            log(f"REFUSED [{row.get('brand')}] {due}: a LINK reached the finished "
                f"caption -> {leak}. Nothing was published.")
            log("  The link belongs in the first comment. Check this row's caption "
                "and the brand's comment_caption_line in brands.csv.")
            failures.append(f"{row.get('brand')} {due} — REFUSED: link in the post body ({leak})")
            continue
        res = []
        done = done_platforms(row)          # platforms that already succeeded on an earlier attempt
        try:
            if plat in ("facebook", "fb", "both") and cfg.get("fb_page_id") and "FB" not in done:
                fb_id = fb_post(ver, cfg["fb_page_id"].strip(), post_tok, fb_cap, media)
                res.append("FB:" + str(fb_id))
                mark_done(row, "FB")        # recorded IMMEDIATELY, so a later IG failure can never double-post this
                wanted = _split_tokens(row.get("fb_page_tags", ""))
                if wanted:
                    stuck = verify_fb_tags(ver, fb_id, post_tok, wanted)
                    if stuck is False:
                        link = f"https://www.facebook.com/{cfg['fb_page_id'].strip()}/posts/{str(fb_id).split('_')[-1]}"
                        log(f"  TAGS STRIPPED by Facebook on {due} — add them by hand: {link}")
                        needs_tagging.append(f"{due}  {row.get('caption','')[:45]}...  {link}")
                    elif stuck:
                        log(f"  tags stuck on {due} — Page Mentioning is working now.")
                if comment_link and fb_id:
                    # Link-in-first-comment (Facebook throttles links in the body),
                    # then READ IT BACK. A comment we never confirmed is a comment we
                    # are not allowed to say we made.
                    fb_link = f"https://www.facebook.com/{cfg['fb_page_id'].strip()}/posts/{str(fb_id).split('_')[-1]}"
                    try:
                        cid = fb_comment(ver, fb_id, post_tok, comment_link)
                        res.append("cmt:" + str(cid))
                        ok = verify_fb_comment(ver, fb_id, post_tok, cid, comment_link)
                        if ok is True:
                            log(f"  first comment CONFIRMED on the live post: {comment_link}")
                        elif ok is False:
                            log(f"  FIRST COMMENT MISSING on {due} — add it by hand: {fb_link}")
                            needs_comment.append(f"{due}  FB  {comment_link}  ->  {fb_link}")
                            failures.append(f"{row.get('brand')} {due} — post is live but its first comment is NOT on it: {comment_link}")
                        else:
                            log(f"  first comment posted but could not be read back to confirm: {fb_link}")
                            needs_comment.append(f"{due}  FB  (unconfirmed) {comment_link}  ->  {fb_link}")
                    except Exception as ce:
                        log(f"  FIRST COMMENT FAILED on {due} (the post itself is fine): {ce}")
                        needs_comment.append(f"{due}  FB  {comment_link}  ->  {fb_link}")
                        failures.append(f"{row.get('brand')} {due} — first comment failed: {ce}")
            if plat in ("instagram", "ig", "both") and "IG" not in done:
                igid = (cfg.get("ig_user_id") or "").strip() or (resolve_ig(ver, cfg.get("fb_page_id","").strip(), post_tok) if cfg.get("fb_page_id") else "")
                if igid:
                    ig_media_id = ig_post(ver, igid, post_tok, ig_cap, media)
                    res.append("IG:" + str(ig_media_id))
                    mark_done(row, "IG")
                    if comment_link:
                        # David asked for the song link on BOTH platforms. Instagram does
                        # not make comment links clickable, but it does put the exact song
                        # in front of people. A failure here is a warning, not a red run —
                        # Facebook is where the clickable one lives.
                        try:
                            icid = ig_comment(ver, ig_media_id, post_tok, comment_link)
                            res.append("igcmt:" + str(icid))
                            iok = verify_ig_comment(ver, ig_media_id, post_tok, icid, comment_link)
                            if iok is True:
                                log("  first comment confirmed on Instagram too.")
                            else:
                                log("  Instagram comment posted but could not be confirmed.")
                                needs_comment.append(f"{due}  IG  (unconfirmed) {comment_link}")
                        except Exception as ie:
                            log(f"  Instagram first comment failed (the post itself is fine): {ie}")
                            needs_comment.append(f"{due}  IG  {comment_link}")
            row["status"] = "POSTED"; changed += 1
            log(f"POSTED [{row.get('brand')}] {due} {plat} -> {', '.join(res) or '(nothing matched platform)'}")
        except Exception as e:
            row["status"] = "FAILED"
            bump_attempts(row)
            changed += 1
            done_now = done_platforms(row)
            extra = f" (already live on {'+'.join(sorted(done_now))} — will NOT be re-posted)" if done_now else ""
            log(f"FAILED [{row.get('brand')}] {due} {plat} attempt {attempts_of(row)}/{MAX_ATTEMPTS}{extra}: {e}")
            failures.append(f"{row.get('brand')} {due} {plat} (attempt {attempts_of(row)}/{MAX_ATTEMPTS}) — {e}")
            if attempts_of(row) >= MAX_ATTEMPTS:
                log(f"  ^ giving up on this row after {MAX_ATTEMPTS} attempts. Fix it and set status back to QUEUED to try again.")
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

    # ---- POSTS THAT NEED HAND-TAGGING ---------------------------------------
    tag_file = HERE / "needs_tagging.txt"
    if needs_tagging:
        log("")
        log(f"@ MENTIONS: {len(needs_tagging)} post(s) went out with the tags stripped by Facebook.")
        log("   Facebook removes API Page-mentions until the app passes App Review.")
        log("   Open each link and tag by hand (about 20 seconds each):")
        for n in needs_tagging:
            log(f"   - {n}")
        with open(tag_file, "w", encoding="utf-8") as fh:
            fh.write("Posts that went out but had their @ mentions stripped by Facebook.\n")
            fh.write("Open each and add the tags by hand. This file is rewritten every run.\n\n")
            fh.write("\n".join(needs_tagging) + "\n")
    elif tag_file.exists():
        tag_file.unlink()

    # ---- POSTS WHOSE FIRST COMMENT IS MISSING -------------------------------
    cmt_file = HERE / "needs_comment.txt"
    if needs_comment:
        log("")
        log(f"FIRST COMMENTS: {len(needs_comment)} post(s) went out without a confirmed comment.")
        log("   The post is live and fine — only the link in the comments is missing.")
        for n in needs_comment:
            log(f"   - {n}")
        with open(cmt_file, "w", encoding="utf-8") as fh:
            fh.write("Posts that published but whose first comment is missing or unconfirmed.\n")
            fh.write("Open each post, paste the link as a comment. Rewritten every run.\n\n")
            fh.write("\n".join(needs_comment) + "\n")
    elif cmt_file.exists():
        cmt_file.unlink()

    # ---- LOUD FAILURE -------------------------------------------------------
    # This run used to finish GREEN even when a post had failed, which is exactly
    # how Micah Spurlock's 2026-08-17 spotlight was lost without anyone being told.
    # A failed post now fails the whole run, so GitHub turns it RED and emails.
    if failures:
        log("")
        log("=" * 68)
        log(f"!! {len(failures)} POST(S) FAILED THIS RUN — this run is being marked FAILED on purpose")
        for f in failures:
            log(f"   - {f}")
        log("=" * 68)
        with open(HERE / "last_failure.txt", "w", encoding="utf-8") as fh:
            fh.write("\n".join(failures))
        raise SystemExit(1)


if __name__ == "__main__":
    import sys
    if "--ytcheck" in sys.argv:
        i = sys.argv.index("--ytcheck")
        ytcheck(sys.argv[i + 1:])
    elif "--tagcheck" in sys.argv:
        i = sys.argv.index("--tagcheck")
        tagcheck(sys.argv[i + 1:])
    elif "--diagnose" in sys.argv:
        i = sys.argv.index("--diagnose")
        date_arg = sys.argv[i + 1] if len(sys.argv) > i + 1 else None
        diagnose(date_arg)
    else:
        main()
