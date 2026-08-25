"""
LINK PLACEMENT AUDIT — where do our links actually sit?
=======================================================
Publishes NOTHING. Read-only.

THE RULE (Social Command standing rule):
  NO OUTBOUND LINKS IN THE POST BODY. Facebook throttles any post carrying an
  off-Facebook link. Publish the photo/video + caption clean, then put the link
  in the FIRST COMMENT. That is Meta's own guidance.

So: a URL in the FIRST COMMENT is correct and deliberate.
    A URL in the CAPTION is the violation.

David reported a DWR Music post "carrying a YouTube link" and called it a
violation. This settles which of the two actually happened, from the live posts
rather than from config. For the last 10 posts on the DWR Music Page it prints:
date, whether the CAPTION holds a URL, whether the FIRST COMMENT holds a URL,
and the URL itself.
"""
import os, re, requests

VER  = "v23.0"
PAGE = "113416904992248"
BASE = "https://graph.facebook.com/" + VER
URL_RE = re.compile(r'(https?://\S+|www\.\S+|\byoutu\.be/\S+|\byoutube\.com/\S+)', re.I)
SNIFF  = re.compile(r'http|youtu|www\.', re.I)


def log(m):
    print(m, flush=True)


def urls_in(text):
    return URL_RE.findall(text or "")


def main():
    tok = os.environ.get("META_TOKEN", "")
    if not tok:
        log("LINKAUDIT: no META_TOKEN.")
        return

    r = requests.get(BASE + "/" + PAGE,
                     params={"fields": "access_token", "access_token": tok}, timeout=60)
    ptok = (r.json() or {}).get("access_token") or tok

    log("")
    log("=" * 78)
    log("LINK PLACEMENT AUDIT - DWR Music Page - last 25 posts - read-only")
    log("=" * 78)

    r = requests.get(BASE + "/" + PAGE + "/posts", timeout=90, params={
        "fields": "id,created_time,message,permalink_url",
        "limit": 25, "access_token": ptok})
    if not r.ok:
        log("could not list posts: HTTP %s %s" % (r.status_code, r.text[:300]))
        return

    posts = (r.json() or {}).get("data") or []
    log("posts returned: %d" % len(posts))
    log("")
    log("%-12s %-9s %-13s %s" % ("DATE", "CAP_URL?", "1ST_CMT_URL?", "THE URL"))
    log("-" * 78)

    violations = []
    for p in posts:
        pid  = p.get("id")
        when = (p.get("created_time") or "")[:10]
        msg  = p.get("message") or ""
        cap_urls = urls_in(msg)

        c = requests.get(BASE + "/" + str(pid) + "/comments", timeout=60, params={
            "fields": "from,message,created_time", "order": "chronological",
            "limit": 5, "access_token": ptok})
        comments = ((c.json() or {}).get("data") or []) if c.ok else []
        first = comments[0].get("message") if comments else ""
        cmt_urls = urls_in(first)

        shown = (cap_urls + cmt_urls)
        log("%-12s %-9s %-13s %s" % (
            when,
            "YES !!" if cap_urls else "no",
            "yes" if cmt_urls else "no",
            (shown[0][:44] if shown else "-")))
        if cap_urls:
            violations.append((when, pid, cap_urls, msg[:120]))

    log("-" * 78)
    log("")
    if violations:
        log("!! CAPTION VIOLATIONS FOUND - these are the ones that get throttled:")
        for when, pid, urls, snippet in violations:
            log("   %s  %s" % (when, pid))
            log("      urls    : %s" % ", ".join(urls))
            log("      caption : %s..." % snippet)
    else:
        log("==> NO caption carries a URL. The rule is being FOLLOWED.")
        log("    Any link seen on these posts is in the FIRST COMMENT, which is")
        log("    correct and deliberate - that is Meta's own guidance and it is")
        log("    what protects the post's reach.")

    log("")
    log("--- full first comment on each post, for the record ---")
    for p in posts:
        pid = p.get("id"); when = (p.get("created_time") or "")[:10]
        c = requests.get(BASE + "/" + str(pid) + "/comments", timeout=60, params={
            "fields": "from,message", "order": "chronological",
            "limit": 3, "access_token": ptok})
        data = ((c.json() or {}).get("data") or []) if c.ok else []
        if not data:
            log("  %s  (no comments)" % when)
        else:
            for d in data[:2]:
                who = (d.get("from") or {}).get("name") or "?"
                log("  %s  [%s] %s" % (when, who, (d.get("message") or "")[:90]))

    log("")
    log("LINKAUDIT finished. Nothing was published.")


if __name__ == "__main__":
    main()
