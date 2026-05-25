import sys
import os
import datetime

# ==============================================================================
# ⚠️ THIS SCRIPT IS DESIGNED TO BE PASTED INTO AN n8n "CODE" NODE (PYTHON) ⚠️
# ==============================================================================

# 1. Point n8n to your local project folder on the server
#    Change this path if n8n is running on a different machine or Docker volume
project_dir = "/Users/Syifa/Documents/satria_data_report"
if project_dir not in sys.path:
    sys.path.append(project_dir)

# 2. Load the environment variables from your .env file
from dotenv import load_dotenv
load_dotenv(os.path.join(project_dir, ".env"))

# 3. Import your modules from the local project folder!
from script.fetch import fetch_from_metabase
from script.process import process_join
from script.upload import upload_output

# 4. Define the period (dynamically fallback to current month)
#    You can also read this from n8n input: _input.item.json.get("period")
months = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
now = datetime.datetime.now()
period = f"{months[now.month - 1]} {now.year}"

# 5. Set up paths
convert_folder = os.path.join(project_dir, "convert_csv")
output_folder = os.path.join(project_dir, "output")
service_account_file = os.path.join(project_dir, "service_account.json")
drive_folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")

# 6. Execute the pipeline
#    (Note: the underlying scripts will use threading and file I/O as normal)
fetch_from_metabase(convert_folder, use_benefit=False, report_period=period)
process_join(convert_folder, output_folder, use_benefit=False, report_period=period)
upload_output(output_folder, drive_folder_id, service_account_file)

# 7. Return a success object to the next n8n node
return [{"json": {"status": "success", "period": period}}]
