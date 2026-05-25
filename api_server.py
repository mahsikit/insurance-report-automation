import os
import datetime
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# ==============================================================================
# n8n WEBHOOK API WRAPPER
# Run this file locally (e.g. `python api_server.py`) and use an HTTP Request 
# node in n8n to trigger it!
# ==============================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

from script.fetch import fetch_from_metabase
from script.process import process_join
from script.upload import upload_output

app = Flask(__name__)

@app.route("/generate-report", methods=["POST"])
def generate_report():
    data = request.json or {}
    
    # Extract parameters passed from n8n
    use_benefit = data.get("benefit", False)
    manual_policies = data.get("policies")
    if manual_policies:
        manual_policies = [p.strip() for p in manual_policies.split(",")]
        
    period = data.get("period")
    if not period:
        months = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
        now = datetime.datetime.now()
        period = f"{months[now.month - 1]} {now.year}"
        
    convert_folder = os.path.join(BASE_DIR, "convert_csv")
    output_folder = os.path.join(BASE_DIR, "output")
    service_account_file = os.path.join(BASE_DIR, "service_account.json")
    drive_folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
    
    try:
        print(f"\n🔔 Received webhook request from n8n for period: {period}")
        fetch_from_metabase(convert_folder, use_benefit=use_benefit, report_period=period, manual_policies=manual_policies)
        process_join(convert_folder, output_folder, use_benefit=use_benefit, report_period=period)
        upload_output(output_folder, drive_folder_id, service_account_file)
        
        return jsonify({
            "status": "success",
            "message": "Reports generated and uploaded successfully",
            "period": period
        }), 200
        
    except Exception as e:
        print(f"\n❌ Error processing webhook request: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

if __name__ == "__main__":
    print("🚀 Starting Report Generation API Server on port 5000...")
    print("👉 In n8n, use an HTTP Request node to POST to: http://<your-machine-ip>:5000/generate-report")
    app.run(host="0.0.0.0", port=5000)
