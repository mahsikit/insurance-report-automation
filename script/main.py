import os
import argparse
import datetime
from dotenv import load_dotenv

# =========================
# BASE PATH
# =========================
BASE_DIR = os.path.dirname(os.path.dirname(__file__))

load_dotenv(os.path.join(BASE_DIR, ".env"))

from fetch import fetch_from_metabase
from process import process_join
from upload import upload_output

def get_default_period():
    months = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
    now = datetime.datetime.now()
    return f"{months[now.month - 1]} {now.year}"

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
args = parser.parse_args()

# =========================
# PATHS
# =========================
CONVERT_FOLDER = os.path.join(BASE_DIR, "convert_csv")
OUTPUT_FOLDER = os.path.join(BASE_DIR, "output")
SERVICE_ACCOUNT_FILE = os.path.join(BASE_DIR, "service_account.json")
DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")

# =========================
# RUN PIPELINE
# =========================
fetch_from_metabase(CONVERT_FOLDER, use_benefit=args.benefit, report_period=args.period)
process_join(CONVERT_FOLDER, OUTPUT_FOLDER, use_benefit=args.benefit, report_period=args.period)
# upload_output(OUTPUT_FOLDER, DRIVE_FOLDER_ID, SERVICE_ACCOUNT_FILE)
