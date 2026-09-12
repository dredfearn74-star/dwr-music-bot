#!/usr/bin/env python3
"""
capture_comments.py — read the comments on a day's posts and keep a leads list.

WHY: the 12 Sep 2026 MotiveAF reel ends "want in? drop a comment below and I'll come
find you." Every person who comments is a lead for the Uptick CRM — the first lead
list it has ever had. Nobody should be reading Facebook by hand to collect them.

WHAT IT DOES (read-only against Meta — it never posts, never replies, never likes):
  1. Finds the posts published on POST_DATE (default 2026-09-12) for BRAND (default
     MotiveAF) by reading the bot's own autopublish_log.txt lines:
        POSTED [MotiveAF] 2026-09-12 both -> FB:<id>, ..., IG:<id>, ...
  2. Pulls every comment on each of those posts (Facebook page post + Instagram media),
     with replies, via the Graph API using the same META_TOKEN the bot posts with.
  3. Merges them into leads/leads_<POST_DATE>.csv (one row per comment, keyed by
     comment id, so re-running only ADDS new comments — nothing is lost or doubled).
  4. Prints a short summary and exits 0. Exits 1 only if it could not read Meta at all,
     so a red run means "the capture did not happen", never "no one commented".

COLUMNS: captured_at, platform, post_id, comment_id, name, username, said, when, is_reply, parent_id
"""
import csv, os, re, sys, datetime, pathlib
import requests

HERE = pathlib.Path(__file__).resolve().parent
GRAPH_VERSION = os.environ.get("GRAPH_VERSION", "v23.0")
BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"
POST_DATE = (os.environ.get("POST_DATE") or "2026-09-12").strip()
BRAND = (os.environ.get("BRAND") or "MotiveAF").strip()
TOKEN = (os.environ.get("META_TOKEN") or "").strip()
OUT = HERE / "leads" / f"leads_{POST_DATE}.csv"
FIELDS = ["captured_at", "platform", "post_id", "comment_id", "name", "username", "said", "when", "is_reply", "parent_id"]


def die(msg):
    print("!! " + msg, flush=True)
    sys.exit(1)


def read_brands():
    with open(HERE / "brands.csv", encoding="utf-8") as fh:
        return {r["brand"].strip().lower(): r for r in csv.DictReader(fh)}


def page_token(pid, tok):
    r = requests.get(f"{BASE}/{pid}", params={"fields": "access_token", "access_token": tok}, timeout=60)
    if not r.ok:
        die(f"could not get the Page token for {pid}: HTTP {r.status_code} {r.text[:200]}")
    return (r.json() or {}).get("access_token") or tok


def find_posts():
    """Return [(platform, post_id)] for BRAND on POST_DATE from autopublish_log.txt."""
    found = []
    pat = re.compile(rf"POSTED \[{re.escape(BRAND)}\] {re.escape(POST_DATE)} \S+ -> (.*)$")
    for line in (HERE / "autopublish_log.txt").read_text(encoding="utf-8", errors="replace").splitlines():
        m = pat.search(line)
        if not m:
            continue
        for part in m.group(1).split(","):
            part = part.strip()
            if part.startswith("FB:"):
                found.append(("facebook", part[3:].strip()))
            elif part.startswith("IG:"):
                found.append(("instagram", part[3:].strip()))
    # dedupe, keep order
    seen, out = set(), []
    for p in found:
        if p not in seen:
            seen.add(p); out.append(p)
    return out


def paged(url, params):
    """Follow Graph paging; yield every item."""
    while url:
        r = requests.get(url, params=params, timeout=60)
        if not r.ok:
            die(f"Graph read failed: {url} -> HTTP {r.status_code} {r.text[:300]}")
        j = r.json() or {}
        for item in j.get("data", []):
            yield item
        url = ((j.get("paging") or {}).get("next")) or None
        params = None  # the `next` URL already carries them


def fb_comments(post_id, tok):
    rows = []
    for c in paged(f"{BASE}/{post_id}/comments",
                   {"fields": "id,message,created_time,from{name,id}", "filter": "stream", "limit": 100,
                    "access_token": tok}):
        frm = c.get("from") or {}
        parent = (c.get("parent") or {}).get("id", "")
        rows.append({"platform": "facebook", "post_id": post_id, "comment_id": c.get("id", ""),
                     "name": frm.get("name", ""), "username": frm.get("id", ""),
                     "said": (c.get("message") or "").replace("\n", " ").strip(),
                     "when": c.get("created_time", ""), "is_reply": "yes" if parent else "",
                     "parent_id": parent})
    return rows


def ig_comments(media_id, tok):
    rows = []
    for c in paged(f"{BASE}/{media_id}/comments",
                   {"fields": "id,text,timestamp,username,from{username,id},replies{id,text,timestamp,username}",
                    "limit": 50, "access_token": tok}):
        rows.append({"platform": "instagram", "post_id": media_id, "comment_id": c.get("id", ""),
                     "name": "", "username": c.get("username") or (c.get("from") or {}).get("username", ""),
                     "said": (c.get("text") or "").replace("\n", " ").strip(),
                     "when": c.get("timestamp", ""), "is_reply": "", "parent_id": ""})
        for rp in ((c.get("replies") or {}).get("data") or []):
            rows.append({"platform": "instagram", "post_id": media_id, "comment_id": rp.get("id", ""),
                         "name": "", "username": rp.get("username", ""),
                         "said": (rp.get("text") or "").replace("\n", " ").strip(),
                         "when": rp.get("timestamp", ""), "is_reply": "yes", "parent_id": c.get("id", "")})
    return rows


def main():
    if not TOKEN:
        die("META_TOKEN is not set.")
    brands = read_brands()
    b = brands.get(BRAND.lower())
    if not b:
        die(f"brand {BRAND!r} is not in brands.csv")
    posts = find_posts()
    if not posts:
        print(f"No POSTED line for [{BRAND}] {POST_DATE} in autopublish_log.txt yet — nothing to read. "
              f"(That is normal before the post goes out.)")
        return 0
    ptok = page_token(b["fb_page_id"].strip(), TOKEN)

    existing = {}
    if OUT.exists():
        with open(OUT, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                existing[r["comment_id"]] = r

    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    new = 0
    for plat, pid in posts:
        rows = fb_comments(pid, ptok) if plat == "facebook" else ig_comments(pid, ptok)
        print(f"{plat} post {pid}: {len(rows)} comment(s) on the live post")
        for r in rows:
            if r["comment_id"] and r["comment_id"] not in existing:
                r["captured_at"] = now
                existing[r["comment_id"]] = r
                new += 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in sorted(existing.values(), key=lambda x: (x.get("when") or "")):
            w.writerow({k: r.get(k, "") for k in FIELDS})
    # never trust a save you haven't read back
    with open(tmp, encoding="utf-8") as fh:
        n = sum(1 for _ in csv.DictReader(fh))
    if n != len(existing):
        die(f"read-back mismatch: wrote {len(existing)} rows, read {n}")
    tmp.replace(OUT)
    print(f"LEADS FILE: {OUT.relative_to(HERE)} — {len(existing)} comment(s) total, {new} new this run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
