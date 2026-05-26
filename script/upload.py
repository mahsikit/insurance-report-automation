import os
import time
import threading
import concurrent.futures
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from tqdm import tqdm

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FOLDER_MIME = "application/vnd.google-apps.folder"

folder_lock = threading.Lock()
thread_local = threading.local()


def _build_drive_service(credentials):
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _get_drive_service(credentials):
    if not hasattr(thread_local, "drive_service"):
        thread_local.drive_service = _build_drive_service(credentials)
    return thread_local.drive_service


def _get_or_create_folder(service, name, parent_id, cache):
    key = (parent_id, name)

    if key in cache:
        return cache[key]

    with folder_lock:
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


def _upload_single_file(file_path, output_folder, drive_folder_id, credentials, folder_cache, extra_meta=None):
    try:
        service = _get_drive_service(credentials)
        rel = os.path.relpath(file_path, output_folder)
        parts = rel.split(os.sep)

        # parts: [source_name, company_name, policy_no, filename]
        policy_no = parts[-2] if len(parts) >= 2 else ""

        parent_id = drive_folder_id
        for folder_name in parts[:-1]:
            parent_id = _get_or_create_folder(service, folder_name, parent_id, folder_cache)

        file_name = parts[-1]
        safe_name = file_name.replace("'", "\\'")
        query = f"name='{safe_name}' and '{parent_id}' in parents and trashed=false"

        res = service.files().list(
            q=query, fields="files(id)", pageSize=1,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        existing = res.get("files", [])

        media = MediaFileUpload(file_path, mimetype=XLSX_MIME, resumable=False)

        if existing:
            file_id = existing[0]["id"]
            service.files().update(
                fileId=file_id,
                media_body=media,
                supportsAllDrives=True,
            ).execute()
        else:
            result = service.files().create(
                body={"name": file_name, "parents": [parent_id]},
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            ).execute()
            file_id = result.get("id", "")

        web_view_link = f"https://drive.google.com/file/d/{file_id}/view" if file_id else ""
        return True, policy_no, file_id, web_view_link, file_path, extra_meta, None
    except (HttpError, OSError) as e:
        return False, "", "", "", file_path, extra_meta, e


def upload_output(output_folder, drive_folder_id, credentials, specific_files=None, max_workers=20):  # Drive API write quota (~10 QPS/user) is the real ceiling, not thread count
    """Upload .xlsx files and return a dict keyed by policy_no.

    If specific_files is provided, only those files are uploaded.
    Otherwise falls back to walking the entire output_folder.
    """
    start_time = time.time()
    print("☁️  Connecting to Google Drive...")
    _build_drive_service(credentials)
    folder_cache = {}

    # specific_files may be a list of dicts {file_path, policy_no, company_name, source_name}
    # or a plain list of file paths (fallback).
    meta_map: dict[str, dict] = {}
    if specific_files is not None:
        all_files = []
        for item in specific_files:
            if isinstance(item, dict):
                fp = item.get("file_path", "")
                if fp and fp.endswith(".xlsx"):
                    all_files.append(fp)
                    meta_map[fp] = item
            elif isinstance(item, str) and item.endswith(".xlsx"):
                all_files.append(item)
    else:
        for root, _, files in os.walk(output_folder):
            for fname in files:
                if fname.endswith(".xlsx"):
                    all_files.append(os.path.join(root, fname))

    if not all_files:
        print("   ⚠️  No files to upload.")
        return {}

    print(f"   ✅ {len(all_files)} files to upload\n")

    uploaded = 0
    errors = 0
    # policy_no → {file_id, web_view_link, file_path}
    upload_results: dict[str, dict] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _upload_single_file,
                file_path,
                output_folder,
                drive_folder_id,
                credentials,
                folder_cache,
                meta_map.get(file_path),
            ): file_path for file_path in all_files
        }

        for future in tqdm(concurrent.futures.as_completed(futures), total=len(all_files), desc="Uploading", unit="file"):
            success, policy_no, file_id, web_view_link, file_path, extra_meta, err = future.result()
            if success:
                uploaded += 1
                upload_results[policy_no] = {
                    "file_id": file_id,
                    "web_view_link": web_view_link,
                    "file_path": file_path,
                    "company_name": (extra_meta or {}).get("company_name", ""),
                    "source_name": (extra_meta or {}).get("source_name", ""),
                }
            else:
                errors += 1
                tqdm.write(f"❌ {file_path}: {err}")

    end_time = time.time()
    print("\n☁️  UPLOAD SELESAI!")
    print(f"✅ Uploaded : {uploaded}")
    if errors:
        print(f"❌ Errors   : {errors}")
    print(f"⏱️  Duration : {end_time - start_time:.2f} seconds")

    return upload_results
