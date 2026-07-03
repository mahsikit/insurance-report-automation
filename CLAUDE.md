# CLAUDE.md — satria_data_report

## What this project does

Generates per-policy insurance claim Excel reports. For each active policy it produces
one `.xlsx` file with two sheets:
- **CR** — claim ratio summary row (premiums, loss ratios, insured counts)
- **Query_result** or **Benefit** — raw claim line-items or benefit-level data

Reports are uploaded to Google Drive, the master tracking sheet is updated, and Gmail drafts
are created ready for review and manual sending.

---

## Project structure

```
satria_data_report/
├── script/
│   ├── main.py          # Entry point — orchestrates the full pipeline
│   ├── auth.py          # OAuth 2.0 web-client credential flow + token caching
│   ├── fetch.py         # Pulls data from Metabase (question + dashboard APIs)
│   ├── process.py       # Joins CR + claim data, writes one Excel per policy
│   ├── upload.py        # Uploads output folder tree to Google Drive
│   ├── sheets.py        # Syncs master sheet; reads routing + recipients; writes links
│   └── drafts.py        # Creates Gmail drafts with Excel attachments
├── convert_csv/         # Intermediate CSVs written by fetch.py (gitignored)
├── output/              # Final reports, nested by broker/company/policy (gitignored)
├── .env                 # Real credentials (gitignored)
├── .env.example         # Template — copy to .env and fill in
├── token.json           # OAuth refresh token, created on first run (gitignored)
├── satria_yudha.json    # OAuth web-client secret — gitignored, never commit
├── .gitignore
└── requirements.txt
```

---

## Pipeline

Sheet sync happens **first** — new active policies are added automatically, and column C
drives per-policy routing to the correct Metabase dashboard.

```
main.py
  └─ auth.py    → credentials (OAuth 2.0; token.json cached after first browser consent)
  └─ fetch.py   → fetch_active_policies_full() — card 732 active policy list
  └─ sheets.py  → sync_new_policies() — append any new policies to FINAL tab
  └─ sheets.py  → read_master() → master dict (policy_no → routing + recipients)
  └─ [date filter] → exclude policies lapsed > 3 months before report period end
  └─ fetch.py   → convert_csv/claim_ratio_CR.csv          (all eligible policies)
                  convert_csv/query_result_Query_result.csv (header policies → dash 48)
                  convert_csv/query_result_Benefit.csv      (detail policies → dash 38)
  └─ process.py → output/<source_name>/<company_name>/<policy_no>/
                    Report Claim - <PERIOD> - <COMPANY>_<POLICY>.xlsx
  └─ upload.py  → Google Drive folder (same subfolder structure)
  └─ sheets.py  → write_links() — columns J (Drive link) + K (as_of) on matched rows
  └─ drafts.py  → Gmail drafts (one per unique To+Cc group)
                  ← pipeline ends here
```

---

## Automation (Gitea Actions)

Workflow file: `.github/workflows/report_automation.yml`

**Scheduled run:** every 1st of the month at 08:00 WIB (01:00 UTC).
The period is auto-derived as the previous calendar month
(e.g. workflow fires on June 1 → period = "Mei 2026").

**Manual trigger (`workflow_dispatch`) inputs:**

| Input | Default | Description |
|---|---|---|
| `period` | previous month | Override period in Indonesian format (e.g. `Mei 2026`) |
| `manual_policies` | blank (all) | Comma-separated policy numbers to filter |

**Gitea secrets required** (update when switching accounts):

| Secret | Content |
|---|---|
| `OAUTH_CLIENT_SECRET_JSON` | Full contents of `satria_yudha.json` |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Filename: `satria_yudha.json` |
| `TOKEN_JSON` | Contents of `token.json` generated locally with the satria account |

> The `use_benefit` flag has been removed. Per-policy routing (`detail` vs `header`) is
> now driven by column C of the FINAL master sheet.

---

## auth.py

Wraps `InstalledAppFlow` with `token.json` caching. Scopes: Drive, Sheets, Gmail Compose.
On first run a browser window opens for consent; subsequent runs refresh silently.

`GOOGLE_LOGIN_HINT` env var (optional) pre-fills the Google account in the browser.

**Fixed port:** the local server uses port **8080** so the redirect URI is predictable
for web-type OAuth clients. Before the first run with `satria_yudha.json`:
1. Go to Google Cloud Console → APIs & Services → Credentials → the satria client.
2. Add `http://localhost:8080/` to **Authorized redirect URIs**.
3. Delete `token.json` so the consent flow re-runs as the satria account.
4. After consent succeeds locally, copy the new `token.json` contents to the `TOKEN_JSON`
   Gitea secret so the runner can authenticate without a browser.

---

## fetch.py

Four Metabase API calls, three of which run in parallel.

| Function | Source | Purpose |
|---|---|---|
| `fetch_active_policies_full()` | Card 732 | Full active policy list for sheet sync |
| `_fetch_cr_csv()` | Card 552 | CR summary (all eligible policies) |
| `_fetch_dashboard_csv()` × 2 | Dashboard 48 + 38 | Claim / benefit data (parallel) |

**Auth:** username/password session — `POST /api/session` → `X-Metabase-Session` header.

**Card 732 columns returned:** `policy_no`, `company_name`, `policy_effective_date`,
`policy_renewal_date`, `source_name`.

**CR fetch flow (card 552):**
1. `GET /api/card/552` — introspects `parameters` to discover `id`, `type`, `target`, `slug`
2. Builds `As_Of` filter = `YYYY-MM` derived from `REPORT_PERIOD`
3. Sends combined policy list (header + detail) as `policy_no` filter
4. Matches parameters by `slug` (`"As_Of"`) not display name (`"As Of"`)

**Dashboard fetch flow (dashboards 48 / 38):**
1. `GET /api/dashboard/:id` — auto-discovers dashcard ID, card ID, parameter definitions
2. Builds `claim_date` = first day of next month with `~` prefix (e.g. `~2026-06-01`)
3. Builds `is_aso=false` filter
4. Sends the respective policy list (header or detail) as `policy_no` filter
5. Skipped entirely if the policy set is empty

---

## process.py

- Reads CR CSV plus whichever of `Query_result` / `Benefit` CSVs exist in `convert_csv/`
- Per-policy routing from `master` (column C): `detail` → `Benefit` sheet (dash 38 data),
  `header` → `Query_result` sheet (dash 48 data)
- Keeps all columns as `str` on read (preserves leading zeros on IDs like `nik`, `card_no`)
- Converts known money/count columns to numeric after load (`CR_NUMERIC`, `QUERY_NUMERIC`)
- Writes Excel files in **parallel** using `ThreadPoolExecutor` (8 workers) with `xlsxwriter` engine
- Output folder structure: `output/<source_name>/<company_name>/<policy_no>/`
- Returns `list[dict]` with `file_path`, `policy_no`, `company_name`, `source_name`,
  `policy_effective_date`, `policy_renewal_date` for each file written
- Policies in CR with no matching rows in the routed data source are skipped

---

## upload.py

- Accepts a `credentials` object (OAuth); passes it to every Drive API call
- `specific_files` parameter (list of dicts from `process_join`) limits upload to freshly written files only
- Uses `_get_or_create_folder()` with a local cache to avoid creating duplicate folders
- **Parallel Upload**: `ThreadPoolExecutor` with 20 workers
- **Safe Overwrite**: if a file already exists in Drive, updates it via `fileId` instead of duplicating
- Returns `dict[policy_no] → {file_id, web_view_link, file_path, company_name, source_name,
  policy_effective_date, policy_renewal_date}` — date fields passed through to drafts.py
- `webViewLink` is constructed manually as `https://drive.google.com/file/d/{file_id}/view`

---

## sheets.py

Three functions, all operating on the **FINAL** tab:

- `sync_new_policies(creds, sid, sheet)` — reads column E directly (all rows, ignoring
  column C) to detect gaps; appends one row per new active policy with A/B/E/H/I populated.
  Called once per run before `read_master()`.
- `read_master(creds, sid, sheet)` — reads `A:K`; returns
  `dict[policy_no] → {"to", "cc", "need_report", "row", "e_date"}`.
  Only rows with non-blank column C and column E are included.
- `write_links(creds, sid, sheet, upload_results, as_of, master)` — batch-writes
  Drive link (col J) and as_of (col K) for each uploaded policy row.

- Column A always has data → no row-trim shift in the Sheets API response.
- Row 1 is the header — skipped by row number, not string match.
- First occurrence of each policy_no is used; duplicates are ignored.

**Column layout (load-bearing — sheet must not be reorganised):**

| Column | Content | Written by |
|---|---|---|
| A | company_name | sync_new_policies |
| B | source_name | sync_new_policies |
| C | need_report (`detail` or `header`) | user |
| E | policy_no (match key) | sync_new_policies |
| F | To addresses | user |
| G | Cc addresses | user |
| H | s_date (policy effective date) | sync_new_policies |
| I | e_date (policy renewal date) | sync_new_policies |
| J | Drive link | write_links |
| K | Latest As Of | write_links |

---

## drafts.py

- Receives `master` (from `sheets.read_master()`) and `upload_results` (from `upload.py`)
- Groups policies by **unique `(To, Cc)` combination** — `source_name` no longer splits groups
- Policies where **both To and Cc are blank** are never bundled together — each such policy
  gets its own separate draft, since there's no way to know if they belong in the same email
- Each draft has:
  - **Subject:** `Report Claim Insured - {bulan} {year} - {broker}` where broker = unique
    source_name(s) in the group joined by ` / `
  - **Body:** HTML email in Indonesian — fixed copy with as-of month, plus an HTML table of
    `No | policy_no | company_name | policy_effective_date | policy_renewal_date`
  - **Jolly HR paragraph** — included by default; **omitted** if broker name contains `andika`
  - **Attachments:** the `.xlsx` file for each policy in the group
- `{bulan}` = period month (e.g. Mei), `{bulan_lalu}` = previous month (e.g. April)
- Drive links are **not** included in the email body
- Still creates a draft when To and/or Cc are blank in the master sheet — those
  headers are simply left unset on the draft so the user can fill them in manually
  before sending
- Uses `gmail.compose` scope — drafts sit in the satria account's Gmail inbox, ready for review
- Gmail's 25 MB per-message limit applies to total attachment size

---

## Configuration

### `REPORT_PERIOD`
Passed via `--period "Mei 2026"`. Defaults to the previous month on every run.

Controls:
- The `As_Of` filter sent to CR card (`2026-05`)
- The `claim_date` filter sent to the dashboard (`~2026-06-01`)
- The `As_Of` filter sent to the active policy card (`2026-05-31`)
- The lapse cutoff date (last day of period minus 3 months)
- The report filename

### `.env` variables

| Variable | Required | Description |
|---|---|---|
| `METABASE_URL` | Yes | `https://metabase.yourcompany.com` |
| `METABASE_USER` | Yes | Login email |
| `METABASE_PASSWORD` | Yes | Login password |
| `METABASE_CR_CARD_ID` | Yes | Saved question ID for claim ratio |
| `METABASE_QUERY_CARD_ID` | Yes | Dashboard ID for claim-level data (dash 48) |
| `METABASE_BENEFIT_CARD_ID` | Yes | Dashboard ID for benefit-level data (dash 38) |
| `METABASE_ACTIVE_POLICY_CARD_ID` | Yes | Card 732 — active policy list with source_name |
| `GOOGLE_DRIVE_FOLDER_ID` | Yes | Target Google Drive folder ID for uploads |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Yes | Filename of the OAuth client JSON (e.g. `satria_yudha.json`) |
| `MASTER_SPREADSHEET_ID` | Yes | Google Sheets spreadsheet ID (from the URL) |
| `MASTER_SHEET_NAME` | Yes | Sheet tab name — currently `FINAL` |
| `GOOGLE_LOGIN_HINT` | Optional | Pre-fills the Google account chooser on first consent |

### Credential files
- `satria_yudha.json` — OAuth 2.0 web-type client secret for the satria account. **Never commit.**
  Requires `http://localhost:8080/` registered as an Authorized redirect URI in Google Cloud Console.
- `token.json` — Generated automatically after first browser consent. Contains the refresh token. **Never commit.**

---

## How to run

### Setup (first time)
```bash
pip install -r requirements.txt
cp .env.example .env
# fill in .env
# place satria_yudha.json at project root
# register http://localhost:8080/ in Google Cloud Console for the satria client
```

### Full run (previous month, all policies)
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

Routing (claim vs benefit detail) is per-policy, driven by column C of the FINAL
master sheet. The `--benefit` flag has been removed.

---

## Key design decisions

- **dtype=str on CSV read** — preserves leading zeros on ID fields (`nik`, `card_no`, `member_id`). Numeric columns are cast explicitly after load.
- **Per-policy routing** — column C of the FINAL tab decides per policy: `detail` → dashboard 38 (Benefit sheet), `header` → dashboard 48 (Query_result sheet). Sheet is read before fetch so routing drives the API calls.
- **Sheet sync before fetch** — card 732 is queried first to detect new active policies. New rows are appended to FINAL (cols A/B/E/H/I) so the user only needs to fill in C (routing) and F/G (recipients) before the next run picks them up.
- **Date filter** — policies where `e_date` (col I) is more than 3 months before the last day of the report period are excluded automatically. Policies with no `e_date` are treated as eligible.
- **Parallel fetch** — CR + both dashboards fetched simultaneously with `ThreadPoolExecutor(max_workers=3)`.
- **Parallel Excel writing** — 8 worker threads write Excel files concurrently with the `xlsxwriter` C engine.
- **Parallel Google Drive upload** — 20 worker threads upload concurrently. Existing files are updated via `fileId` instead of duplicated.
- **As_Of slug not name** — Metabase returns `name: "As Of"` (space) but `slug: "As_Of"` (underscore). Parameters are matched by `slug`.
- **Sheets A:K read** — reading from column A prevents the Sheets API from trimming leading empty rows, which would shift row indices and write to wrong cells.
- **Column E dedup check in sync** — `sync_new_policies` reads column E directly (not `read_master`) so policies added in a previous sync but without a routing value yet are not duplicated.
- **Conditional Jolly HR paragraph** — omitted from the email body when the broker name contains "andika". All other brokers receive the standard paragraph.
- **Gmail drafts, not auto-send** — drafts sit in the satria account's inbox for review. Grouped by `(To, Cc)` — same recipients always bundled into one draft regardless of source_name.
- **Fixed OAuth port 8080** — web-type clients require a predictable redirect URI. Port 8080 is used so only one URI needs registering in Google Cloud Console.
- **Strip env vars** — all path-critical env vars are `.strip()`-ed at read time to guard against trailing newlines in Gitea secrets.
- **PII / production** — data is patient insurance claims (PII). Pipeline runs on a self-hosted Gitea runner. Do not use GitHub-hosted Actions runners as data would transit Microsoft/GitHub infrastructure.
