import os
import requests
import calendar
import time
import threading
import concurrent.futures
import pandas as pd
import re
from tqdm import tqdm
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import datetime

# ==============================================================================
# ⚙️ CONFIGURATION MAPPING ⚙️
# ==============================================================================
# This section automatically pulls variables from the n8n input (if you pass them 
# from a previous "Set" or "Edit Fields" node). If they aren't provided by n8n,
# it falls back to the hardcoded values below.
# Think of this as your virtual .env file!

n8n_input = {}
try:
    # In n8n, _input.item.json contains the data passed from the previous node
    n8n_input = _input.item.json
except NameError:
    pass

METABASE_URL = n8n_input.get("METABASE_URL", "https://metabase.yourcompany.com")
METABASE_USER = n8n_input.get("METABASE_USER", "email@yourcompany.com")
METABASE_PASSWORD = n8n_input.get("METABASE_PASSWORD", "password")
METABASE_CR_CARD_ID = str(n8n_input.get("METABASE_CR_CARD_ID", "552"))
METABASE_QUERY_CARD_ID = str(n8n_input.get("METABASE_QUERY_CARD_ID", "48"))
METABASE_BENEFIT_CARD_ID = str(n8n_input.get("METABASE_BENEFIT_CARD_ID", ""))
METABASE_ACTIVE_POLICY_CARD_ID = str(n8n_input.get("METABASE_ACTIVE_POLICY_CARD_ID", "732"))
GOOGLE_DRIVE_FOLDER_ID = n8n_input.get("GOOGLE_DRIVE_FOLDER_ID", "your_drive_folder_id")

# You can pass the JSON object directly from n8n, or paste it here as a fallback
SERVICE_ACCOUNT_INFO = n8n_input.get("SERVICE_ACCOUNT_INFO", {
  "type": "service_account",
  "project_id": "...",
  "private_key_id": "...",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
  "client_email": "...",
  "client_id": "...",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "..."
})

USE_BENEFIT = n8n_input.get("USE_BENEFIT", False)

# Optional: comma-separated list of policies to filter, e.g., "POL-123, POL-456"
raw_policies = n8n_input.get("MANUAL_POLICIES", "")
MANUAL_POLICIES = [p.strip() for p in raw_policies.split(",")] if raw_policies else None

# Get period dynamically from n8n, or fallback to current month
months = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
now = datetime.datetime.now()
default_period = f"{months[now.month - 1]} {now.year}"
REPORT_PERIOD = n8n_input.get("REPORT_PERIOD", default_period)
# ==============================================================================

# ==============================================================================
# FETCH CONSTANTS & HELPERS
# ==============================================================================
CR_FILENAME = "claim_ratio_CR.csv"
QUERY_FILENAME = "query_result_Query_result.csv"

INDONESIAN_MONTHS = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "agustus": 8,
    "september": 9, "oktober": 10, "november": 11, "desember": 12,
}

def _claim_before_date(report_period):
    parts = report_period.strip().split()
    month = INDONESIAN_MONTHS[parts[0].lower()]
    year = int(parts[1])
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1
    return f"~{next_year:04d}-{next_month:02d}-01"

def _as_of_value(report_period):
    parts = report_period.strip().split()
    month = INDONESIAN_MONTHS[parts[0].lower()]
    year = int(parts[1])
    return f"{year:04d}-{month:02d}"

def _as_of_last_day_value(report_period):
    parts = report_period.strip().split()
    month = INDONESIAN_MONTHS[parts[0].lower()]
    year = int(parts[1])
    _, last_day = calendar.monthrange(year, month)
    return f"{year:04d}-{month:02d}-{last_day:02d}"

def _fetch_active_policy_nos(base_url, headers, card_id, as_of_date):
    card = requests.get(f"{base_url}/api/card/{card_id}", headers=headers, timeout=30).json()
    params_payload = []
    for param in card.get("parameters", []):
        slug = param.get("slug", "")
        param_id = param["id"]
        param_type = param.get("type", "")
        target = param.get("target")
        if slug.lower() == "as_of":
            entry = {"id": param_id, "type": param_type, "value": as_of_date}
            if target: entry["target"] = target
            params_payload.append(entry)
    resp = requests.post(f"{base_url}/api/card/{card_id}/query/csv", headers=headers, json={"parameters": params_payload}, timeout=60)
    resp.raise_for_status()
    lines = resp.text.strip().splitlines()
    if len(lines) < 2: return []
    col_headers = lines[0].split(",")
    idx = col_headers.index("policy_no")
    return [line.split(",")[idx].strip() for line in lines[1:] if line.strip()]

def _fetch_cr_csv(base_url, headers, card_id, as_of, policy_nos=None):
    card = requests.get(f"{base_url}/api/card/{card_id}", headers=headers, timeout=30).json()
    params_payload = []
    for param in card.get("parameters", []):
        slug = param.get("slug", "")
        param_id = param["id"]
        param_type = param.get("type", "")
        target = param.get("target")
        if slug == "As_Of":
            entry = {"id": param_id, "type": param_type, "value": as_of}
            if target: entry["target"] = target
            params_payload.append(entry)
        elif slug == "policy_no" and policy_nos:
            entry = {"id": param_id, "type": param_type, "value": policy_nos}
            if target: entry["target"] = target
            params_payload.append(entry)
    resp = requests.post(f"{base_url}/api/card/{card_id}/query/csv", headers=headers, json={"parameters": params_payload}, timeout=120)
    resp.raise_for_status()
    return resp.text

def _fetch_dashboard_csv(base_url, headers, dashboard_id, claim_before_date, policy_nos=None):
    resp = requests.get(f"{base_url}/api/dashboard/{dashboard_id}", headers=headers, timeout=30)
    resp.raise_for_status()
    dashboard = resp.json()
    dashcard = None
    for card in dashboard.get("dashcards", dashboard.get("ordered_cards", [])):
        if card.get("card_id"):
            dashcard = card
            break
    dashcard_id = dashcard["id"]
    card_id = dashcard["card_id"]
    params_payload = []
    for param in dashboard.get("parameters", []):
        slug = param.get("slug", "")
        param_id = param["id"]
        param_type = param.get("type", "")
        target = None
        for mapping in dashcard.get("parameter_mappings", []):
            if mapping.get("parameter_id") == param_id:
                target = mapping.get("target")
                break
        if slug == "claim_date":
            entry = {"id": param_id, "type": param_type, "value": claim_before_date}
            if target: entry["target"] = target
            params_payload.append(entry)
        elif slug == "is_aso":
            entry = {"id": param_id, "type": param_type, "value": "false"}
            if target: entry["target"] = target
            params_payload.append(entry)
        elif slug == "policy_no" and policy_nos:
            entry = {"id": param_id, "type": param_type, "value": policy_nos}
            if target: entry["target"] = target
            params_payload.append(entry)
    resp = requests.post(f"{base_url}/api/dashboard/{dashboard_id}/dashcard/{dashcard_id}/card/{card_id}/query/csv", headers=headers, json={"parameters": params_payload}, timeout=120)
    resp.raise_for_status()
    return resp.text

def fetch_from_metabase(convert_folder, use_benefit=False, report_period="Mei 2026", manual_policies=None):
    base_url = METABASE_URL.rstrip("/")
    cr_card_id = METABASE_CR_CARD_ID
    if use_benefit: query_dashboard_id = METABASE_BENEFIT_CARD_ID
    else: query_dashboard_id = METABASE_QUERY_CARD_ID

    claim_before = _claim_before_date(report_period)
    as_of = _as_of_value(report_period)
    os.makedirs(convert_folder, exist_ok=True)

    resp = requests.post(f"{base_url}/api/session", json={"username": METABASE_USER, "password": METABASE_PASSWORD}, timeout=30)
    resp.raise_for_status()
    token = resp.json()["id"]
    session_headers = {"X-Metabase-Session": token}

    active_policy_nos = None
    if manual_policies:
        active_policy_nos = manual_policies
    elif METABASE_ACTIVE_POLICY_CARD_ID:
        as_of_last_day = _as_of_last_day_value(report_period)
        active_policy_nos = _fetch_active_policy_nos(base_url, session_headers, METABASE_ACTIVE_POLICY_CARD_ID, as_of_last_day)

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_cr = executor.submit(_fetch_cr_csv, base_url, session_headers, cr_card_id, as_of, active_policy_nos)
        future_dashboard = executor.submit(_fetch_dashboard_csv, base_url, session_headers, query_dashboard_id, claim_before, active_policy_nos)
        cr_csv = future_cr.result()
        dashboard_csv = future_dashboard.result()

    with open(os.path.join(convert_folder, CR_FILENAME), "w", encoding="utf-8") as f:
        f.write(cr_csv)
    with open(os.path.join(convert_folder, QUERY_FILENAME), "w", encoding="utf-8") as f:
        f.write(dashboard_csv)

# ==============================================================================
# PROCESS CONSTANTS & HELPERS
# ==============================================================================
CR_NUMERIC = ["gross_written_premium_before_disc", "gross_earned_premium_before_disc", "policy_age_pcnt", "approval_amount", "active_insureds", "active_employees", "number_claim_submission", "Loss Ratio by GWP before Disc", "Loss Ratio by GEP before Disc"]
QUERY_NUMERIC = ["los", "incurred", "approved", "excess", "excess_paid_by_member", "age"]

def _apply_numeric(df, cols):
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

def _detect_files(convert_folder):
    cr_file = data_file = None
    for fname in os.listdir(convert_folder):
        if not fname.endswith(".csv"): continue
        path = os.path.join(convert_folder, fname)
        header = pd.read_csv(path, nrows=0).columns.tolist()
        if "Loss Ratio by GWP before Disc" in header or "gross_written_premium_before_disc" in header: cr_file = fname
        elif "claims_id" in header: data_file = fname
    return cr_file, data_file

def broker_folder(source_name):
    if not source_name or str(source_name).strip() in ("", "nan"): return "UNKNOWN"
    name = str(source_name).strip()
    return re.sub(r'[\\/:*?"<>|]', '', name)

def _write_policy(policy, df_cr_filtered, df_data_filtered, output_folder, data_sheet, report_period):
    company_name = str(df_cr_filtered.iloc[0]['company_name'])
    source_name = df_data_filtered.iloc[0].get('source_name', '')
    broker = broker_folder(source_name)
    safe_company = re.sub(r'[\\/:*?"<>|]', '', company_name).strip()
    policy_dir = os.path.join(output_folder, broker, safe_company, policy)
    os.makedirs(policy_dir, exist_ok=True)
    output_path = os.path.join(policy_dir, f"Report Claim - {report_period} - {safe_company}_{policy}.xlsx")
    with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
        df_cr_filtered.to_excel(writer, sheet_name="CR", index=False)
        df_data_filtered.to_excel(writer, sheet_name=data_sheet, index=False)

def process_join(convert_folder, output_folder, use_benefit=False, report_period="Mei 2026"):
    cr_file, data_file = _detect_files(convert_folder)
    if not cr_file or not data_file: return
    df_cr = _apply_numeric(pd.read_csv(os.path.join(convert_folder, cr_file), dtype=str), CR_NUMERIC)
    df_data = _apply_numeric(pd.read_csv(os.path.join(convert_folder, data_file), dtype=str), QUERY_NUMERIC)
    df_cr['policy_no'] = df_cr['policy_no'].str.strip()
    df_data['policy_no'] = df_data['policy_no'].str.strip()
    policies = df_cr['policy_no'].dropna().unique()
    grouped_data = df_data.groupby('policy_no')
    data_sheet = "Benefit" if use_benefit else "Query_result"
    os.makedirs(output_folder, exist_ok=True)
    tasks = {}
    for policy in policies:
        if policy not in grouped_data.groups: continue
        tasks[policy] = (df_cr[df_cr['policy_no'] == policy], grouped_data.get_group(policy))
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_write_policy, policy, cr_slice, data_slice, output_folder, data_sheet, report_period): policy for policy, (cr_slice, data_slice) in tasks.items()}
        for future in as_completed(futures): future.result()

# ==============================================================================
# UPLOAD CONSTANTS & HELPERS
# ==============================================================================
SCOPES = ["https://www.googleapis.com/auth/drive"]
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FOLDER_MIME = "application/vnd.google-apps.folder"
folder_lock = threading.Lock()
thread_local = threading.local()

def _build_service(service_account_info):
    creds = service_account.Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)

def _get_service(service_account_info):
    if not hasattr(thread_local, "service"): thread_local.service = _build_service(service_account_info)
    return thread_local.service

def _get_or_create_folder(service, name, parent_id, cache):
    key = (parent_id, name)
    if key in cache: return cache[key]
    with folder_lock:
        if key in cache: return cache[key]
        safe_name = name.replace("'", "\\'")
        query = f"name='{safe_name}' and mimeType='{FOLDER_MIME}' and '{parent_id}' in parents and trashed=false"
        res = service.files().list(q=query, fields="files(id)", pageSize=1, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files = res.get("files", [])
        if files: folder_id = files[0]["id"]
        else: folder_id = service.files().create(body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}, fields="id", supportsAllDrives=True).execute()["id"]
        cache[key] = folder_id
        return folder_id

def _upload_single_file(file_path, output_folder, drive_folder_id, service_account_info, folder_cache):
    try:
        service = _get_service(service_account_info)
        rel = os.path.relpath(file_path, output_folder)
        parts = rel.split(os.sep)
        parent_id = drive_folder_id
        for folder_name in parts[:-1]: parent_id = _get_or_create_folder(service, folder_name, parent_id, folder_cache)
        file_name = parts[-1]
        safe_name = file_name.replace("'", "\\'")
        query = f"name='{safe_name}' and '{parent_id}' in parents and trashed=false"
        res = service.files().list(q=query, fields="files(id)", pageSize=1, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files = res.get("files", [])
        media = MediaFileUpload(file_path, mimetype=XLSX_MIME, resumable=False)
        if files: service.files().update(fileId=files[0]["id"], media_body=media, supportsAllDrives=True).execute()
        else: service.files().create(body={"name": file_name, "parents": [parent_id]}, media_body=media, fields="id", supportsAllDrives=True).execute()
        return True, file_path, None
    except Exception as e: return False, file_path, e

def upload_output(output_folder, drive_folder_id, service_account_info, max_workers=20):
    _build_service(service_account_info)
    folder_cache = {}
    all_files = [os.path.join(root, fname) for root, _, files in os.walk(output_folder) for fname in files if fname.endswith(".xlsx")]
    if not all_files: return
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_upload_single_file, fp, output_folder, drive_folder_id, service_account_info, folder_cache): fp for fp in all_files}
        for future in concurrent.futures.as_completed(futures): future.result()

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================
# Use /tmp as it is typically writable in Docker environments
BASE_DIR = "/tmp/satria_data_report"
CONVERT_FOLDER = os.path.join(BASE_DIR, "convert_csv")
OUTPUT_FOLDER = os.path.join(BASE_DIR, "output")

try:
    fetch_from_metabase(CONVERT_FOLDER, use_benefit=USE_BENEFIT, report_period=REPORT_PERIOD, manual_policies=MANUAL_POLICIES)
    process_join(CONVERT_FOLDER, OUTPUT_FOLDER, use_benefit=USE_BENEFIT, report_period=REPORT_PERIOD)
    upload_output(OUTPUT_FOLDER, GOOGLE_DRIVE_FOLDER_ID, SERVICE_ACCOUNT_INFO)
    
    # Return success to n8n
    return [{"json": {"status": "success", "period": REPORT_PERIOD}}]
except Exception as e:
    # Return error to n8n
    return [{"json": {"status": "error", "message": str(e), "period": REPORT_PERIOD}}]
