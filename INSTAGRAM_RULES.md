# Instagram posting rules — DWR Music bot

*Written 2026-08-31 from real Business Suite numbers. Anyone filling `content_queue.csv` follows this.*
*Moved into the repo 2026-09-03 by Social Command — this file now lives beside `post.py`, which is the only copy that counts. The old copy in `Master Project Controller\\CommandPost\\dwr-bot-upgrade\\` is an 11 August dead snapshot and has been deleted.*

---

## Why these rules exist — the numbers

Last 28 days (Aug 3 – Aug 30, 2026), **David Wayne Redfearn Music**:

| | Facebook | Instagram |
|---|---|---|
| Reach, last full week | **1,800** | **509** |
| Views, 28 days | **20,200** (+142%) | — |
| Unique people, 28 days | **9,312** (+192%) | — |
| Posts, last week | 17 | 11 |
| Stories, last week | 6 | **0** |

**Facebook is doing ~3.5× the work off roughly the same content.** That is not a failure of the
Instagram content — it is how the two platforms differ:

- **Facebook reaches people with an existing connection** — friends of friends, people in Newton
  and Des Moines, people who follow the venue. A local network. David's audience is a
  central-Iowa, goes-out-to-a-bar-on-Friday crowd, and that crowd lives on Facebook.
- **Instagram barely uses your network at all.** Reach there comes from the algorithm pushing
  Reels to strangers. It takes months of consistent posting before it decides what you are.
  David started posting there seriously only weeks ago. 509 is a normal number for that stage.

**So: do not split effort evenly.** Keep Instagram alive and consistent, but Facebook gets the
best material and the priority. For the venue pitch this does not matter at all — a bar owner is
buying local reach, and local reach is exactly what Facebook delivers.

---

## The rules

### 1. Never post a gig or venue clip to Instagram without the venue's handle
Fill **`ig_mentions`** with the venue's `@handle`. Instagram auto-links it. An untagged venue post
is a wasted post — the tag is the entire reason the venue values this.
Fill **`fb_page_tags`** with the venue's Facebook **Page ID** for the same post.

### 2. Every venue needs BOTH identifiers on file before the gig
Before a date goes in the queue, the venue's Facebook Page ID **and** Instagram @handle must be
recorded in the venue tracker
(`G:\My Drive\Businesses\DWR Music\01_Brand\DWR_Music_DesMoines_Venue_Targets.xlsx`).
This is already what David asks venues for on the one-sheet: *"Your Facebook page name and
Instagram handle, so I tag you right."* Chase it if it's missing.

### 3. Location tag on every Instagram post — ✅ SUPPORTED as of 2026-09-03
Location tags are how locals discover things on Instagram. This is the single biggest lever for
a local act, and the bot now does it.

**How to use it:** put the venue's numeric **Facebook Place id** in the `ig_location_id` column.
**Leave it blank and the post simply goes out untagged** — blank is always safe, and so are the
words `none` / `skip` / `-`.

**Where to get the number:** it is the Facebook *Place* id, not the Page id and not an @handle.
Open the venue's Facebook page, click the address / map, and the number is in the URL. Record it
in the venue tracker next to the Page id and the Instagram handle, so it is entered once, ever.

**What happens if it is wrong:** nothing bad. Before publishing, the bot looks the id up. If it
does not resolve — a typo, an @handle pasted in by mistake, a Place that cannot be tagged — it
writes the reason into the run log and **publishes the post without the tag.** A missing location
tag is a much smaller loss than a missing post, so the post always wins.

### 4. Stories go to BOTH platforms — ✅ SUPPORTED as of 2026-09-03
Last week: 6 Facebook stories, **0 Instagram stories**. Stories are the cheapest thing on
Instagram and the main way existing followers see anything at all. Same story, both places.
**The bot does this now.** Put `story` in the row's **`format`** column. Blank (or `feed`) is an ordinary post — every existing row is unaffected.

A story is a different animal and the bot treats it as one: **no caption, no first comment, no location tag, no Page mentions.** Meta ignores all of those on a story, so chasing them would only write false failures into the log every run. Story rows carry the picture or clip and nothing else, and they disappear after 24 hours.

**Facebook video stories are the one thing still missing** — they need a three-phase upload that is unbuilt. A `story` row with an `.mp4` going to Facebook is refused with a plain-English reason rather than half-working; set that row's `platform` to `ig`, or use a still.

### 5. Captions
Do not reuse a Facebook caption verbatim on Instagram. Facebook tolerates a link in the body;
Instagram makes links unclickable, so on Instagram say **"link in bio"**. Hashtags do real work
on Instagram and almost none on Facebook — keep them to a handful of local, specific ones
(`#DesMoines #IowaMusic #LiveMusic` plus the town), never a wall.

### 6. Reels are the only Instagram format that reaches strangers
A static photo on Instagram reaches roughly nobody outside the follower list. If it's going to
Instagram and it could be a reel, make it a reel. Business Suite's own recommendation on the
account: 1–3 reels a week, 15–90 seconds, at least 720p, **never muted**.

---

## GAPS — what the bot can and cannot do yet

These are code changes to `post.py`, owned by Social Command. Escalated 2026-08-31.

### ✅ Gap 1 — Instagram location tag — CLOSED 2026-09-03
Built and shipped. `content_queue.csv` now carries **12** columns:
`brand, date, platform, caption, fb_page_tags, ig_mentions, media_url, first_comment,
ig_location_id, posted_to, attempts, status`.

`ig_location_id` is passed to Instagram's media container as `location_id`. Blank = skip.
`resolve_ig_location()` in `post.py` validates the id first and falls back to an untagged post
rather than failing the run, and if Instagram refuses a tag that did resolve, the container is
retried once without it. See Rule 3 above for how to fill the column in.

**Not yet proven on a live post** — no gig row has a Place id in it yet, and the account is still
waiting on the Instagram API permission below. The first gig post that carries one is the test.

### ✅ Gap 2 — stories — CLOSED 2026-09-03 (with one carve-out)

### Gap 2 — no stories, either platform
Built. `content_queue.csv` now carries a **`format`** column — blank / `feed` / `reel` = an ordinary
post, **`story`** = a 24-hour story. A `platform` value was deliberately NOT used: it would collide
with `both`.

| Route | How it publishes | State |
|---|---|---|
| Instagram story, photo | `media_type=STORIES` + `image_url` | ✅ built |
| Instagram story, video | `media_type=STORIES` + `video_url` | ✅ built |
| Facebook story, photo | unpublished `/photos` → `/photo_stories` | ✅ built |
| **Facebook story, video** | three-phase resumable upload | ❌ **not built** — the row is refused with a reason, never half-posted |

A story row skips the caption, the first comment, the location tag and Page mentions, because Meta
ignores all four on a story. A story row with no `media_url` is refused.

**Proven end to end against a stubbed Graph API** (feed posts, IG photo story, IG video story, FB photo
story, the no-media refusal and the FB-video refusal all behave correctly, and an ordinary feed post is
byte-for-byte unchanged). **NOT yet proven on a live account** — same Instagram permission block as
Gap 1.
*Impact: 0 Instagram stories last week against 6 on Facebook. Cheapest reach on the platform,
currently unused.*

### Not a gap, just a known limit
Facebook blocks tagging **personal profiles** through the API — Meta's privacy rule, not a bug.
For a performer with a personal profile rather than a Page, post with the bot then edit the live
post by hand and type their `@name`. Pages and all Instagram mentions are fully automated.


---

## 🔴 The one thing blocking Instagram right now — David's Meta dashboard

Every Instagram call beyond the post itself has been refused since 27 August:
`HTTP 400 (#10) Application does not have permission for this action`. Facebook was fixed on
27 August and its first comments are confirmed landing. **Instagram was never fixed**, which is
why `needs_comment.txt` still collects `IG` lines every run.

The location tag above and the stories work both run through the same Instagram permission, so
neither can be proven on a live post until it is granted. **This is the highest-value 2 minutes
in the whole bot.** It is `instagram_manage_comments` plus `instagram_content_publish`, in the
same App dashboard where the Facebook permissions were added.
