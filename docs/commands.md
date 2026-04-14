# PersonaBot Command Reference

All commands are slash commands under the `/pb` namespace. Commands are organized into five groups: **config**, **scrape**, **export**, **stats**, and **db**.

**Configuration gate:** `/pb scrape start` takes **no optional parameters** — it runs from the saved guild config. Before the first scrape, run `/pb config setup` (or the individual `set-*` commands) to configure channels, included users, and the scrape window. `scrape start` errors with a missing-fields checklist if any required field is unset.

---

## Data Retention

All scraped data (messages, media, scrape jobs, export files) is **automatically deleted after 24 hours**. When a scrape completes, the bot notifies you of the retention window. Run `/pb export generate` within that time to receive your JSONL file as a Discord attachment. The retention period is configurable via the `RETENTION_HOURS` environment variable.

---

## `/pb config` — Server Configuration

**Requires:** Manage Server permission

### `/pb config setup`

Chain the three required config steps in order: channels → included users → scrape window. Use this on first setup to avoid running the three individual commands.

- **Parameters:** None
- **Response:**
  1. **Step 1 of 3** — channel picker (multi-select dropdown)
  2. **Step 2 of 3** — user picker (multi-select dropdown), shown after step 1 is confirmed
  3. **Step 3 of 3** — "Set window" button; clicking it opens the scrape-window modal (Discord modal API needs a fresh interaction, which the button click provides)
- **Notes:** Each step shares the underlying UI with its individual `/pb config set-*` command. Closing or timing out any step leaves the fields set up to that point — re-run `/pb config setup` or pick up with the individual command for the remaining step.

### `/pb config show`

Display the current server configuration.

- **Parameters:** None
- **Response:** Ephemeral embed showing scrape channels, included users, scrape window, and message limit
- **Notes:** Returns "run `/pb config setup`" if no config row exists yet

### `/pb config set-channels`

Set which channels the bot will scrape.

- **Parameters:** None (opens a channel picker)
- **Response:** Ephemeral channel select dropdown with multi-select checkboxes, then confirmation
- **Notes:** Displays a native Discord channel picker filtered to text/news channels. Select up to 25 channels, then click Confirm. Times out after 3 minutes.

### `/pb config set-included-users`

Set the allowlist of users whose messages will be scraped. **At least one user must be set before any scrape can run** — the empty state blocks `/pb scrape start`.

- **Parameters:** None (opens a user picker)
- **Response:** Ephemeral user select dropdown with multi-select checkboxes, then confirmation
- **Notes:** Displays a native Discord user picker with search. Select up to 25 users, then click Confirm. Times out after 3 minutes.

### `/pb config set-window`

Set the scrape date window (absolute start/end dates). A window is **required** before every `/pb scrape start`.

- **Parameters:** None (opens a modal dialog)
- **Response:** Modal with two fields: **Start Date** and **End Date**, both in UTC. Pre-filled with the current configured window if one exists.
- **Accepted formats:** `YYYY-MM-DD` or the unpadded `YYYY-M-D` (e.g. `2024-01-15` or `2024-1-15`). Year must be 4 digits; month and day may be 1 or 2 digits.
- **Validation:** Both fields required. Start must be on or before End. End must not be in the future. End date is **inclusive** (messages sent on the end date are included).
- **Notes:** Stored as absolute dates, not rolling windows — set it once per campaign slice and it stays until you change it or `/pb config reset`.

### `/pb config set-message-limit`

Set the message limit per scrape job.

- **Parameters:**
  - `limit` (int, required) — Must be between 100 and 100,000
- **Response:** Ephemeral confirmation

### `/pb config reset`

Reset all configuration fields back to defaults.

- **Parameters:** None
- **Response:** Ephemeral confirmation message with red **Reset Config** button and grey **Cancel** button. Times out after 30 seconds.
- **Behavior:** On confirm, clears scrape channels, included users, scrape window (back to NULL), and message limit (back to 10,000). The `guild_config` row and its `created_at` timestamp are preserved.
- **Notes:** Author-only interaction — only the user who ran the command can click the buttons. Destructive action cannot be undone.

---

## `/pb scrape` — Message Collection

**Requires:** Manage Server permission

### `/pb scrape start`

Run a message scrape using the saved guild config. Takes **no parameters** — configure everything via `/pb config setup` first, then run this.

- **Parameters:** None
- **Response:**
  - If channels, included users, or scrape window are unset, returns an ephemeral missing-fields checklist pointing to the relevant `/pb config set-*` commands (or `/pb config setup`). The scrape is **not started**.
  - Otherwise, confirms the job ID, target counts (users, channels), window, and limit in an ephemeral message, then kicks off the background task.
- **Behavior:**
  - Runs in the background via `asyncio.create_task()`. The command returns immediately.
  - `channel.history(after=start_dt, before=end_dt)` bounds the collection to the configured window; end date is **inclusive** (midnight of end date + 1 day is used as the exclusive upper bound).
  - The limit is **global** across all channels (not per-channel) and caps from the oldest end of the window.
  - Filter: only messages from users in the configured include list are stored; messages from other authors are skipped without counting toward the limit.
  - Batches database writes every 100 messages; each batch uses a fresh short-lived pool connection.
  - Posts progress updates to the invoking channel every 500 messages.
  - Downloads images from messages with 2+ reactions as sidecar media (threshold is `MEDIA_REACTION_THRESHOLD`).
  - Media saved to `data/media/{guild_id}/{message_id}/`.
  - Job tracked in `scrape_jobs` table; status transitions `running → completed | failed | cancelled`.
  - Every stored message is stamped with the `job_id` of the scrape that **first** captured it (**first-wins** — subsequent scrapes that re-hit the same message do not overwrite `job_id`). This is what makes `/pb export generate job_id:X` return a stable, reproducible corpus.
  - On completion, warns the user to run `/pb export generate` within the retention window (default 24 hours).

### `/pb scrape status`

Check progress of a scrape job.

- **Parameters:**
  - `job_id` (string, required) — The job ID (autocomplete shows all recent jobs for this guild with status and found count)
- **Response:** Ephemeral embed with status, messages found, messages stored, and any error

### `/pb scrape cancel`

Cancel a running scrape job.

- **Parameters:**
  - `job_id` (string, required) — The job ID (autocomplete shows running jobs only)
- **Response:** Ephemeral confirmation
- **Notes:** Uses cooperative cancellation via `asyncio.Event`. Only works while the job is actively running — completed/failed/cancelled jobs return "not running".

---

## `/pb export` — Corpus Export

**Requires:** Manage Server permission

### `/pb export generate`

Run the full preprocessing pipeline and export one user's corpus as JSONL.

- **Parameters:**
  - `job_id` (string, optional) — Restrict the export to messages that a specific scrape **first captured** (autocomplete shows recent jobs with stored data). Omit to export from every user currently in the database.
- **Response flow:**
  - If no messages are stored at all (or no messages match the given `job_id`), returns an actionable ephemeral error pointing to `/pb scrape start`.
  - If exactly one user has stored messages (within the optional `job_id` filter), the bot auto-resolves and runs the export pipeline without a picker.
  - If multiple users have stored messages, shows an ephemeral picker with up to 25 users ordered by message count; clicking one runs the export for that user.
- **Authorization gate on `job_id`:** because `job_id` values are guild-blind `secrets.token_hex(6)` strings and autocomplete is just a hint, a `job_id` belonging to another guild is indistinguishable from "not found" — the bot does **not** leak the existence of cross-guild jobs.
- **Pipeline:**
  1. Fetch all messages for the resolved user (filtered by `job_id` when provided — every DB read is scoped so the result is exactly what scrape `X` first discovered).
  2. Filter low-quality messages (short, emoji-only, bot commands, etc.).
  3. Score remaining messages by quality signals (reactions, length, replies, formatting).
  4. Select top N messages (default 1,000, via `TOP_N_MESSAGES`).
  5. Group into conversation windows (new window on 4+ hour gap or channel change).
  6. Inject reply context (parent message content for replies).
  7. Enforce token budget (include whole windows in quality order until budget is full; default 100,000 via `TOKEN_BUDGET`).
  8. Write JSONL file and send as a Discord attachment.
- **Delivery:** JSONL file sent as a Discord file attachment (up to 25 MB). Files exceeding 25 MB are not attached — the embed shows a warning suggesting you reduce `TOKEN_BUDGET`. Embed footer warns about the retention window.
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

## `/pb db` — Server Data Management

**Requires:** Manage Server permission

### `/pb db reset`

Permanently delete **all scraped data** for this server: every stored message, every scrape job record, every downloaded media file, and every generated export file on disk. **Preserves `guild_config`** — the operator keeps their channels, included users, window, and message limit. Use `/pb config reset` separately if you also want to clear settings.

- **Parameters:** None
- **Response:**
  - If any scrape is currently running for this guild, the command refuses with an ephemeral error listing the running job IDs and tells the operator to `/pb scrape cancel <job_id>` first. A live scrape would race the delete and write orphan rows after the wipe.
  - Otherwise, the bot shows an ephemeral confirmation message with a red **Delete Server Data** button and a grey **Cancel** button. Author-only (only the invoker can click); times out after 30 seconds.
  - On confirm, the bot runs the delete in a single transaction, sweeps the filesystem outside the transaction, and edits the confirmation message with the result — counts for messages deleted, scrape jobs deleted, and media files deleted. If there was nothing to delete, the message says so and notes that the config is still intact.
- **Behavior:**
  - Database delete order is `downloaded_media` (with `RETURNING local_path` so the disk sweep has the paths) → `messages` → `scrape_jobs`, all in one `db.transaction()`.
  - Filesystem sweep happens **after** the transaction commits and **after** the pool connection is released: individual media file unlinks from the returned paths, then `shutil.rmtree` on `data/media/{guild_id}/` and `{exports_dir}/{guild_id}/` to catch any stragglers.
  - `guild_config` is intentionally untouched — this is the strict "data wipe, keep settings" path. It's orthogonal to `/pb config reset` so operators can combine as needed.
- **Safety notes:** This is the single most destructive command in the bot. There is no backup and no undo. The running-scrape block, the author-only confirmation, the 30-second timeout, and the ephemeral-only response are the only safeguards — plan accordingly.

---

## Summary

| Command | Permissions | Ephemeral | Key Parameters |
|---------|-------------|-----------|----------------|
| `/pb config setup` | Manage Server | Yes | — (chains channels → users → window) |
| `/pb config show` | Manage Server | Yes | — |
| `/pb config set-channels` | Manage Server | Yes | channel picker (select menu) |
| `/pb config set-included-users` | Manage Server | Yes | user picker (select menu) |
| `/pb config set-window` | Manage Server | Yes | modal with start/end dates (YYYY-MM-DD UTC) |
| `/pb config set-message-limit` | Manage Server | Yes | limit (100–100k) |
| `/pb config reset` | Manage Server | Yes | confirm button (danger) |
| `/pb scrape start` | Manage Server | Yes | — (uses saved config) |
| `/pb scrape status` | Manage Server | Yes | job_id (autocomplete) |
| `/pb scrape cancel` | Manage Server | Yes | job_id (autocomplete) |
| `/pb export generate` | Manage Server | Yes → No | job_id? (autocomplete) |
| `/pb export view` | Manage Server | Yes | user |
| `/pb export list` | Manage Server | No | — |
| `/pb stats user` | None | No | user |
| `/pb stats server` | None | No | — |
| `/pb stats top` | None | No | n? (default 10) |
| `/pb db reset` | Manage Server | Yes | confirm button (danger) |
