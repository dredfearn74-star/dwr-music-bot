#!/usr/bin/env python3
"""pubcheck.py — the LOGGED-OUT Facebook visibility check (added 2026-09-28).

WHY: for a month every bot post on Facebook was invisible to the public because
it went through a Meta app stuck in development mode. The bot reported green
every day, because it checked the post with the SAME admin token that could see
it. Nobody looked from the outside. This script looks from the outside.

HOW: Facebook's Page Plugin (the embeddable timeline widget) renders a page's
PUBLIC posts to anyone, no login, no token. We fetch it for every brand and
confirm that each Facebook post the bot marked POSTED in the last 2 days is
actually there. Anything missing = exit 1 = red run = somebody gets told.

No token, no secrets, nothing to expire. If Facebook ever blocks the fetch,
that is ALSO a red run (we would rather be told than be blind again).
"""
import csv, html, re, sys, datetime as dt, urllib.request, urllib.parse

PLUGIN = "https://www.facebook.com/plugins/page.php?href={}&tabs=timeline&width=500&height=3000"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
LOOKBACK_DAYS = 2
SNIPPET = 28   # chars of caption we look for — short enough to survive Facebook's "See more" truncation


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def norm(s):
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


def main():
    brands = {r["brand"].strip(): r for r in csv.DictReader(open("brands.csv", encoding="utf-8"))}
    rows = list(csv.DictReader(open("content_queue.csv", encoding="utf-8")))
    today = dt.date.today()
    since = today - dt.timedelta(days=LOOKBACK_DAYS)

    expected = {}   # brand -> [(date, snippet, caption)]
    for r in rows:
        if r.get("status", "").strip().upper() != "POSTED":
            continue
        if "FB" not in (r.get("posted_to") or "").upper():
            continue
        try:
            d = dt.date.fromisoformat(r["date"].strip())
        except Exception:
            continue
        if d < since or d > today:
            continue
        cap = norm(r.get("caption", ""))
        if len(cap) < 12:
            continue
        expected.setdefault(r["brand"].strip(), []).append((d, cap[:SNIPPET], cap[:70]))

    failures, notes = [], []
    for brand, items in expected.items():
        cfg = brands.get(brand)
        if not cfg:
            failures.append(f"{brand}: not in brands.csv")
            continue
        page = cfg["fb_page_id"].strip()
        url = PLUGIN.format(urllib.parse.quote(f"https://www.facebook.com/{page}", safe=""))
        try:
            raw = fetch(url)
            body = norm(raw)
        except Exception as e:
            failures.append(f"{brand}: could not fetch the public page plugin ({e}). BLIND — check by hand from a logged-out browser.")
            continue
        if page not in raw and "followers" not in body:
            failures.append(f"{brand}: plugin loaded but the page name is missing — Facebook served a login wall or blocked the runner. BLIND.")
            continue
        for d, snip, cap in items:
            if snip in body:
                notes.append(f"OK   {brand} {d}  \"{cap}...\"  is PUBLIC")
            else:
                failures.append(f"HIDDEN {brand} {d}  \"{cap}...\"  marked POSTED but NOT visible logged-out")

    print("\n".join(notes) if notes else "(no bot Facebook posts in the last %d days to check)" % LOOKBACK_DAYS)
    if failures:
        print("\n=== PUBLIC VISIBILITY FAILURES ===")
        print("\n".join(failures))
        with open("last_failure.txt", "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M} pubcheck: " + " | ".join(failures) + "\n")
        sys.exit(1)
    print("\nAll recent Facebook posts are visible to the public. ✅")


if __name__ == "__main__":
    main()
