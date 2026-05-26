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


def _find_files_for_period(output_folder, period):
    """Walk output/ and return an upload_results-shaped dict for the given period.

    Matches files whose name contains the period string, e.g. "Mei 2026".
    Derives policy_no, company_name, source_name from the folder structure:
      output/<source_name>/<company_name>/<policy_no>/<filename>.xlsx
    """
    results = {}
    for root, _, files in os.walk(output_folder):
        for fname in files:
            if not fname.endswith(".xlsx") or period not in fname:
                continue
            file_path = os.path.join(root, fname)
            rel = os.path.relpath(file_path, output_folder)
            parts = rel.split(os.sep)
            if len(parts) < 4:
                continue
            source_name, company_name, policy_no = parts[0], parts[1], parts[2]
            results[policy_no] = {
                "file_path": file_path,
                "company_name": company_name,
                "source_name": source_name,
                "file_id": "",
                "web_view_link": "",
            }
    return results


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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview email plan without sending. Upload and sheet update still run.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive send-confirmation prompt (for non-TTY / CI environments).",
    )
    parser.add_argument(
        "--email-only",
        action="store_true",
        help="Skip fetch/process/upload — read existing output/ files and send emails only.",
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
    from sheets import update_master_sheet, read_recipients
    from email_sender import send_reports

    # Authenticate once — shared by Drive, Sheets, Gmail
    creds = get_credentials(CLIENT_SECRET_FILE, TOKEN_FILE)

    # =========================
    # EMAIL-ONLY MODE
    # =========================
    if args.email_only:
        upload_results = _find_files_for_period(OUTPUT_FOLDER, args.period)
        if not upload_results:
            print(f"❌ No files found in output/ for period '{args.period}'.")
            print("   Run the full pipeline first (without --email-only).")
            return 1
        print(f"📂 Found {len(upload_results)} file(s) for '{args.period}' in output/")
        recipient_map = read_recipients(creds, MASTER_SPREADSHEET_ID, MASTER_SHEET_NAME)
        send_reports(creds, upload_results, recipient_map, period=args.period,
                     dry_run=args.dry_run, assume_yes=args.yes)
        return 0

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
        send_reports(creds, upload_results, recipient_map, period=args.period,
                     dry_run=args.dry_run, assume_yes=args.yes)

    return 0


if __name__ == "__main__":
    sys.exit(main())
