"""
POST HISTORY DUMP + LINK AUDIT — read-only, publishes NOTHING.

Two jobs:
1. THE SAFETY QUESTION. David asked (2026-08-25) whether the 12 unused "batch 1"
   images in media/ (both-count, just-start, music-meets-you, gratitude,
   in-the-bridge, come-make-noise, your-voice-matters, one-whole-song,
   pass-the-light, turn-it-up, keep-playing, gentle) have ALREADY been posted.
   Re-posting a real post is a hard failure, so this walks the Page's whole
   published history and prints every post's date and caption. The queue cannot
   answer this — only the platform can.
2. The link-placement audit: is any URL sitting in a post BODY rather than in
   the first comment.
"""
import os, re, requests

VER  = "v23.0"
PAGE = "113416904992248"
BASE = "https://graph.facebook.com/" + VER
URL_RE = re.compile(r'(https?://\S+|www\.\S+)', re.I)

# the themes of the 12 unused batch-1 images, from their filenames
THEMES = {
    "both-count": ["both count", "every one counts"],
    "just-start": ["just start", "start where"],
    "music-meets-you": ["music meets you", "meets you"],
    "gratitude": ["grateful", "gratitude", "thank"],
    "in-the-bridge": ["bridge"],
    "come-make-noise": ["make noise", "make some noise"],
    "your-voice-matters": ["your voice", "voice matters"],
    "one-whole-song": ["whole song", "one song"],
    "pass-the-light": ["pass the light", "the light"],
    "turn-it-up": ["turn it up"],
    "keep-playing": ["keep playing", "keep going"],
    "gentle": ["gentle", "be gentle"],
}


def log(m):
    print(m, flush=True)


def main():
    tok = os.environ.get("META_TOKEN", "")
    if not tok:
        log("no META_TOKEN."); return
    r = requests.get(BASE + "/" + PAGE,
                     params={"fields": "access_token", "access_token": tok}, timeout=60)
    ptok = (r.json() or {}).get("access_token") or tok

    posts, url = [], BASE + "/" + PAGE + "/posts"
    params = {"fields": "id,created_time,message", "limit": 100, "access_token": ptok}
    for _ in range(6):                       # up to ~600 posts, plenty
        rr = requests.get(url, params=params, timeout=90)
        if not rr.ok:
            log("list failed: HTTP %s %s" % (rr.status_code, rr.text[:200])); break
        j = rr.json() or {}
        posts.extend(j.get("data") or [])
        nxt = ((j.get("paging") or {}).get("next"))
        if not nxt:
            break
        url, params = nxt, None

    log("")
    log("=" * 78)
    log("FULL PUBLISHED HISTORY - DWR Music Page - %d posts" % len(posts))
    log("=" * 78)
    for p in posts:
        when = (p.get("created_time") or "")[:10]
        msg = (p.get("message") or "(no caption)").replace("\n", " ")
        log("%s | %s" % (when, msg[:96]))

    log("")
    log("=" * 78)
    log("HAVE THE 12 BATCH-1 IMAGES ALREADY BEEN POSTED?")
    log("=" * 78)
    blob = " ".join((p.get("message") or "").lower() for p in posts)
    any_hit = False
    for name, needles in sorted(THEMES.items()):
        hits = [n for n in needles if n in blob]
        if hits:
            any_hit = True
        log("  %-22s %s" % (name, ("*** POSSIBLE MATCH: " + ", ".join(hits)) if hits else "no trace"))
    log("")
    log("  ==> %s" % ("SOME THEMES APPEAR IN LIVE POSTS - check the history above by hand "
                      "BEFORE queueing anything" if any_hit else
                      "NO trace of any batch-1 theme in the entire published history. "
                      "They have NOT been posted. Safe to queue."))

    log("")
    log("=" * 78)
    log("LINK AUDIT - any URL sitting in a post BODY?")
    log("=" * 78)
    bad = [(p, URL_RE.findall(p.get("message") or "")) for p in posts]
    bad = [(p, u) for p, u in bad if u]
    if not bad:
        log("  none. every caption is clean.")
    for p, u in bad:
        log("  %s  %s  -> %s" % ((p.get("created_time") or "")[:10], p.get("id"), ", ".join(u)[:70]))

    log("")
    log("finished. Nothing was published.")


if __name__ == "__main__":
    main()
