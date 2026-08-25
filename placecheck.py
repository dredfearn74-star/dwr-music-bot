"""
ONE-TIME FIX — move the YouTube link out of the 2026-08-23 caption and into the
first comment, where David's rule says it belongs.

Authorised by David 2026-08-25: "fix it right now."

The post: 113416904992248_1033215906351317
Its caption carries https://youtu.be/S5gRdSLGWgA in the BODY, which throttles
reach. This edits the caption to remove the URL (leaving the words intact) and
posts the URL as the first comment instead. Then it READS BOTH BACK off the live
post and prints them, so the result is proved rather than assumed.

Editing does NOT recover reach already lost. This is about not leaving a
throttled post standing.
"""
import os, re, requests

VER  = "v23.0"
PAGE = "113416904992248"
POST = "113416904992248_1033215906351317"
BASE = "https://graph.facebook.com/" + VER
URL_RE = re.compile(r'https?://\S+|www\.\S+', re.I)


def log(m):
    print(m, flush=True)


def main():
    tok = os.environ.get("META_TOKEN", "")
    if not tok:
        log("no META_TOKEN."); return
    r = requests.get(BASE + "/" + PAGE,
                     params={"fields": "access_token", "access_token": tok}, timeout=60)
    ptok = (r.json() or {}).get("access_token") or tok

    log("=" * 74)
    log("FIXING THE 2026-08-23 POST - link out of the caption, into a comment")
    log("=" * 74)

    r = requests.get(BASE + "/" + POST, timeout=60,
                     params={"fields": "id,message,created_time", "access_token": ptok})
    if not r.ok:
        log("could not read the post: HTTP %s %s" % (r.status_code, r.text[:300])); return
    before = (r.json() or {}).get("message") or ""
    log("BEFORE: %r" % before)

    urls = URL_RE.findall(before)
    if not urls:
        log("")
        log("==> No URL in this caption any more. Nothing to fix - it is already clean.")
        return
    link = urls[0].rstrip(".,")
    log("link found: %s" % link)

    after = URL_RE.sub("", before)
    after = re.sub(r"[ \t]{2,}", " ", after)
    after = re.sub(r"\n{3,}", "\n\n", after).strip().rstrip("-–—").strip()
    after = after + "\n\n\U0001F517 Full song on YouTube — link in the comments \U0001F447"
    log("AFTER : %r" % after)

    log("")
    log("-- 1. editing the caption --")
    e = requests.post(BASE + "/" + POST, timeout=90,
                      data={"message": after, "access_token": ptok})
    log("   edit -> HTTP %s %s" % (e.status_code, e.text[:200]))

    log("-- 2. posting the link as the first comment --")
    c = requests.post(BASE + "/" + POST + "/comments", timeout=90,
                      data={"message": link, "access_token": ptok})
    log("   comment -> HTTP %s %s" % (c.status_code, c.text[:200]))
    cid = (c.json() or {}).get("id") if c.ok else None

    log("")
    log("-- 3. READING IT BACK OFF THE LIVE POST --")
    v = requests.get(BASE + "/" + POST, timeout=60,
                     params={"fields": "message", "access_token": ptok})
    now = ((v.json() or {}).get("message") or "") if v.ok else "(could not read)"
    log("   caption now : %r" % now)
    log("   caption clean: %s" % ("YES" if not URL_RE.search(now) else "NO - STILL HAS A URL"))

    g = requests.get(BASE + "/" + POST + "/comments", timeout=60,
                     params={"fields": "id,message", "order": "chronological",
                             "limit": 5, "access_token": ptok})
    data = ((g.json() or {}).get("data") or []) if g.ok else []
    log("   comments now: %d" % len(data))
    for d in data:
        log("     [%s] %s" % (d.get("id"), d.get("message")))
    ok = any(link.split("?")[0] in (d.get("message") or "") for d in data)
    log("   link is in a comment: %s" % ("YES" if ok else "NO"))

    log("")
    log("RESULT: %s" % ("FIXED - caption clean, link in the comments."
                        if (not URL_RE.search(now) and ok) else
                        "NOT fully fixed - read the lines above."))


if __name__ == "__main__":
    main()
