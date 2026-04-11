# PersonaBot Command Reference

All commands are slash commands under the `/pb` namespace. Commands are organized into four groups: **config**, **scrape**, **export**, and **stats**.

---

## Data Retention

All scraped data (messages, media, scrape jobs, export files) is **automatically deleted after 24 hours**. When a scrape completes, the bot notifies you of the retention window. Run `/pb export generate` within that time to receive your JSONL file as a Discord attachment. The retention period is configurable via the `RETENTION_HOURS` environment variable.

---

## `/pb config` — Server Configuration

**Requires:** Manage Server permission

### `/pb config show`

Display the current server configuration.

- **Parameters:** None
- **Response:** Ephemeral embed showing scrape channels, excluded users, and message limit
- **Notes:** Returns an error if no config exists yet

### `/pb config set-channels`

Set which channels the bot will scrape.

- **Parameters:** None (opens a channel picker)
- **Response:** Ephemeral channel select dropdown with multi-select checkboxes, then confirmation
- **Notes:** Displays a native Discord channel picker filtered to text/news channels. Select up to 25 channels, then click Confirm. Times out after 3 minutes.

### `/pb config set-excluded-users`

Set users to skip during scraping (e.g. other bots).

- **Parameters:** None (opens a user picker)
- **Response:** Ephemeral user select dropdown with multi-select checkboxes, then confirmation
- **Notes:** Displays a native Discord user picker with search. Select up to 25 users, then click Confirm. Times out after 3 minutes.

### `/pb config set-message-limit`

Set the default message limit per scrape job.

- **Parameters:**
  - `limit` (int, required) — Must be between 100 and 100,000
- **Response:** Ephemeral confirmation

---

## `/pb scrape` — Message Collection

**Requires:** Manage Server permission

### `/pb scrape start`

Start a background scrape job.

- **Parameters:**
  - `user` (Member, optional) — Target a specific user. Omit to scrape all users.
  - `channel` (TextChannel, optional) — Scrape one channel. Omit to use configured channels.
  - `limit` (int, optional) — Max messages to collect. Defaults to the configured limit (10,000).
- **Response:** Ephemeral confirmation with job ID
- **Behavior:**
  - Runs in the background via `asyncio.create_task()`
  - The limit is **global** across all channels (not per-channel)
  - Batches database writes every 100 messages
  - Posts progress updates to the channel every 500 messages
  - Downloads images from messages with 2+ reactions as sidecar media
  - Media saved to `data/media/{guild_id}/{message_id}/`
  - Job tracked in `scrape_jobs` table with status: running → completed/failed/cancelled
  - On completion, warns user to run `/pb export generate` within the retention window (default 24 hours)

### `/pb scrape status`

Check progress of a scrape job.

- **Parameters:**
  - `job_id` (string, required) — The job ID (autocomplete shows all recent jobs with status)
- **Response:** Ephemeral embed with status, messages found, messages stored, and any error

### `/pb scrape cancel`

Cancel a running scrape job.

- **Parameters:**
  - `job_id` (string, required) — The job ID (autocomplete shows running jobs only)
- **Response:** Ephemeral confirmation
- **Notes:** Uses cooperative cancellation via `asyncio.Event`. Only works while the job is actively running.

---

## `/pb export` — Corpus Export

**Requires:** Manage Server permission

### `/pb export generate`

Run the full preprocessing pipeline and export a user's corpus as JSONL.

- **Parameters:**
  - `user` (Member, required) — The user to export
  - `budget` (int, optional) — Token budget. Defaults to 100,000.
- **Response:** Ephemeral "exporting..." then a regular embed with the JSONL file attached
- **Pipeline:**
  1. Fetch all scraped messages for the user
  2. Filter low-quality messages (short, emoji-only, bot commands, etc.)
  3. Score remaining messages by quality signals (reactions, length, replies, formatting)
  4. Select top N messages (default 1,000)
  5. Group into conversation windows (new window on 4+ hour gap or channel change)
  6. Inject reply context (parent message content for replies)
  7. Enforce token budget (include whole windows in quality order until budget is full)
  8. Write JSONL file and send as Discord attachment
- **Delivery:** JSONL file sent as a Discord file attachment (up to 25 MB). Files exceeding 25 MB fall back to disk path. Embed footer warns about the retention window.
- **JSONL format:** Line 1 is corpus metadata (user, guild, date range, counts). Lines 2+ are one conversation window per line.

### `/pb export view`

Inspect an existing export without regenerating it.

- **Parameters:**
  - `user` (Member, required) — The user whose export to view
- **Response:** Ephemeral embed with message count, window count, file size, date range, and file path
- **Notes:** Reads the JSONL file on disk. Returns error if no export exists.

### `/pb export list`

List all scraped users and whether they have an existing export.

- **Parameters:** None
- **Response:** Regular embed listing up to 25 users with message counts and export status (exported/not exported)

---

## `/pb stats` — Analytics

**No permission required** — all stats commands are public.

### `/pb stats user`

Show message stats for a specific user.

- **Parameters:**
  - `user` (Member, required) — The user to show stats for
- **Response:** Regular embed with message count, avg words/message, total reactions, first/last message dates, and user avatar

### `/pb stats server`

Show server-wide scraping stats.

- **Parameters:** None
- **Response:** Regular embed with total messages, unique users, channels scraped, total reactions, and total scrape jobs

### `/pb stats top`

Show a leaderboard of top users by message count.

- **Parameters:**
  - `n` (int, optional, default 10) — Number of users to show (clamped to 1–25)
- **Response:** Regular embed with ranked list showing username, message count, and total reactions

---

## Summary

| Command | Permissions | Ephemeral | Key Parameters |
|---------|-------------|-----------|----------------|
| `/pb config show` | Manage Server | Yes | — |
| `/pb config set-channels` | Manage Server | Yes | channel picker (select menu) |
| `/pb config set-excluded-users` | Manage Server | Yes | user picker (select menu) |
| `/pb config set-message-limit` | Manage Server | Yes | limit (100–100k) |
| `/pb scrape start` | Manage Server | Yes | user?, channel?, limit? |
| `/pb scrape status` | Manage Server | Yes | job_id (autocomplete) |
| `/pb scrape cancel` | Manage Server | Yes | job_id (autocomplete) |
| `/pb export generate` | Manage Server | Yes → No | user, budget? |
| `/pb export view` | Manage Server | Yes | user |
| `/pb export list` | Manage Server | No | — |
| `/pb stats user` | None | No | user |
| `/pb stats server` | None | No | — |
| `/pb stats top` | None | No | n? (default 10) |
