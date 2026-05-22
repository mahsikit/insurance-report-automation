import os
import argparse
from dotenv import load_dotenv

# =========================
# BASE PATH
# =========================
BASE_DIR = os.path.dirname(os.path.dirname(__file__))

load_dotenv(os.path.join(BASE_DIR, ".env"))

from fetch import fetch_from_metabase
from process import process_join, REPORT_PERIOD

# =========================
# ARGUMENTS
# =========================
parser = argparse.ArgumentParser(description="Generate per-policy claim reports.")
parser.add_argument(
    "--benefit",
    action="store_true",
    help="Use benefit-level dashboard (METABASE_BENEFIT_CARD_ID) instead of claim-level.",
)
args = parser.parse_args()

# =========================
# PATHS
# =========================
CONVERT_FOLDER = os.path.join(BASE_DIR, "convert_csv")
OUTPUT_FOLDER = os.path.join(BASE_DIR, "output")

# =========================
# RUN PIPELINE
# =========================
fetch_from_metabase(CONVERT_FOLDER, use_benefit=args.benefit, report_period=REPORT_PERIOD)
process_join(CONVERT_FOLDER, OUTPUT_FOLDER, use_benefit=args.benefit)
