# CLAUDE.md — satria_data_report

## What this project does

Generates per-policy insurance claim Excel reports. For each active policy it produces
one `.xlsx` file with two sheets:
- **CR** — claim ratio summary row (premiums, loss ratios, insured counts)
- **Query_result** or **Benefit** — raw claim line-items or benefit-level data

Reports are uploaded to Google Drive, the master tracking sheet is updated, and
recipients receive the reports by email — all automatically.

---

## Project structure

```
satria_data_report/
├── script/
│   ├── main.py          # Entry point — orchestrates the full pipeline
│   ├── auth.py          # OAuth 2.0 Desktop credential flow + token caching
│   ├── fetch.py         # Pulls data from Metabase (question + dashboard APIs)
│   ├── process.py       # Joins CR + claim data, writes one Excel per policy
│   ├── upload.py        # Uploads output folder tree to Google Drive
│   ├── sheets.py        # Updates master tracking sheet (columns D, E); reads recipients (F, G)
│   ├── email_sender.py  # Sends grouped emails via Gmail API; templates at top of file
│   └── convert.py       # Legacy: manual xlsx→csv converter (kept as fallback)
├── convert_csv/         # Intermediate CSVs written by fetch.py (gitignored)
├── output/              # Final reports, nested by broker/company/policy (gitignored)
├── .env                 # Real credentials (gitignored)
├── .env.example         # Template — copy to .env and fill in
├── token.json           # OAuth refresh token, created on first run (gitignored)
├── client_secret_*.json # OAuth Desktop client secret — gitignored, never commit
├── .gitignore
└── requirements.txt
```

---

## Pipeline

```
main.py
  └─ auth.py    → credentials (OAuth 2.0; token.json cached after first browser consent)
  └─ fetch.py   → convert_csv/claim_ratio_CR.csv
                  convert_csv/query_result_Query_result.csv
  └─ process.py → output/<source_name>/<company_name>/<policy_no>/
                    Report Claim - <PERIOD> - <COMPANY>_<POLICY>.xlsx
  └─ upload.py  → Google Drive folder (same subfolder structure)
  └─ sheets.py  → master spreadsheet columns D (Drive link) + E (as_of)
                  reads columns F (To) + G (Cc) for recipient map
  └─ email_sender.py → one email per To/Cc group, .xlsx files attached
```

---

## auth.py

Wraps `InstalledAppFlow` with `token.json` caching. Scopes: Drive, Sheets, Gmail send.
On first run a browser window opens for consent; subsequent runs refresh silently.

`GOOGLE_LOGIN_HINT` env var (optional) pre-fills the Google account in the browser.

---

## fetch.py

Three data sources, all via the Metabase REST API. CR and dashboard are fetched
**in parallel** using `ThreadPoolExecutor`.

| Data | Source | API |
|---|---|---|
| Claim Ratio (CR) | Saved question `/question/552` | `POST /api/card/:id/query/csv` with parameters |
| Claim / Benefit raw data | Dashboard 48 or 38 | `POST /api/dashboard/:id/dashcard/:dc/card/:c/query/csv` |
| Active policy list | Saved question `/question/732` | `POST /api/card/:id/query/csv` with `As_Of` filter (last day of month) |

**Auth:** username/password session — `POST /api/session` → `X-Metabase-Session` header.

**CR fetch flow (card 552):**
1. `GET /api/card/552` — introspects the card's `parameters` list to discover each param's `id`, `type`, `target`, and `slug`
2. Builds `As_Of` filter = `YYYY-MM` derived from `REPORT_PERIOD` (e.g. `"2026-05"` for "Mei 2026") — collapses full history to one snapshot row per policy for the month
3. Optionally builds `policy_no` filter from the active policy list (card 732)
4. Matches parameters by `slug` (not `name`) — slug is `"As_Of"`, display name is `"As Of"`

**Dashboard fetch flow (dashboards 48 / 38):**
1. `GET /api/dashboard/:id` — auto-discovers the main dashcard ID, card ID, and parameter definitions
2. Builds `claim_date` filter = first day of next month with `~` prefix (e.g. `~2026-06-01` for "Mei 2026") — Metabase interprets this as "before that date", covering all of May
3. Builds `is_aso=false` filter — excludes ASO policies
4. Optionally builds `policy_no` filter from the active policy list
5. POSTs all parameters to get filtered CSV

---

## process.py

- Reads the two CSVs from `convert_csv/`, detects which is which by column headers
  (`Loss Ratio by GWP before Disc` or `gross_written_premium_before_disc` → CR file; `claims_id` → query/benefit file)
- Keeps all columns as `str` on read (preserves leading zeros on IDs like `nik`, `card_no`)
- Converts known money/count columns to numeric after load (`CR_NUMERIC`, `QUERY_NUMERIC`)
- Writes Excel files in **parallel** using `ThreadPoolExecutor` (8 workers) with `xlsxwriter` engine
- Output folder structure: `output/<source_name>/<company_name>/<policy_no>/`
- Returns `list[dict]` with `file_path`, `policy_no`, `company_name`, `source_name` for each file written
- Policies in the CR snapshot with no matching claim rows are skipped (expected for zero-claim policies)

---

## upload.py

- Accepts a `credentials` object (OAuth); passes it to every Drive API call
- `specific_files` parameter (list of dicts from `process_join`) limits upload to freshly written files only — avoids re-uploading the entire output folder on a filtered run
- Uses `_get_or_create_folder()` with a local cache to avoid creating duplicate folders
- **Parallel Upload**: `ThreadPoolExecutor` with 20 workers (Drive write quota is the real ceiling)
- **Safe Overwrite**: if a file already exists in Drive, updates it via `fileId` instead of duplicating
- Returns `dict[policy_no] → {file_id, web_view_link, file_path, company_name, source_name}`
- `webViewLink` is constructed manually as `https://drive.google.com/file/d/{file_id}/view`

---

## sheets.py

- Reads columns `A:H` in a single API call — column A has data in every row, preventing the Sheets API from trimming leading empty rows and causing an off-by-one index shift
- Matches rows by **column H** (policy_no); uses first occurrence only (duplicates are skipped)
- Row 1 is always the header — skipped by row number, not by string match
- Batch-updates **column D** (Drive `webViewLink`) and **column E** (`as_of` in `YYYY-MM` form) for matched rows
- Returns `dict[policy_no] → {"to": [str], "cc": [str]}` built from columns F and G (multiple addresses separated by `,` or `;`)

**Column layout (load-bearing — sheet must not be reorganised):**

| Column | Content |
|---|---|
| H | policy_no (match key) |
| F | To addresses |
| G | Cc addresses |
| D | Drive link (written by pipeline) |
| E | Latest As Of (written by pipeline) |

---

## email_sender.py

- Groups upload results by normalised `(to, cc)` tuple — policies sharing the same recipients are combined into one email with multiple attachments
- `SUBJECT_TEMPLATE` / `BODY_TEMPLATE` constants at the top of the file are the canonical place to edit email content; placeholders: `{marketing_name}`, `{company_name}`, `{source_name}`, `{period}`, `{company_list}`
- `_derive_name(email)` extracts a display name from the email local part (e.g. `"jonathan.santoso@…"` → `"Jonathan Santoso"`)
- **`dry_run=True`**: prints the send plan (To, Cc, subject, attachment count) without calling Gmail
- **`assume_yes=True`**: skips the interactive confirmation prompt (required for non-TTY / CI runs)
- In interactive mode: prints the full plan, then prompts `Send N emails? [y/N]` once before sending

---

## Configuration

### `REPORT_PERIOD`
Passed via `--period "Mei 2026"`. Defaults to the current month.

Controls:
- The `As_Of` filter sent to CR card (`2026-05`)
- The `claim_date` filter sent to the dashboard (`~2026-06-01`)
- The `As_Of` filter sent to the active policy card (`2026-05-31`)
- The report filename and email subject

### `.env` variables

| Variable | Required | Description |
|---|---|---|
| `METABASE_URL` | Yes | `https://metabase.yourcompany.com` |
| `METABASE_USER` | Yes | Login email |
| `METABASE_PASSWORD` | Yes | Login password |
| `METABASE_CR_CARD_ID` | Yes | Saved question ID for claim ratio |
| `METABASE_QUERY_CARD_ID` | Yes | Dashboard ID for raw claim data |
| `METABASE_BENEFIT_CARD_ID` | Optional | Dashboard ID for benefit-level data |
| `METABASE_ACTIVE_POLICY_CARD_ID` | Optional | Saved question ID for active policy list |
| `GOOGLE_DRIVE_FOLDER_ID` | Yes | Target Google Drive folder ID for uploads |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Yes | Filename of the OAuth 2.0 Desktop client JSON |
| `MASTER_SPREADSHEET_ID` | Yes | Google Sheets spreadsheet ID (from the URL) |
| `MASTER_SHEET_NAME` | Yes | Sheet tab name (e.g. `2026`) |
| `GOOGLE_LOGIN_HINT` | Optional | Pre-fills the Google account chooser on first consent |

### Credential files
- `client_secret_*.json` — OAuth 2.0 Desktop client secret. Download from Google Cloud Console. Set filename in `GOOGLE_OAUTH_CLIENT_SECRET`. **Never commit.**
- `token.json` — Generated automatically after first browser consent. Contains the refresh token. **Never commit.**

---

## How to run

### Setup (first time)
```bash
pip install -r requirements.txt
cp .env.example .env
# fill in .env
# place client_secret_*.json at project root
```

### Claim-level report (default)
```bash
python3 script/main.py
```

### Specific month
```bash
python3 script/main.py --period "Februari 2026"
```

### Filter by specific policies
```bash
python3 script/main.py --policy "POL-12345,POL-98765"
```

### Benefit-level report
```bash
python3 script/main.py --benefit
```

### Safe runs
```bash
# Preview what would be emailed without sending
python3 script/main.py --period "Mei 2026" --dry-run

# Skip confirmation prompt (non-interactive / CI)
python3 script/main.py --yes
```

---

## Key design decisions

- **dtype=str on CSV read** — preserves leading zeros on ID fields (`nik`, `card_no`, `member_id`). Numeric columns are cast explicitly after load.
- **Column-based file detection** — files are identified by distinctive column headers, not by filename, so renaming inputs doesn't break anything.
- **Parallel fetch** — CR card and dashboard are fetched simultaneously with `ThreadPoolExecutor(max_workers=2)`, saving ~10–15 s of network wait.
- **Parallel Excel writing** — 8 worker threads write Excel files concurrently; combined with the `xlsxwriter` C engine this cuts processing time roughly in half vs sequential openpyxl.
- **Parallel Google Drive upload** — 20 worker threads upload files concurrently. Checks for existing files by name in the target folder and updates them instead of creating duplicates, keeping the folder clean.
- **As_Of slug not name** — Metabase returns `name: "As Of"` (space) but `slug: "As_Of"` (underscore). Parameters are matched by `slug` to avoid this mismatch.
- **Active policy filter is optional** — if `METABASE_ACTIVE_POLICY_CARD_ID` is unset, both the CR card and dashboard return all policies unfiltered.
- **Skipped policies** — a policy in the CR snapshot with zero matching claim rows in the dashboard data is skipped.
- **Sheets A:H read** — reading from column A (not F or H) prevents the Sheets API from trimming leading empty rows in the response, which would shift all row indices and write to the wrong cells.
- **First-occurrence dedup** — if a policy_no appears in multiple sheet rows, only the first is used. This prevents a duplicate entry from silently capturing the email send.
- **PII / production** — data is patient insurance claims (PII). Pipeline runs locally on demand. Do not use GitHub-hosted Actions runners as data would transit Microsoft/GitHub infrastructure. Use a self-hosted runner or a local cron job if automation is needed.
