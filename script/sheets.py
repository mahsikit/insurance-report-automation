import re
from googleapiclient.discovery import build


def _parse_emails(raw: str) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    return [e.strip() for e in re.split(r"[,;]+", str(raw).strip()) if e.strip()]


def read_recipients(credentials, spreadsheet_id, sheet_name):
    """Read the full recipient map from the master sheet without writing anything.

    Returns dict[policy_no] → {"to": [str], "cc": [str]} for all rows.
    """
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A:H",
    ).execute()
    rows = result.get("values", [])

    recipient_map: dict[str, dict] = {}
    for i, row in enumerate(rows):
        row_number = i + 1
        if row_number == 1:
            continue
        h_val = row[7].strip() if len(row) > 7 else ""
        if not h_val:
            continue
        if h_val in recipient_map:
            continue
        recipient_map[h_val] = {
            "to": _parse_emails(row[5] if len(row) > 5 else ""),
            "cc": _parse_emails(row[6] if len(row) > 6 else ""),
        }
    return recipient_map


def update_master_sheet(credentials, spreadsheet_id, sheet_name, upload_results, as_of):
    """Update columns D and E in the master sheet for each uploaded policy.

    Returns dict[policy_no] → {"to": [str], "cc": [str]} built from columns F and G.
    """
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    sheets = service.spreadsheets()

    # Read A:H in one call — column A has data in row 1 so leading empty rows are
    # never trimmed, meaning rows[i] always corresponds to spreadsheet row i+1.
    result = sheets.values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A:H",
    ).execute()
    rows = result.get("values", [])

    # Build lookups from the single read
    policy_row: dict[str, int] = {}
    recipient_map: dict[str, dict] = {}

    for i, row in enumerate(rows):
        row_number = i + 1
        if row_number == 1:
            continue  # skip header row
        h_val = row[7].strip() if len(row) > 7 else ""
        if not h_val:
            continue
        if h_val in policy_row:
            continue  # keep first occurrence
        policy_row[h_val] = row_number
        recipient_map[h_val] = {
            "to": _parse_emails(row[5] if len(row) > 5 else ""),
            "cc": _parse_emails(row[6] if len(row) > 6 else ""),
        }

    if not policy_row:
        print("⚠️  No policy rows found in master sheet column H — skipping sheet update.")
        return {}

    # Build batch update for columns D and E of matched rows
    data = []
    matched = 0
    unmatched = []

    for policy_no, info in upload_results.items():
        if policy_no not in policy_row:
            unmatched.append(policy_no)
            continue
        row_num = policy_row[policy_no]
        # D{row} = webViewLink, E{row} = as_of
        data.append({
            "range": f"'{sheet_name}'!D{row_num}:E{row_num}",
            "values": [[info["web_view_link"], as_of]],
        })
        matched += 1

    if data:
        sheets.values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()

    print(f"\n📊 MASTER SHEET UPDATE SELESAI!")
    print(f"✅ Updated  : {matched} rows")
    if unmatched:
        print(f"⚠️  No sheet row for: {', '.join(unmatched)}")

    # Return only the recipient entries for policies we actually uploaded
    return {p: recipient_map[p] for p in upload_results if p in recipient_map}
