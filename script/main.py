import os
import sys
import argparse
import datetime
from dotenv import load_dotenv

# =========================
# BASE PATH
# =========================
BASE_DIR = os.path.dirname(os.path.dirname(__file__))

load_dotenv(os.path.join(BASE_DIR, ".env"))


def get_default_period():
    months = ["Januari", "Februari", "Maret", "April", "Mei", "Juni",
              "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
    now = datetime.datetime.now()
    return f"{months[now.month - 1]} {now.year}"


def _validate_env():
    required = {
        "GOOGLE_DRIVE_FOLDER_ID": os.environ.get("GOOGLE_DRIVE_FOLDER_ID"),
        "MASTER_SPREADSHEET_ID": os.environ.get("MASTER_SPREADSHEET_ID"),
        "GOOGLE_OAUTH_CLIENT_SECRET": os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        print(f"❌ Missing required environment variables: {', '.join(missing)}")
        print("   Copy .env.example to .env and fill in the values.")
        sys.exit(1)


def main():
    _validate_env()

    # =========================
    # ARGUMENTS
    # =========================
    parser = argparse.ArgumentParser(description="Generate per-policy claim reports.")
    parser.add_argument(
        "--benefit",
        action="store_true",
        help="Use benefit-level dashboard (METABASE_BENEFIT_CARD_ID) instead of claim-level.",
    )
    parser.add_argument(
        "--period",
        type=str,
        default=get_default_period(),
        help="Report period in Indonesian format (e.g., 'Juni 2026'). Defaults to current month.",
    )
    parser.add_argument(
        "--policy",
        type=str,
        help="Comma-separated list of policy numbers to filter (e.g. 'POL123,POL456').",
    )
    args = parser.parse_args()

    # =========================
    # PATHS & CONFIG
    # =========================
    CONVERT_FOLDER = os.path.join(BASE_DIR, "convert_csv")
    OUTPUT_FOLDER = os.path.join(BASE_DIR, "output")
    DRIVE_FOLDER_ID = os.environ["GOOGLE_DRIVE_FOLDER_ID"].strip()
    MASTER_SPREADSHEET_ID = os.environ["MASTER_SPREADSHEET_ID"].strip()
    MASTER_SHEET_NAME = os.environ.get("MASTER_SHEET_NAME", "2026").strip()
    CLIENT_SECRET_FILE = os.path.join(BASE_DIR, os.environ["GOOGLE_OAUTH_CLIENT_SECRET"].strip())
    TOKEN_FILE = os.path.join(BASE_DIR, "token.json")

    # =========================
    # IMPORTS
    # =========================
    from auth import get_credentials
    from fetch import fetch_from_metabase, _as_of_value
    from process import process_join
    from upload import upload_output
    from sheets import update_master_sheet
    from drafts import create_drafts

    # Authenticate once — shared by Drive, Sheets, Gmail
    creds = get_credentials(CLIENT_SECRET_FILE, TOKEN_FILE)

    # =========================
    # FULL PIPELINE
    # =========================
    manual_policies = [p.strip() for p in args.policy.split(",")] if args.policy else None

    fetch_from_metabase(CONVERT_FOLDER, use_benefit=args.benefit, report_period=args.period, manual_policies=manual_policies)
    written_files = process_join(CONVERT_FOLDER, OUTPUT_FOLDER, use_benefit=args.benefit, report_period=args.period)

    if not written_files:
        print("⚠️  No files written — nothing to upload.")
        return 1

    upload_results = upload_output(OUTPUT_FOLDER, DRIVE_FOLDER_ID, creds, specific_files=written_files)

    if upload_results:
        as_of = _as_of_value(args.period)
        recipient_map = update_master_sheet(creds, MASTER_SPREADSHEET_ID, MASTER_SHEET_NAME, upload_results, as_of)
        if recipient_map:
            create_drafts(creds, recipient_map, upload_results, args.period)

    return 0


if __name__ == "__main__":
    sys.exit(main())
