# DWR Music — Auto-Publisher Bot 🎸

Posts **David Wayne Redfearn Music** content — images **and** video reels — to the DWR Music
**Facebook Page + Instagram**, on a schedule, hands-free. Runs on GitHub Actions (free).

> **Events are NOT this bot's job.** Posting *inside* a Facebook event (to notify people who
> RSVP'd) has no API — David + Claude do those live. This bot handles everyday page/feed content.

---

## How it works

Three files:

| File | What it is |
|------|-----------|
| `brands.csv` | The account directory — DWR Music's Facebook Page ID + Instagram ID (already filled in). |
| `content_queue.csv` | Your post list. One row per post, each with a **date**. The bot posts each row on its date. |
| `META_TOKEN` (a GitHub *secret*, not a file) | The login token for the DWR Music page. Set once. |

Every day the bot wakes up, looks for any row whose **date is today (US Central) and status is `QUEUED`**,
posts it to Facebook and/or Instagram, and marks it `POSTED`. That's how you "spread posts out" —
just give each one a different date.

- A `.mp4` / `.mov` file → posts as a **reel/video**.
- A `.jpg` / `.png` file → posts as a **photo**.

---

## One-time setup (≈10 min, David's hands)

1. **Add the token.** In this repo: **Settings → Secrets and variables → Actions → New repository secret.**
   - Name: `META_TOKEN`
   - Value: a **long-lived access token for the DWR Music page** with these permissions:
     `pages_show_list, pages_manage_posts, pages_read_engagement, instagram_basic, instagram_content_publish`.
   - Get it from the **Graph API Explorer** (pick the DWR Music page → generate token with those scopes),
     then run it through the **Access Token Debugger → Extend Access Token** so it lasts ~60 days.
   - *(Same Meta app as your other bots — "DWR Social Bot", App ID 1688393782412588. One app, one token.)*
2. That's it. The schedule (`.github/workflows/daily.yml`) is already set to run daily at 10:00 AM Central.

---

## How to add a post

1. Drop your image or CapCut clip into the **`media/`** folder (upload it right here on GitHub).
2. Open **`content_queue.csv`** and add a row:

   ```
   brand,date,platform,caption,fb_page_tags,ig_mentions,media_url,first_comment,ig_location_id,posted_to,attempts,status
   DWR Music,2026-08-02,both,"New cover dropping 🎶 what should I play next?",,,https://raw.githubusercontent.com/USERNAME/dwr-music-bot/main/media/cover_aug2.mp4,,,,,QUEUED
   ```

   - `date` — the day it should post (YYYY-MM-DD, US Central).
   - `platform` — `both`, `facebook`, or `instagram`.
   - `fb_page_tags` — *(optional)* Facebook **Page** IDs to tag, clickable (see **Tagging** below). Leave blank to skip.
   - `ig_mentions` — *(optional)* Instagram **@handles** to mention, clickable. Leave blank to skip.
   - `media_url` — the **raw** GitHub link to your file (replace `USERNAME` with your GitHub username).
   - `first_comment` — *(optional)* the link to post as the **first comment**. A URL posts as-is; `yt:Song Title` looks the song up on YouTube; `none` means no comment. Blank falls back to the brand's standing link in `brands.csv`. **Links never go in the caption** — Facebook throttles any post carrying one.
   - `ig_location_id` — *(optional)* the venue's numeric Facebook **Place** id, to tag the Instagram post's location (see **Location tags** below). Leave blank to skip.
   - `posted_to` / `attempts` — the bot's own bookkeeping. Leave blank.
   - `status` — `QUEUED` to go live; `DRAFT` or `HOLD` to keep it parked.

3. Save. The bot handles the rest on the date you set.

---

## Tagging (clickable @mentions)

Two optional columns let a post tag people/places **clickably, hands-free**:

| Column | What goes in it | Result |
|--------|-----------------|--------|
| `fb_page_tags` | Facebook **Page** IDs — e.g. the venue's Page, another band's Page. Comma-separated. Example: `100xxxxxxxxxx, 100yyyyyyyyyy` | The bot wraps each as `@[ID]` in the post text, which Facebook turns into a **clickable Page mention**. |
| `ig_mentions` | Instagram **@handles** — comma-separated, `@` optional. Example: `@murphandmarys, @davidwredfearnmusic` | Added to the Instagram caption; Instagram **auto-links** each handle. |

**The one thing that can't be automated:** Facebook blocks tagging **personal profiles** (a person, not a Page) through the API — that's Meta's privacy rule, not a bug. For a personal-profile performer, post the photo with the bot, then **edit the live Facebook post by hand** and type their `@name` in the composer (the photo's already attached, so it's a 20-second edit). Pages and all Instagram mentions are fully automated; only personal FB profiles need that manual touch.

> **Finding a Facebook Page ID:** open the Page → *About* → the numeric **Page ID** is listed, or use the Graph API Explorer. Personal profiles have no taggable API ID.

---

## Location tags on Instagram (`ig_location_id`)

*Added 2026-09-03. A location tag is how people nearby find a local act, so it is worth filling in on every gig post.*

Put the venue's numeric **Facebook Place id** in `ig_location_id`. **Blank = no tag, which is always safe** (so are `none`, `skip`, `-`).

**It is the Place id, not the Page id and not an @handle.** Open the venue's Facebook page, click its address or map, and the number is in the URL that opens. Write it into the venue tracker beside the Page id and the Instagram handle so it only ever gets looked up once.

**A wrong id cannot break a post.** Before publishing, the bot looks the id up:

| What you put in | What happens |
|---|---|
| A Place id that resolves | The post is tagged. The run log names the place, e.g. `location tag: Murph & Mary's (110…)`. |
| Blank / `none` / `skip` | `no location on this row`. Post goes out untagged. |
| An @handle or other non-number | Logged as ignored, with the reason. Post goes out untagged. |
| A number that doesn't resolve | Logged as ignored, with the HTTP code. Post goes out untagged. |
| A Place Instagram refuses to tag | Container retried once without the tag. Post still goes out. |

**The post always wins** — a missing location tag is a far smaller loss than a missing post.

⚠️ **Not yet proven on a live post.** The account's Instagram API permission is still refused (`#10 Application does not have permission`), so no Instagram write beyond the post itself can be confirmed. The first gig row that carries a Place id is the real test.

---

## Testing it

- Go to the **Actions** tab → **DWR daily post** → **Run workflow** to fire it on demand.
- Add one test row dated **today** with `status=QUEUED`, run it, and check Facebook + Instagram.
- The run log (and `autopublish_log.txt`) will show `POSTED`, `SKIPPED`, `Nothing due today`, or `FAILED`.

## Notes & troubleshooting

- **`FAILED` rows don't auto-retry** (so a bad post can't loop forever). Fix the issue and set the row's
  `status` back to `QUEUED` to try again.
- **Instagram needs a public media URL** — that's why media lives in this (public) repo.
- **Token expired?** Re-generate + extend it and update the `META_TOKEN` secret. Symptoms: `FAILED ... OAuth`.
- Keep media clips reasonably small (GitHub's per-file limit is 100 MB; short reels are fine).
