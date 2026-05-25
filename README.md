# Insurance Report Automation

This project automates the generation of per-policy insurance claim Excel reports. It fetches data directly from Metabase via API, joins claim ratios with raw claim/benefit data, generates individual Excel files for each active policy, and uploads them securely to Google Drive.

## Features
- **Fully Automated Data Pipeline**: Fetches Claim Ratios and Raw Claims concurrently from Metabase.
- **Parallel Processing**: Uses multi-threading to write Excel files and upload to Google Drive blazingly fast.
- **Smart Uploads**: Detects if a report already exists on Google Drive and updates it instead of creating duplicates.
- **Dynamic Periods**: Automatically defaults to the current month or accepts any specific past/future period via command line.

## Prerequisites
- Python 3.8+
- A Google Cloud Service Account with Editor access to the target Google Drive folder.
- Metabase account credentials.

## Setup

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Environment Variables**
   Copy the example template to a new `.env` file:
   ```bash
   cp .env.example .env
   ```
   Open `.env` and fill in your Metabase credentials, target Card/Dashboard IDs, and the target Google Drive Folder ID.

3. **Google Drive Service Account**
   Place your Google Service Account key file at the root of the project and name it `service_account.json`. (Note: This file is ignored by Git to keep your credentials secure).

## Usage

### Claim-level report (Default)
Run the pipeline for the current month. This will fetch from the primary dashboard (e.g., Query_result).
```bash
python script/main.py
```

### Specific Report Period
You can generate reports for a specific past or future month by passing the `--period` argument (using Indonesian month names).
```bash
python script/main.py --period "Februari 2026"
```

### Filter by Specific Policies
You can filter the execution to only run for specific policies. Pass a single policy or a comma-separated list of policies. This makes the pipeline significantly faster by skipping the active policy lookup.
```bash
python script/main.py --policy "POL-12345,POL-98765"
```

### Benefit-level report
If you need to fetch from the benefit-level dashboard instead of the claim-level dashboard, use the `--benefit` flag.
```bash
python script/main.py --benefit
```

## Output Structure
The script will process the data locally before uploading it to Google Drive. The output is structured exactly like this:
```
output/<source_name>/<company_name>/<policy_no>/Report Claim - <PERIOD> - <COMPANY>_<POLICY>.xlsx
```

## Gitea Actions Automation
This project is configured to run automatically using Gitea Actions!
Instead of running it locally on your Mac, you can trigger it directly from the Gitea website:
1. Go to your repository on Gitea.
2. Click the **Actions** tab.
3. Select the **Satria Data Report Automation** workflow on the left side.
4. Click the **Run workflow** button.
5. A menu will appear where you can type in the **Period** (e.g. `Februari 2026`) and optionally supply a comma-separated list of **Manual Policies** if you only want to process specific policies.
6. Click **Run workflow**!

Gitea will spin up an isolated Ubuntu runner, download your data from Metabase, process all the files, and upload them to Google Drive in the background!

### Required Secrets
To make the automation work, the following secrets must be added to your repository (Settings -> Actions -> Secrets):
- `METABASE_URL`
- `METABASE_USER`
- `METABASE_PASSWORD`
- `METABASE_CR_CARD_ID`
- `METABASE_QUERY_CARD_ID`
- `METABASE_BENEFIT_CARD_ID`
- `METABASE_ACTIVE_POLICY_CARD_ID`
- `GOOGLE_DRIVE_FOLDER_ID`
- `SERVICE_ACCOUNT_JSON` (The entire contents of your Google Service Account JSON file)

## Architecture
- `script/main.py`: Entry point that orchestrates the fetch → process → upload pipeline.
- `script/fetch.py`: Pulls data from Metabase using session tokens and parallel requests.
- `script/process.py`: Joins the CR and claim data, filters, and writes Excel files concurrently.
- `script/upload.py`: Handles Google Drive folder tree creation and safe, multi-threaded file uploads.
