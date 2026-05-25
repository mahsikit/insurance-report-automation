import os
import threading
import concurrent.futures
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ==============================================================================
# ⚙️ NODE 3: UPLOAD TO GOOGLE DRIVE
# ==============================================================================
n8n_input = {}
try:
    # 'Run Once for All Items' mode — _items is a list of plain dicts
    if _items:
        n8n_input = _items[0]["json"]
except (NameError, AttributeError, IndexError, KeyError):
    pass
if not n8n_input:
    try:
        # 'Run Once for Each Item' mode
        n8n_input = _item["json"]
    except (NameError, AttributeError, KeyError):
        pass

GOOGLE_DRIVE_FOLDER_ID = n8n_input.get("GOOGLE_DRIVE_FOLDER_ID", "your_drive_folder_id")

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


BASE_DIR = "/tmp/satria_data_report"
OUTPUT_FOLDER = os.path.join(BASE_DIR, "output")


SCOPES = ["https://www.googleapis.com/auth/drive"]
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FOLDER_MIME = "application/vnd.google-apps.folder"
folder_lock = threading.Lock()
thread_local = threading.local()

def _build_service():
    creds = service_account.Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)

def _get_service():
    if not hasattr(thread_local, "service"): thread_local.service = _build_service()
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


def _upload_single_file(file_path, output_folder, drive_folder_id, folder_cache):
    try:
        service = _get_service()
        rel = os.path.relpath(file_path, output_folder)
        parts = rel.split(os.sep)
        parent_id = drive_folder_id
        for folder_name in parts[:-1]: parent_id = _get_or_create_folder(service, folder_name, parent_id, folder_cache)
        file_name = parts[-1]
        safe_name = file_name.replace("'", "\'")
        query = f"name='{safe_name}' and '{parent_id}' in parents and trashed=false"
        res = service.files().list(q=query, fields="files(id)", pageSize=1, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files = res.get("files", [])
        
        media = MediaFileUpload(file_path, mimetype=XLSX_MIME, resumable=False)
        
        if files: service.files().update(fileId=files[0]["id"], media_body=media, supportsAllDrives=True).execute()
        else: service.files().create(body={"name": file_name, "parents": [parent_id]}, media_body=media, fields="id", supportsAllDrives=True).execute()
        return True, file_path, None
    except Exception as e: return False, file_path, e


try:

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

    return [{"json": output_data}]
except Exception as e:
    return [{"json": {"status": "error", "step": "upload", "message": str(e)}}]
