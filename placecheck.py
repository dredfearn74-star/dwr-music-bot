"""
PLACE CHECK — can we attach the venue to a post WITHOUT Meta App Review?
=======================================================================
Publishes NOTHING. Everything it creates is unpublished and deleted again.

WHY THIS EXISTS
---------------
The @[PageID] mention syntax is dead. tagcheck proved it again on 2026-08-21:
Facebook silently deletes the token and hands back message_tags=0. App Review is
the only thing that revives it, and David declined that on 2026-08-19. So every
venue tag on every campaign post has been added BY HAND. That has to stop.

This probes a different mechanism, one that is NOT part of the Page Mentioning
restriction: the `place` parameter. A post carrying place=<venue Page ID> renders
as "- at Murph and Mary's Pub", the venue name is a live link, and the post
surfaces on the venue's own Page. If it works, the venue half of the hand-tagging
goes away for good.

It probes the exact shape a real spotlight post needs:
  photo uploaded UNPUBLISHED to /photos  ->  /feed post that attaches it + place
That route also returns a proper page-post id (PAGEID_POSTID) instead of the bare
photo id the current /photos route returns - which is the OTHER bug on the 8/23
list, the one that kills needs_tagging.txt. So this tests both fixes at once.

Run it from the Tag check workflow. It touches post.py not at all.
"""
import os, json, requests

VER   = "v23.0"
PAGE  = "113416904992248"
VENUE = "110966485223915"
IMG   = "https://raw.githubusercontent.com/dredfearn74-star/dwr-music-bot/main/media/aug22_flyer.png"
BASE  = "https://graph.facebook.com/" + VER


def log(m):
    print(m, flush=True)


def main():
    tok = os.environ.get("META_TOKEN", "")
    if not tok:
        log("PLACECHECK: no META_TOKEN. Nothing to do.")
        return

    log("")
    log("========================================================================")
    log("PLACECHECK - nothing will be published. Every object is deleted again.")
    log("========================================================================")

    r = requests.get(BASE + "/" + PAGE,
                     params={"fields": "access_token", "access_token": tok}, timeout=60)
    ptok = (r.json() or {}).get("access_token") or tok
    log("page-scoped token resolved: %s" % ("yes" if ptok != tok else "no - using the token as given"))

    cleanup = []

    log("")
    log("=== TEST 1 - plain /feed post carrying place ==========================")
    r = requests.post(BASE + "/" + PAGE + "/feed", timeout=120, data={
        "message": "PLACE TEST please ignore",
        "place": VENUE,
        "published": "false",
        "access_token": ptok})
    if not r.ok:
        log("  REJECTED HTTP %s: %s" % (r.status_code, r.text[:400]))
    else:
        pid = (r.json() or {}).get("id")
        cleanup.append(pid)
        log("  created id: %s" % pid)
        g = requests.get(BASE + "/" + str(pid), timeout=60,
                         params={"fields": "id,message,place,message_tags", "access_token": ptok})
        body = g.json() if g.ok else {"error_text": g.text[:300]}
        log("  read back : %s" % json.dumps(body, indent=2)[:900])
        log("  >>> PLACE STUCK: %s" % ("YES" if body.get("place") else "NO"))

    log("")
    log("=== TEST 2 - the real shape: unpublished photo + /feed with place =====")
    r = requests.post(BASE + "/" + PAGE + "/photos", timeout=180, data={
        "url": IMG, "published": "false", "access_token": ptok})
    if not r.ok:
        log("  photo upload REJECTED HTTP %s: %s" % (r.status_code, r.text[:400]))
    else:
        media_id = (r.json() or {}).get("id")
        cleanup.append(media_id)
        log("  unpublished photo id: %s" % media_id)
        r2 = requests.post(BASE + "/" + PAGE + "/feed", timeout=120, data={
            "message": "PLACE TEST 2 please ignore",
            "place": VENUE,
            "attached_media[0]": json.dumps({"media_fbid": media_id}),
            "published": "false",
            "access_token": ptok})
        if not r2.ok:
            log("  feed post REJECTED HTTP %s: %s" % (r2.status_code, r2.text[:400]))
        else:
            pid2 = (r2.json() or {}).get("id")
            cleanup.append(pid2)
            log("  created id: %s" % pid2)
            log("  >>> ID SHAPE: %s" % ("PAGEID_POSTID - correct, this also fixes the hand-tag list bug"
                                        if "_" in str(pid2) else "bare id - still wrong"))
            g2 = requests.get(BASE + "/" + str(pid2), timeout=60,
                              params={"fields": "id,message,place,message_tags,attachments",
                                      "access_token": ptok})
            body2 = g2.json() if g2.ok else {"error_text": g2.text[:300]}
            log("  read back : %s" % json.dumps(body2, indent=2)[:1400])
            log("  >>> PLACE STUCK: %s" % ("YES" if body2.get("place") else "NO"))

    log("")
    log("=== CLEANUP ===========================================================")
    for cid in [c for c in cleanup if c]:
        d = requests.delete(BASE + "/" + str(cid), params={"access_token": ptok}, timeout=60)
        log("  deleted %s: HTTP %s %s" % (cid, d.status_code, d.text[:120]))

    log("")
    log("PLACECHECK finished. Nothing was published.")


if __name__ == "__main__":
    main()
