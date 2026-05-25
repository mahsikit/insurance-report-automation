import os

with open("n8n_node_fetch.py", "r") as f:
    content = f.read()

import re

# 1. Add BASE_DIR back to fetch
replacement = """
    base_url = METABASE_URL.rstrip("/")
    
    BASE_DIR = "/tmp/satria_data_report"
    CONVERT_FOLDER = os.path.join(BASE_DIR, "convert_csv")
    os.makedirs(CONVERT_FOLDER, exist_ok=True)
"""
content = content.replace('    base_url = METABASE_URL.rstrip("/")', replacement)

# 2. Write files and don't pass csv in json
replacement2 = """
    with open(os.path.join(CONVERT_FOLDER, CR_FILENAME), "w", encoding="utf-8") as f: f.write(cr_csv)
    with open(os.path.join(CONVERT_FOLDER, QUERY_FILENAME), "w", encoding="utf-8") as f: f.write(dashboard_csv)

    output_data = n8n_input.copy()
    output_data.update({
        "status": "fetch_success", 
        "REPORT_PERIOD": REPORT_PERIOD, 
        "USE_BENEFIT": USE_BENEFIT
    })
    return [{"json": output_data}]
"""
content = re.sub(r'    # Bypassing file system completely! Pass raw CSV data in memory to the next node.*    return \[\{"json": output_data\}\]', replacement2, content, flags=re.DOTALL)

with open("n8n_node_fetch.py", "w") as f:
    f.write(content)

# Patch Process node
with open("n8n_node_process.py", "r") as f:
    content_proc = f.read()

replacement_proc1 = """
BASE_DIR = "/tmp/satria_data_report"
CONVERT_FOLDER = os.path.join(BASE_DIR, "convert_csv")
OUTPUT_FOLDER = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
"""
content_proc = re.sub(r'import io\nimport base64', replacement_proc1, content_proc)

replacement_proc2 = """
def _detect_files(folder):
    cr_file = data_file = None
    if not os.path.exists(folder): return None, None
    for fname in os.listdir(folder):
        if not fname.endswith(".csv"): continue
        header = pd.read_csv(os.path.join(folder, fname), nrows=0).columns.tolist()
        if "Loss Ratio by GWP before Disc" in header or "gross_written_premium_before_disc" in header: cr_file = fname
        elif "claims_id" in header: data_file = fname
    return cr_file, data_file

def _write_policy(policy, df_cr_filtered, df_data_filtered, data_sheet, report_period):
    company_name = str(df_cr_filtered.iloc[0]['company_name'])
    source_name = df_data_filtered.iloc[0].get('source_name', '')
    broker = re.sub(r'[\\\\/:*?"<>|]', '', str(source_name).strip()) if source_name and str(source_name).strip() not in ("", "nan") else "UNKNOWN"
    safe_company = re.sub(r'[\\\\/:*?"<>|]', '', company_name).strip()
    
    policy_dir = os.path.join(OUTPUT_FOLDER, broker, safe_company, policy)
    os.makedirs(policy_dir, exist_ok=True)
    out_path = os.path.join(policy_dir, f"Report Claim - {report_period} - {safe_company}_{policy}.xlsx")
    
    with pd.ExcelWriter(out_path, engine='xlsxwriter') as writer:
        df_cr_filtered.to_excel(writer, sheet_name="CR", index=False)
        df_data_filtered.to_excel(writer, sheet_name=data_sheet, index=False)
        
    return {"status": "ok"}
"""
content_proc = re.sub(r'def _write_policy.*?return {"filename": filename, "folder_path": folder_path, "data": b64_data}', replacement_proc2, content_proc, flags=re.DOTALL)

replacement_proc3 = """
    cr_file, data_file = _detect_files(CONVERT_FOLDER)
    if not cr_file or not data_file:
        raise FileNotFoundError("Could not find CR or Data CSV files in /tmp. Did the Fetch node succeed?")
        
    df_cr = _apply_numeric(pd.read_csv(os.path.join(CONVERT_FOLDER, cr_file), dtype=str), CR_NUMERIC)
    df_data = _apply_numeric(pd.read_csv(os.path.join(CONVERT_FOLDER, data_file), dtype=str), QUERY_NUMERIC)
"""
content_proc = re.sub(r'    cr_csv = n8n_input\.get\("cr_csv"\).*?df_data = _apply_numeric\(pd\.read_csv\(io\.StringIO\(dashboard_csv\), dtype=str\), QUERY_NUMERIC\)', replacement_proc3, content_proc, flags=re.DOTALL)

replacement_proc4 = """
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_write_policy, p, cr_slice, data_slice, data_sheet, REPORT_PERIOD) for p, (cr_slice, data_slice) in tasks.items()]
        for future in as_completed(futures): future.result()

    output_data = n8n_input.copy()
    output_data.update({"status": "process_success", "total_processed": len(tasks)})
    return [{"json": output_data}]
"""
content_proc = re.sub(r'    output_files = \[\]\n    with ThreadPoolExecutor.*?return \[\{"json": output_data\}\]', replacement_proc4, content_proc, flags=re.DOTALL)

with open("n8n_node_process.py", "w") as f:
    f.write(content_proc)

# Patch Upload node
with open("n8n_node_upload.py", "r") as f:
    content_up = f.read()

replacement_up1 = """
BASE_DIR = "/tmp/satria_data_report"
OUTPUT_FOLDER = os.path.join(BASE_DIR, "output")
"""
content_up = content_up.replace("import io\nimport base64", replacement_up1)

replacement_up2 = """
def _upload_single_file(file_path, output_folder, drive_folder_id, folder_cache):
    try:
        service = _get_service()
        rel = os.path.relpath(file_path, output_folder)
        parts = rel.split(os.sep)
        parent_id = drive_folder_id
        for folder_name in parts[:-1]: parent_id = _get_or_create_folder(service, folder_name, parent_id, folder_cache)
        file_name = parts[-1]
        safe_name = file_name.replace("'", "\\\\'")
        query = f"name='{safe_name}' and '{parent_id}' in parents and trashed=false"
        res = service.files().list(q=query, fields="files(id)", pageSize=1, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files = res.get("files", [])
        
        media = MediaFileUpload(file_path, mimetype=XLSX_MIME, resumable=False)
        
        if files: service.files().update(fileId=files[0]["id"], media_body=media, supportsAllDrives=True).execute()
        else: service.files().create(body={"name": file_name, "parents": [parent_id]}, media_body=media, fields="id", supportsAllDrives=True).execute()
        return True, file_path, None
    except Exception as e: return False, file_path, e
"""
content_up = re.sub(r'def _upload_single_file\(file_obj, drive_folder_id, folder_cache\):.*?except Exception as e: return False, file_name, e', replacement_up2, content_up, flags=re.DOTALL)

replacement_up3 = """
    if not os.path.exists(OUTPUT_FOLDER):
        raise FileNotFoundError(f"Could not find {OUTPUT_FOLDER}. Did the Process node succeed?")
        
    _build_service()
    folder_cache = {}
    all_files = [os.path.join(root, fname) for root, _, files in os.walk(OUTPUT_FOLDER) for fname in files if fname.endswith(".xlsx")]
    
    if not all_files:
        raise ValueError("No .xlsx files found in output directory to upload.")
        
    uploaded = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(_upload_single_file, fp, OUTPUT_FOLDER, GOOGLE_DRIVE_FOLDER_ID, folder_cache) for fp in all_files]
        for future in concurrent.futures.as_completed(futures):
            if future.result()[0]: uploaded += 1

    output_data = n8n_input.copy()
    output_data.update({"status": "upload_success", "files_uploaded": uploaded})
"""
content_up = re.sub(r'    _build_service\(\)\n    folder_cache = \{\}.*?output_data\.update\(\{"status": "upload_success", "files_uploaded": uploaded\}\)', replacement_up3, content_up, flags=re.DOTALL)

with open("n8n_node_upload.py", "w") as f:
    f.write(content_up)

