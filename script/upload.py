import os
import time
import threading
import concurrent.futures
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from tqdm import tqdm

SCOPES = ["https://www.googleapis.com/auth/drive"]
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FOLDER_MIME = "application/vnd.google-apps.folder"

folder_lock = threading.Lock()
thread_local = threading.local()


def _build_service(service_account_file):
    creds = service_account.Credentials.from_service_account_file(
        service_account_file, scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _get_service(service_account_file):
    if not hasattr(thread_local, "service"):
        thread_local.service = _build_service(service_account_file)
    return thread_local.service


def _get_or_create_folder(service, name, parent_id, cache):
    key = (parent_id, name)
    
    # Fast check without lock
    if key in cache:
        return cache[key]

    with folder_lock:
        # Double check after acquiring lock
        if key in cache:
            return cache[key]

        safe_name = name.replace("'", "\\'")
        query = (
            f"name='{safe_name}' and mimeType='{FOLDER_MIME}' "
            f"and '{parent_id}' in parents and trashed=false"
        )
        res = service.files().list(
            q=query, fields="files(id)", pageSize=1,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        files = res.get("files", [])

        if files:
            folder_id = files[0]["id"]
        else:
            meta = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
            folder_id = service.files().create(
                body=meta, fields="id", supportsAllDrives=True
            ).execute()["id"]

        cache[key] = folder_id
        return folder_id


def _upload_single_file(file_path, output_folder, drive_folder_id, service_account_file, folder_cache):
    try:
        service = _get_service(service_account_file)
        rel = os.path.relpath(file_path, output_folder)
        parts = rel.split(os.sep)

        parent_id = drive_folder_id
        for folder_name in parts[:-1]:
            parent_id = _get_or_create_folder(service, folder_name, parent_id, folder_cache)

        file_name = parts[-1]
        safe_name = file_name.replace("'", "\\'")
        query = f"name='{safe_name}' and '{parent_id}' in parents and trashed=false"
        
        # Check if file exists
        res = service.files().list(
            q=query, fields="files(id)", pageSize=1,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        files = res.get("files", [])

        media = MediaFileUpload(file_path, mimetype=XLSX_MIME, resumable=False)
        
        if files:
            # File exists, update it
            file_id = files[0]["id"]
            service.files().update(
                fileId=file_id,
                media_body=media,
                supportsAllDrives=True,
            ).execute()
        else:
            # File does not exist, create it
            service.files().create(
                body={"name": file_name, "parents": [parent_id]},
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            ).execute()
            
        return True, file_path, None
    except Exception as e:
        return False, file_path, e


def upload_output(output_folder, drive_folder_id, service_account_file, max_workers=20):
    start_time = time.time()
    print("☁️  Connecting to Google Drive...")
    # Initialize main service to verify connection early
    _build_service(service_account_file)
    folder_cache = {}

    all_files = []
    for root, _, files in os.walk(output_folder):
        for fname in files:
            if fname.endswith(".xlsx"):
                all_files.append(os.path.join(root, fname))

    if not all_files:
        print("   ⚠️  No files to upload.")
        return

    print(f"   ✅ {len(all_files)} files to upload\n")

    uploaded = 0
    errors = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _upload_single_file, 
                file_path, 
                output_folder, 
                drive_folder_id, 
                service_account_file, 
                folder_cache
            ): file_path for file_path in all_files
        }

        for future in tqdm(concurrent.futures.as_completed(futures), total=len(all_files), desc="Uploading", unit="file"):
            success, file_path, err = future.result()
            if success:
                uploaded += 1
            else:
                errors += 1
                tqdm.write(f"❌ {file_path}: {err}")

    end_time = time.time()
    duration = end_time - start_time
    print("\n☁️  UPLOAD SELESAI!")
    print(f"✅ Uploaded : {uploaded}")
    if errors:
        print(f"❌ Errors   : {errors}")
    print(f"⏱️  Duration : {duration:.2f} seconds")
