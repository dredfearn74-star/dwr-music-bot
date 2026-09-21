#!/usr/bin/env python3
"""
Put the missing first comments on posts that already went live.

WHY THIS EXISTS
---------------
2026-09-21. The new never-expiring system-user token could post but not comment:
the Content Scheduler system user held only the Page's "Content" task, and
commenting as the Page needs "Community activity". Facebook answered every
comment attempt with (#200) Cannot access object_id. Three posts went live
carrying no link — the MotiveAF shirts link and the Save Tonight pre-save link.

post.py only tries the first comment at the moment it publishes a row. Once a
row is POSTED it never looks at it again, by design — that is what stops
double-posting. So a permission fixed AFTER the fact leaves those posts bare
for ever unless something goes back for them. This is that something.

WHAT IT DOES
------------
Reads needs_comment.txt — the file post.py already writes every run, listing
every post that published without a confirmed comment — and posts each missing
comment. Then it re-reads the live post to prove the comment is actually there,
and rewrites needs_comment.txt with only the ones still outstanding.

It publishes ONLY comments, never a post, and only for posts that are already
live. Run it any time; it is safe to re-run.
"""

import os
import re
import sys
import time
import datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
NEEDS = HERE / "needs_comment.txt"
LOG = HERE / "autopublish_log.txt"
VER = os.environ.get("GRAPH_VERSION", "v23.0")
TOKEN = os.environ.get("META_TOKEN", "")

# "2026-09-21  FB  Shirts: https://...  ->  https://www.facebook.com/<page>/posts/<post>"
LINE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<plat>FB|IG)\s+(?P<msg>.+?)\s+->\s+(?P<url>https?://\S+)\s*$"
)
FB_POST_URL = re.compile(r"facebook\.com/(?P<page>\d+)/posts/(?P<post>\d+)")


def log(m):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] FIXCOMMENTS {m}"
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def graph_error(r):
    """Pull Facebook's real complaint out of the body. 'HTTP 403' on its own
    cost a full day of guessing back in August — never report the bare code."""
    try:
        e = r.json().get("error", {})
        bits = [e.get("message", "")]
        for k in ("type", "code", "error_subcode", "fbtrace_id"):
            if e.get(k) is not None:
                bits.append(f"{k}={e[k]}")
        return f"HTTP {r.status_code}: " + " | ".join(b for b in bits if b)
    except Exception:
        return f"HTTP {r.status_code}: {r.text[:200]}"


def page_token(page_id):
    """META_TOKEN is a SYSTEM USER token, not a Page token. Every Page has its
    own token that has to be looked up at call time — that is how one secret
    serves all three brands. Never put a Page token in META_TOKEN."""
    r = requests.get(
        f"https://graph.facebook.com/{VER}/me/accounts",
        params={"access_token": TOKEN, "fields": "id,name,access_token", "limit": 100},
        timeout=60,
    )
    if not r.ok:
        raise SystemExit(f"could not list Pages: {graph_error(r)}")
    for p in r.json().get("data", []):
        if str(p.get("id")) == str(page_id):
            return p.get("access_token"), p.get("name", page_id)
    raise SystemExit(f"Page {page_id} is not assigned to this token's system user.")


def already_there(obj_id, tok, message):
    """Never trust a write you have not read back."""
    r = requests.get(
        f"https://graph.facebook.com/{VER}/{obj_id}/comments",
        params={"access_token": tok, "fields": "message", "limit": 50},
        timeout=60,
    )
    if not r.ok:
        return False
    needle = message.strip()[:40]
    return any(needle in (c.get("message") or "") for c in r.json().get("data", []))


def main():
    if not TOKEN:
        raise SystemExit("META_TOKEN is not set.")
    if not NEEDS.exists():
        log("needs_comment.txt is not there — nothing is missing a comment.")
        return 0

    rows, skipped = [], []
    for raw in NEEDS.read_text(encoding="utf-8").splitlines():
        m = LINE.match(raw.strip())
        if m:
            rows.append(m.groupdict())

    if not rows:
        log("no outstanding comments listed.")
        return 0

    still_missing, fixed, failed = [], 0, 0

    for row in rows:
        if row["plat"] != "FB":
            # Instagram comment links are not clickable — IG uses link-in-bio,
            # so a missing IG comment is not worth a permission or a retry.
            skipped.append(row)
            continue

        u = FB_POST_URL.search(row["url"])
        if not u:
            log(f"  can't read a page/post id out of {row['url']} — leaving it")
            still_missing.append(row)
            continue

        obj = f"{u.group('page')}_{u.group('post')}"
        try:
            tok, name = page_token(u.group("page"))
        except SystemExit as e:
            log(f"  {e}")
            still_missing.append(row)
            failed += 1
            continue

        if already_there(obj, tok, row["msg"]):
            log(f"  [{name}] {row['date']} — comment is already on the post, nothing to do")
            fixed += 1
            continue

        r = requests.post(
            f"https://graph.facebook.com/{VER}/{obj}/comments",
            data={"message": row["msg"], "access_token": tok},
            timeout=120,
        )
        if not r.ok:
            log(f"  [{name}] {row['date']} FAILED: {graph_error(r)}")
            still_missing.append(row)
            failed += 1
            continue

        time.sleep(3)
        if already_there(obj, tok, row["msg"]):
            log(f"  [{name}] {row['date']} — comment posted and CONFIRMED on the live post: {row['msg'][:60]}")
            fixed += 1
        else:
            log(f"  [{name}] {row['date']} — Facebook accepted the comment but it did not read back. Leaving it listed.")
            still_missing.append(row)
            failed += 1

    header = (
        "Posts that published but whose first comment is missing or unconfirmed.\n"
        "Open each post, paste the link as a comment. Rewritten every run.\n\n"
    )
    if still_missing or skipped:
        keep = still_missing + skipped
        NEEDS.write_text(
            header + "\n".join(f"{r['date']}  {r['plat']}  {r['msg']}  ->  {r['url']}" for r in keep) + "\n",
            encoding="utf-8",
        )
    else:
        NEEDS.unlink(missing_ok=True)

    log(f"done — {fixed} comment(s) now confirmed live, {failed} still missing, "
        f"{len(skipped)} Instagram row(s) left alone on purpose.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
