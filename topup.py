#!/usr/bin/env python3
"""
MotiveAF daily top-up — the floor under the posting schedule.

WHY THIS EXISTS
---------------
David's rule, 2026-09-20: "there needs to be a daily posting on MotiveAF no
matter what." The queue is finite. It runs to 2026-11-10 today and then stops,
and any day a row fails or nobody refills it, MotiveAF goes silent. Silence is
what we are removing.

WHAT IT DOES
------------
Runs 30 minutes BEFORE the daily poster. If MotiveAF has no QUEUED row dated
today, it appends one by recycling a meme that has ALREADY posted successfully
once — same image, same caption, same first comment. Then post.py runs and
publishes it through the identical, proven path.

WHY RECYCLING POSTED ROWS AND NOT RAW IMAGES
--------------------------------------------
A row that reached POSTED has a caption that passed caption_gate(), a first
comment that resolved, and an image Facebook and Instagram both accepted. It
cannot introduce a new failure mode. Writing fresh captions for the ~86 unused
images in media/ is a content job, not a reliability job — that stays David's.

WHAT IT WILL NEVER RECYCLE
--------------------------
  * Video. David's words: "all of the posts that we've got so far from MotiveAF
    that are NOT my videos" — his own face and voice are not filler. Anything
    ending .mp4/.mov/.m4v is excluded outright.
  * A build-in-public post. Those name a real week, a real email, a real gig.
    Reposting one six weeks later is a lie about what happened. Detected by
    DATED_MARKERS below.
  * Anything posted or recycled inside COOLDOWN_DAYS.
  * Any other brand. MotiveAF only.

It never edits, deletes or reorders an existing row. It only ever appends.
Delete this file and its workflow and the bot behaves exactly as it did before.
"""

import csv
import datetime
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
QUEUE = HERE / "content_queue.csv"
LOG = HERE / "autopublish_log.txt"

BRAND = "MotiveAF"
COOLDOWN_DAYS = 45
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".avi", ".webm")

# Phrases that make a caption a record of a specific week. Never recycled.
DATED_MARKERS = (
    "this week", "last week", "yesterday", "today i", "tonight",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "sent an email", "got told", "spent half a day", "my buddy",
)


def log(msg):
    line = f"[{datetime.datetime.now(ZoneInfo('America/Chicago')):%Y-%m-%d %H:%M:%S}] TOPUP {msg}"
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def is_video(url):
    return url.strip().lower().split("?")[0].endswith(VIDEO_EXT)


def is_dated(caption):
    low = " " + re.sub(r"\s+", " ", caption.lower()) + " "
    return any(m in low for m in DATED_MARKERS)


def main():
    if not QUEUE.exists():
        log("no content_queue.csv — nothing to do.")
        return 0

    with open(QUEUE, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        log("queue empty — nothing to do.")
        return 0
    fieldnames = list(rows[0].keys())

    today = datetime.datetime.now(ZoneInfo("America/Chicago")).date()

    def date_of(r):
        try:
            return datetime.date.fromisoformat((r.get("date") or "").strip())
        except ValueError:
            return None

    # 1. Is MotiveAF already covered today? Anything not yet given up on counts.
    for r in rows:
        if (r.get("brand") or "").strip() != BRAND:
            continue
        d = date_of(r)
        if d != today:
            continue
        st = (r.get("status") or "").strip().upper()
        if st in ("QUEUED", "", "POSTED"):
            log(f"{BRAND} already has a row for {today} (status {st or 'blank'}). Standing down.")
            return 0
        if st == "FAILED" and int((r.get("attempts") or "0").strip() or 0) < 3:
            log(f"{BRAND} has a FAILED row for {today} still inside its retries. Standing down.")
            return 0

    # 2. Build the recycle pool: MotiveAF stills that actually published.
    pool = []
    for r in rows:
        if (r.get("brand") or "").strip() != BRAND:
            continue
        if (r.get("status") or "").strip().upper() != "POSTED":
            continue
        media = (r.get("media_url") or "").strip()
        if not media or is_video(media):
            continue
        if is_dated(r.get("caption") or ""):
            continue
        pool.append(r)

    if not pool:
        log("NO RECYCLABLE POSTS. Pool is empty — MotiveAF will be silent today. "
            "This needs a human: add rows to content_queue.csv.")
        return 1  # go red; silence must never be quiet

    # 3. Least-recently-used wins, so the rotation is even.
    def last_used(r):
        d = date_of(r)
        return d or datetime.date(2000, 1, 1)

    pool.sort(key=last_used)
    oldest = last_used(pool[0])
    if (today - oldest).days < COOLDOWN_DAYS:
        log(f"every candidate was used within {COOLDOWN_DAYS} days "
            f"(oldest: {oldest}). Not repeating myself — standing down.")
        return 0

    src = pool[0]
    new = {k: "" for k in fieldnames}
    new.update({
        "brand": BRAND,
        "date": today.isoformat(),
        "platform": (src.get("platform") or "both").strip() or "both",
        "caption": src.get("caption") or "",
        "fb_page_tags": src.get("fb_page_tags") or "",
        "ig_mentions": src.get("ig_mentions") or "",
        "media_url": src.get("media_url") or "",
        "first_comment": src.get("first_comment") or "",
        "ig_location_id": src.get("ig_location_id") or "",
        "format": src.get("format") or "",
        "posted_to": "",
        "attempts": "0",
        "status": "QUEUED",
    })

    rows.append(new)

    # Write to temp, read it back, count it, THEN swap — same discipline post.py
    # uses. A half-written queue is worse than a missed post.
    tmp = QUEUE.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    with open(tmp, encoding="utf-8-sig") as fh:
        written = list(csv.DictReader(fh))
    if len(written) != len(rows):
        tmp.unlink(missing_ok=True)
        log(f"ABORTED: wrote {len(written)} rows, expected {len(rows)}. Queue untouched.")
        return 1
    os.replace(tmp, QUEUE)

    name = new["media_url"].rsplit("/", 1)[-1]
    log(f"queue was dry for {today} — recycled {name} "
        f"(first ran {src.get('date','?')}, {(today - oldest).days} days ago). "
        f"{len(pool)} posts in the rotation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
