# Insurance Report Automation

Generates per-policy insurance claim Excel reports, uploads them to Google Drive, writes back links to a master tracking spreadsheet, and emails the reports to recipients — all from a single command.

## Features
- **Fully Automated Pipeline**: Fetches Claim Ratios and Raw Claims concurrently from Metabase, generates one `.xlsx` per policy, uploads to Drive, updates the master sheet, and sends grouped emails.
- **Parallel Processing**: Multi-threaded Excel writing and Drive uploads.
- **Smart Uploads**: Updates an existing Drive file instead of creating a duplicate.
- **Email Grouping**: Policies sharing the same To/CC pair are combined into one email with multiple attachments.
- **Dynamic Periods**: Defaults to the current month; accepts any Indonesian-format period via `--period`.

## Prerequisites
- Python 3.8+
- A Google Cloud project with Drive, Sheets, and Gmail APIs enabled.
- An **OAuth 2.0 Desktop** client secret file (download from Google Cloud Console → APIs & Services → Credentials).
- Metabase account credentials.

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure environment variables
```bash
cp .env.example .env
```
Open `.env` and fill in all required values (see the table below).

### 3. Place the OAuth client secret
Download the OAuth 2.0 Desktop client JSON from Google Cloud Console and put it at the project root. Set `GOOGLE_OAUTH_CLIENT_SECRET` in `.env` to its filename.

### 4. First run — browser consent
The first time you run the pipeline, a browser window opens asking you to grant Drive, Sheets, and Gmail access to the pipeline. After approving, a `token.json` file is created at the project root. Subsequent runs use that token silently (auto-refreshed).

If you have multiple Google accounts in your browser, set `GOOGLE_LOGIN_HINT=you@example.com` in `.env` to pre-select the right account.

## Usage

### Claim-level report (default)
```bash
python3 script/main.py
```

### Specific report period
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

### Safe runs — always preview first

**Dry run** (upload + sheet update happen, emails are only previewed):
```bash
python3 script/main.py --period "Mei 2026" --dry-run
```

When you run without `--dry-run`, the pipeline prints the email plan and asks for confirmation before sending. Type `y` to proceed, anything else to abort.

To skip the confirmation prompt in non-interactive environments (e.g. Gitea Actions):
```bash
python3 script/main.py --yes
```

> **Tip**: Always use `--policy` with a single test policy and `--dry-run` when verifying a new setup.

## Master spreadsheet column layout

`sheets.py` matches rows by **column H** (policy_no) and writes to columns D and E. Columns F and G supply recipient addresses. This layout is load-bearing — if the sheet is reorganised the script must be updated accordingly.

| Column | Content | Direction |
|---|---|---|
| H | `policy_no` (match key) | read |
| F | To address(es), comma or semicolon separated | read |
| G | Cc address(es), comma or semicolon separated | read |
| D | Drive `webViewLink` of the uploaded report | **written** |
| E | "Latest As Of" (`YYYY-MM` e.g. `2026-05`) | **written** |

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `METABASE_URL` | Yes | `https://metabase.yourcompany.com` |
| `METABASE_USER` | Yes | Metabase login email |
| `METABASE_PASSWORD` | Yes | Metabase login password |
| `METABASE_CR_CARD_ID` | Yes | Saved question ID for claim ratio |
| `METABASE_QUERY_CARD_ID` | Yes | Dashboard ID for raw claim data |
| `METABASE_BENEFIT_CARD_ID` | Optional | Dashboard ID for benefit-level data |
| `METABASE_ACTIVE_POLICY_CARD_ID` | Optional | Card ID for active policy list filter |
| `GOOGLE_DRIVE_FOLDER_ID` | Yes | Target Google Drive folder ID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Yes | Filename of the OAuth 2.0 Desktop client JSON |
| `MASTER_SPREADSHEET_ID` | Yes | Google Sheets spreadsheet ID (from the URL) |
| `MASTER_SHEET_NAME` | Yes | Sheet tab name (e.g. `2026`) |
| `GOOGLE_LOGIN_HINT` | Optional | Pre-fills the Google account chooser on first consent |

## Output structure
```
output/<source_name>/<company_name>/<policy_no>/Report Claim - <PERIOD> - <COMPANY>_<POLICY>.xlsx
```

## Architecture
- `script/main.py` — Entry point; orchestrates fetch → process → upload → sheet update → email.
- `script/auth.py` — OAuth 2.0 Desktop flow; caches credentials in `token.json`.
- `script/fetch.py` — Pulls data from Metabase (concurrent CR + dashboard fetch).
- `script/process.py` — Joins CR + claim data; writes one Excel per policy (parallel).
- `script/upload.py` — Uploads the output folder tree to Google Drive (parallel, safe overwrite).
- `script/sheets.py` — Updates columns D & E of the master tracking sheet; returns recipient map.
- `script/email_sender.py` — Groups policies by To/CC pair; sends one email per group via Gmail API. Edit the `SUBJECT_TEMPLATE` / `BODY_TEMPLATE` constants at the top of the file to customise the email content.

## Gitea Actions Automation
Trigger manually from the Gitea Actions tab. Requires `--yes` to be added to the pipeline command in the workflow YAML (non-interactive environment).

### Required secrets
- `METABASE_URL`, `METABASE_USER`, `METABASE_PASSWORD`
- `METABASE_CR_CARD_ID`, `METABASE_QUERY_CARD_ID`
- `METABASE_BENEFIT_CARD_ID`, `METABASE_ACTIVE_POLICY_CARD_ID`
- `GOOGLE_DRIVE_FOLDER_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `MASTER_SPREADSHEET_ID`, `MASTER_SHEET_NAME`
- `TOKEN_JSON` (contents of `token.json` generated locally after first consent)
