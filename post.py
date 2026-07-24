"""
DWR MUSIC — Auto-Publisher (Facebook + Instagram)
=================================================
Posts David Wayne Redfearn Music content — images AND video reels — to the
DWR Music Facebook Page and Instagram, on a schedule, hands-free.

Three files run the whole thing:
  - brands.csv          -> account directory (FB Page ID + Instagram ID for DWR Music)
  - content_queue.csv   -> the post list; one row per post, each tagged with a date
  - META_TOKEN (secret) -> a long-lived Meta token for the DWR Music page (a GitHub Actions secret)

Each run: for every QUEUED row whose date is today (US Central) or earlier, it posts to
Facebook and/or Instagram, then marks the row POSTED. A .mp4/.mov/.m4v media file is posted
as a reel/video; anything else is posted as a photo. A row is skipped safely if the
brand/account/token is missing, so it never posts to the wrong place.

Nothing here ever asks for a password. Runs on GitHub Actions (Facebook's API is not
reachable from the Cowork sandbox, so the bot lives on GitHub).
"""
import csv
import datetime
import os
import pathlib
import time

import requests

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Chicago")
except Exception:
    TZ = None

HERE = pathlib.Path(__file__).parent
GRAPH_VERSION_DEFAULT = "v21.0"


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
    for r in csv.DictReader(open(p, encoding="utf-8")):
        d[r["brand"].strip().lower()] = r
    return d


def log(m):
    line = f"[{now_ct():%Y-%m-%d %H:%M:%S}] {m}"
    print(line, flush=True)
    with open(HERE / "autopublish_log.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def is_video(url):
    return url.lower().split("?")[0].endswith((".mp4", ".mov", ".m4v"))


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


def main():
    env = read_env()
    brands = read_brands()
    ver = env.get("GRAPH_VERSION", GRAPH_VERSION_DEFAULT)
    gtok = env.get("META_TOKEN") or env.get("GLOBAL_TOKEN") or env.get("FB_TOKEN") or ""
    qp = HERE / "content_queue.csv"
    if not qp.exists():
        log("No content_queue.csv found. Nothing to do.")
        return
    rows = list(csv.DictReader(open(qp, encoding="utf-8")))
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
        plat = (row.get("platform") or "both").strip().lower()
        cap = row.get("caption", "")
        media = (row.get("media_url") or "").strip()
        res = []
        try:
            if plat in ("facebook", "fb", "both") and cfg.get("fb_page_id"):
                res.append("FB:" + str(fb_post(ver, cfg["fb_page_id"].strip(), tok, cap, media)))
            if plat in ("instagram", "ig", "both") and cfg.get("ig_user_id"):
                res.append("IG:" + str(ig_post(ver, cfg["ig_user_id"].strip(), tok, cap, media)))
            row["status"] = "POSTED"; changed += 1
            log(f"POSTED [{row.get('brand')}] {due} {plat} -> {', '.join(res) or '(nothing matched platform)'}")
        except Exception as e:
            row["status"] = "FAILED"; changed += 1
            log(f"FAILED [{row.get('brand')}] {due} {plat}: {e}")
    if changed:
        w = csv.DictWriter(open(qp, "w", newline="", encoding="utf-8"), fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
        log(f"Queue updated ({changed} row(s) changed).")
    else:
        log("Nothing due today. Exiting cleanly.")


if __name__ == "__main__":
    main()
