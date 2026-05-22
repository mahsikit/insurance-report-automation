# CLAUDE.md — satria_data_report

## What this project does

Generates per-policy insurance claim Excel reports. For each active policy it produces
one `.xlsx` file with two sheets:
- **CR** — claim ratio summary row (premiums, loss ratios, insured counts)
- **Query_result** or **Benefit** — raw claim line-items or benefit-level data

Reports are written to `output/<source_name>/<company_name>/<policy_no>/` and
automatically uploaded to a Google Drive folder after generation.

---

## Project structure

```
satria_data_report/
├── script/
│   ├── main.py        # Entry point — orchestrates fetch → process → upload
│   ├── fetch.py       # Pulls data from Metabase (question + dashboard APIs)
│   ├── process.py     # Joins CR + claim data, writes one Excel per policy
│   ├── upload.py      # Uploads output folder tree to Google Drive
│   └── convert.py     # Legacy: manual xlsx→csv converter (kept as fallback)
├── convert_csv/       # Intermediate CSVs written by fetch.py (gitignored)
├── output/            # Final reports, nested by broker/company/policy/period (gitignored)
├── .env               # Real credentials (gitignored)
├── .env.example       # Template — copy to .env and fill in
├── service_account.json  # Google service account key (gitignored)
├── .gitignore
└── requirements.txt
```

---

## Pipeline

```
main.py
  └─ fetch.py   → convert_csv/claim_ratio_CR.csv
                  convert_csv/query_result_Query_result.csv
  └─ process.py → output/<source_name>/<company_name>/<policy_no>/
                    Report Claim - <PERIOD> - <COMPANY>_<POLICY>.xlsx
  └─ upload.py  → Google Drive folder (same subfolder structure)
```

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
2. Builds `As_Of` filter = `YYYY-MM` derived from `REPORT_PERIOD` (e.g. `"2026-05"` for "Mei 2026") — collapses full history (~13,971 rows) to one snapshot row per policy for the month (~165 rows)
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
- Writes Excel files in **parallel** using `ThreadPoolExecutor` (8 workers) with `xlsxwriter` engine (~2× faster than openpyxl)
- Output folder structure: `output/<source_name>/<company_name>/<policy_no>/`
- `source_name` is used as-is (full name, filesystem-safe characters only) — e.g. `PT COMPANY NAME`
- Policies in the CR snapshot with no matching claim rows are skipped (expected for zero-claim policies)

---

## upload.py

- Authenticates to Google Drive using a service account (`service_account.json`)
- Walks the `output/` folder tree and recreates the same subfolder structure inside the target Drive folder
- Uses `_get_or_create_folder()` with a local cache to avoid creating duplicate folders
- **Parallel Upload**: Uses `ThreadPoolExecutor` with 20 workers to upload files concurrently
- **Safe Overwrite**: Checks if a file exists on Drive before uploading; if it exists, updates it via `fileId` instead of duplicating
- Includes time tracking for the upload process
- Uploads each `.xlsx` file with the correct MIME type
- Target Drive folder ID: Configured via `GOOGLE_DRIVE_FOLDER_ID` in `.env`

---

## Configuration

### `REPORT_PERIOD`
The report period is passed dynamically via command line argument to `main.py` (e.g. `--period "Mei 2026"`). If omitted, it automatically defaults to the current month.

This controls:
- The `As_Of` filter sent to CR card 552 (e.g. `2026-05`)
- The `claim_date` filter sent to the dashboard (e.g. `~2026-06-01`)
- The `As_Of` filter sent to the active policy card (e.g. `2026-05-31` using `calendar.monthrange`)
- The report filename

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

### `service_account.json`
Google service account key file placed at the project root. **Never commit this file** —
it is listed in `.gitignore`. The service account email must have Editor access to the
target Google Drive folder.

---

## How to run

### Setup (first time)
```bash
pip install -r requirements.txt
cp .env.example .env
# fill in .env with Metabase credentials
# place service_account.json at project root
```

### Claim-level report (default)
```bash
python3 script/main.py
```
Fetches from dashboard 48, second sheet named `Query_result`. Automatically uses the current month.

To run for a specific month:
```bash
python3 script/main.py --period "Februari 2026"
```

### Benefit-level report
```bash
python3 script/main.py --benefit
```
Fetches from dashboard 38, second sheet named `Benefit`.

---

## Key design decisions

- **dtype=str on CSV read** — preserves leading zeros on ID fields (`nik`, `card_no`, `member_id`). Numeric columns are cast explicitly after load.
- **Column-based file detection** — files are identified by distinctive column headers, not by filename, so renaming inputs doesn't break anything.
- **Parallel fetch** — CR card and dashboard are fetched simultaneously with `ThreadPoolExecutor(max_workers=2)`, saving ~10–15 s of network wait.
- **Parallel Excel writing** — 8 worker threads write Excel files concurrently; combined with the `xlsxwriter` C engine this cuts processing time roughly in half vs sequential openpyxl.
- **Parallel Google Drive upload** — 20 worker threads upload files concurrently. Checks for existing files by name in the target folder and updates them instead of creating duplicates, keeping the folder clean.
- **As_Of slug not name** — Metabase returns `name: "As Of"` (space) but `slug: "As_Of"` (underscore). Parameters are matched by `slug` to avoid this mismatch.
- **Active policy filter is optional** — if `METABASE_ACTIVE_POLICY_CARD_ID` is unset, both the CR card and dashboard return all policies unfiltered.
- **Skipped policies** — a policy in the CR snapshot with zero matching claim rows in the dashboard data is skipped. Ten of the eleven skips in May 2026 had `number_claim_submission=0` in the CR; one (COMPANY X) had 1 claim but it was filtered out by `is_aso=false` on the dashboard side.
- **PII / production** — data is patient insurance claims (PII). Pipeline runs locally on demand. Do not use GitHub-hosted Actions runners as data would transit Microsoft/GitHub infrastructure. Use a self-hosted runner or a local cron job if automation is needed.
