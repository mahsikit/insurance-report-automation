import os
import sys
import argparse
import datetime
import calendar
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
    # Default to previous month (matches cron behaviour)
    prev = now.replace(day=1) - datetime.timedelta(days=1)
    return f"{months[prev.month - 1]} {prev.year}"


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
        "--period",
        type=str,
        default=get_default_period(),
        help="Report period in Indonesian format (e.g., 'Juni 2026'). Defaults to previous month.",
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
    MASTER_SHEET_NAME = os.environ.get("MASTER_SHEET_NAME", "FINAL").strip()
    CLIENT_SECRET_FILE = os.path.join(BASE_DIR, os.environ["GOOGLE_OAUTH_CLIENT_SECRET"].strip())
    TOKEN_FILE = os.path.join(BASE_DIR, "token.json")

    # =========================
    # IMPORTS
    # =========================
    from auth import get_credentials
    from fetch import fetch_from_metabase, fetch_active_policies_full, _as_of_value, _as_of_last_day_value
    from process import process_join
    from upload import upload_output
    from sheets import read_master, sync_new_policies, write_links
    from drafts import create_drafts

    # Authenticate once — shared by Drive, Sheets, Gmail
    creds = get_credentials(CLIENT_SECRET_FILE, TOKEN_FILE)

    # =========================
    # 1. SYNC NEW POLICIES FROM METABASE → MASTER SHEET
    # =========================
    active_policy_card_id = os.environ.get("METABASE_ACTIVE_POLICY_CARD_ID", "").strip()
    if active_policy_card_id:
        import requests as _req
        base_url = os.environ["METABASE_URL"].rstrip("/")
        token = _req.post(
            f"{base_url}/api/session",
            json={"username": os.environ["METABASE_USER"], "password": os.environ["METABASE_PASSWORD"]},
            timeout=30,
        ).json()["id"]
        mb_headers = {"X-Metabase-Session": token}

        as_of_last_day = _as_of_last_day_value(args.period)
        print(f"🔄 Syncing active policies from Metabase (card {active_policy_card_id}, as_of={as_of_last_day})...")
        active_policies = fetch_active_policies_full(base_url, mb_headers, active_policy_card_id, as_of_last_day)
        print(f"   Metabase: {len(active_policies)} active policies")

        sync_new_policies(creds, MASTER_SPREADSHEET_ID, MASTER_SHEET_NAME, active_policies)
        print()

    # =========================
    # 2. READ MASTER SHEET (after sync — routing is per-policy)
    # =========================
    print(f"📋 Reading master sheet '{MASTER_SHEET_NAME}'...")
    master = read_master(creds, MASTER_SPREADSHEET_ID, MASTER_SHEET_NAME)
    print(f"   ✅ {len(master)} policies found\n")

    if not master:
        print("⚠️  Master sheet is empty — nothing to process.")
        return 1

    # =========================
    # 2. DATE FILTER — active or lapsed ≤ 3 months
    # =========================
    as_of_last_day = datetime.date.fromisoformat(_as_of_last_day_value(args.period))
    # Subtract 3 months from the last day of the report period
    cutoff_month = as_of_last_day.month - 3
    cutoff_year = as_of_last_day.year
    if cutoff_month <= 0:
        cutoff_month += 12
        cutoff_year -= 1
    cutoff_last = calendar.monthrange(cutoff_year, cutoff_month)[1]
    cutoff_date = datetime.date(cutoff_year, cutoff_month, min(as_of_last_day.day, cutoff_last))

    def _parse_edate(raw):
        for fmt in ("%d %B %Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                return datetime.datetime.strptime(raw.strip(), fmt).date()
            except ValueError:
                continue
        return None

    excluded_date = []
    eligible = {}
    for p, info in master.items():
        ed = _parse_edate(info.get("e_date", ""))
        if ed is None or ed >= cutoff_date:
            eligible[p] = info
        else:
            excluded_date.append(p)

    if excluded_date:
        print(f"   📅 Excluded (lapsed > 3 months): {len(excluded_date)} policies")
    print()

    # =========================
    # 3. SPLIT ROUTING
    # =========================
    manual_filter = set(p.strip() for p in args.policy.split(",")) if args.policy else None

    header_policies = [
        p for p, info in eligible.items()
        if info["need_report"] == "header"
        and (manual_filter is None or p in manual_filter)
    ]
    detail_policies = [
        p for p, info in eligible.items()
        if info["need_report"] == "detail"
        and (manual_filter is None or p in manual_filter)
    ]

    print(f"📊 Routing: {len(header_policies)} header (dash48)  |  {len(detail_policies)} detail (dash38)")
    if manual_filter:
        print(f"   🔎 Manual filter active: {', '.join(sorted(manual_filter))}")
    print()

    if not header_policies and not detail_policies:
        print("⚠️  No policies matched. Check --policy filter or master sheet column C.")
        return 1

    # =========================
    # 4. FETCH
    # =========================
    fetch_from_metabase(
        CONVERT_FOLDER,
        report_period=args.period,
        header_policies=header_policies or None,
        detail_policies=detail_policies or None,
    )

    # =========================
    # 5. PROCESS
    # =========================
    written_files = process_join(CONVERT_FOLDER, OUTPUT_FOLDER, master=master, report_period=args.period)

    if not written_files:
        print("⚠️  No files written — nothing to upload.")
        return 1

    # =========================
    # 5. UPLOAD
    # =========================
    upload_results = upload_output(OUTPUT_FOLDER, DRIVE_FOLDER_ID, creds, specific_files=written_files)

    # =========================
    # 6. UPDATE MASTER SHEET (write J + K)
    # =========================
    if upload_results:
        as_of = _as_of_value(args.period)
        write_links(creds, MASTER_SPREADSHEET_ID, MASTER_SHEET_NAME, upload_results, as_of, master)

        # =========================
        # 7. CREATE GMAIL DRAFTS
        # =========================
        create_drafts(creds, master, upload_results, args.period)

    return 0


if __name__ == "__main__":
    sys.exit(main())
