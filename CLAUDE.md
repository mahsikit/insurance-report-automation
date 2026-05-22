# CLAUDE.md — satria_data_report

## What this project does

Generates per-policy insurance claim Excel reports. For each active policy, it produces
one `.xlsx` file with two sheets:
- **CR** — claim ratio summary row (premiums, loss ratios, insured counts)
- **Query_result** or **Benefit** — raw claim line-items or benefit-level data

Reports are written to `output/<BROKER>/`, e.g. `output/ANDIKA/`.

---

## Project structure

```
satria_data_report/
├── script/
│   ├── main.py        # Entry point — orchestrates fetch → process
│   ├── fetch.py       # Pulls data from Metabase (question + dashboard APIs)
│   ├── process.py     # Joins CR + claim data, writes one Excel per policy
│   └── convert.py     # Legacy: manual xlsx→csv converter (not wired in, kept as fallback)
├── convert_csv/       # Intermediate CSVs written by fetch.py
├── input/             # Legacy: manual Excel drop folder (unused when fetch.py runs)
├── output/            # Final reports, grouped by broker subfolder
├── .env               # Real credentials (gitignored)
├── .env.example       # Template — copy to .env and fill in
├── .gitignore
└── requirements.txt
```

---

## Pipeline

```
main.py
  └─ fetch.py  →  convert_csv/claim_ratio_CR.csv
                  convert_csv/query_result_Query_result.csv
  └─ process.py → output/<BROKER>/Report Claim - <PERIOD> - <COMPANY>_<POLICY>.xlsx
```

### fetch.py

Three data sources, all via the Metabase REST API:

| Data | Source | API |
|---|---|---|
| Claim Ratio (CR) | Saved question `/question/552` | `POST /api/card/:id/query/csv` with parameters |
| Claim / Benefit raw data | Dashboard 48 or 38 | `POST /api/dashboard/:id/dashcard/:dc/card/:c/query/csv` |
| Active policy list | Saved question `/question/732` | `POST /api/card/:id/query/csv` (no filters) |

**Auth:** username/password session — `POST /api/session` → `X-Metabase-Session` header.

**CR fetch flow (card 552):**
1. `GET /api/card/552` — introspects the card's `parameters` list to get each param's `id`, `type`, and `target`
2. Builds `As_Of` filter = `YYYY-MM` derived from `REPORT_PERIOD` (e.g. `"2026-05"` for "Mei 2026") — collapses full history (~13,971 rows) to one snapshot row per policy for the month (~165 rows)
3. Optionally builds `policy_no` filter from the active policy list (card 732)
4. POSTs both parameters alongside the CSV request

**Dashboard fetch flow (dashboards 48 / 38):**
1. `GET /api/dashboard/:id` — auto-discovers the main dashcard ID, card ID, and parameter definitions
2. Builds `claim_date` filter = first day of next month with `~` prefix (e.g. `~2026-06-01` for "Mei 2026") — Metabase interprets this as "before that date", covering all of May
3. Builds `is_aso=false` filter — excludes ASO policies
4. Optionally builds `policy_no` filter from the active policy list
5. POSTs all parameters to get filtered CSV

### process.py

- Reads the two CSVs from `convert_csv/`, detects which is which by column headers
  (`Loss Ratio by GWP before Disc` or `gross_written_premium_before_disc` → CR file; `claims_id` → query/benefit file)
- Keeps all columns as `str` on read (preserves leading zeros on IDs like `nik`, `card_no`)
- Converts known money/count columns to numeric after load (see `CR_NUMERIC`, `QUERY_NUMERIC`)
- Iterates over each `policy_no` in the CR file, pairs it with all matching claim rows,
  writes one Excel per policy
- Broker subfolder derived from `source_name` field:
  `PT ANDIKA MITRA SEJATI (EB HEALTH)` → `ANDIKA`

---

## Configuration

### `REPORT_PERIOD`
Set manually at the top of `script/process.py`:
```python
REPORT_PERIOD = "Mei 2026"
```
This controls:
- The `claim_date` end-of-month filter sent to the dashboard (auto-computed)
- The report filename: `Report Claim - Mei 2026 - <COMPANY>_<POLICY>.xlsx`

**Update this every month before running.**

### `.env` variables

| Variable | Required | Description |
|---|---|---|
| `METABASE_URL` | Yes | `https://metabase.easysunday.co.id` |
| `METABASE_USER` | Yes | Login email |
| `METABASE_PASSWORD` | Yes | Login password |
| `METABASE_CR_CARD_ID` | Yes | Saved question ID for claim ratio — currently `552` |
| `METABASE_QUERY_CARD_ID` | Yes | Dashboard ID for raw claim data — currently `48` |
| `METABASE_BENEFIT_CARD_ID` | Optional | Dashboard ID for benefit-level data — currently `38` |
| `METABASE_ACTIVE_POLICY_CARD_ID` | Optional | Saved question ID for active policy list — currently `732` |

---

## How to run

### Setup (first time)
```bash
pip install -r requirements.txt
cp .env.example .env
# fill in .env
```

### Claim-level report (default)
```bash
python3 script/main.py
```
Fetches from dashboard 48, second sheet named `Query_result`.

### Benefit-level report
```bash
python3 script/main.py --benefit
```
Fetches from dashboard 38, second sheet named `Benefit`.

---

## Key design decisions

- **dtype=str on CSV read** — preserves leading zeros on ID fields (`nik`, `card_no`,
  `member_id`). Numeric columns are cast explicitly after load.
- **Column-based file detection** — files are identified by distinctive column headers,
  not by filename, so renaming inputs doesn't break anything.
- **Broker subfolder** — `broker_folder()` in `process.py` derives a short name from
  `source_name`. Currently heuristic (strips "PT ", strips parenthetical, takes first
  word). Add an explicit dict mapping if multiple brokers need custom names.
- **Active policy filter is optional** — if `METABASE_ACTIVE_POLICY_CARD_ID` is unset,
  both the CR card and dashboard are fetched unfiltered.
- **As_Of on CR card** — card 552 holds daily snapshots for all historical policies. Without `As_Of` it returns ~13,971 rows. Filtering to `As_Of=YYYY-MM` gives one row per active policy for that month (~165 rows). Parameters are discovered dynamically from `GET /api/card/:id` so the code doesn't hardcode param IDs.
- **Dashboard vs question** — CR uses a saved question filtered by `As_Of` (month snapshot); claim data uses dashboards because the `claim_date` (before-date) and `is_aso` filters are configured there.
- **Skipped policies** — a policy in the CR snapshot with zero matching claim rows in the dashboard data is skipped (no Excel written). This is expected for policies with no claims in the period.
