import re
from googleapiclient.discovery import build


def _parse_emails(raw: str) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    return [e.strip() for e in re.split(r"[,;]+", str(raw).strip()) if e.strip()]


def read_master(credentials, spreadsheet_id, sheet_name):
    """Read the FINAL tab and return a routing + recipient map for every policy.

    FINAL tab column layout (load-bearing — do not reorganise):
        C  — need_report   : 'detail' → dashboard 38 (benefit), 'header' → dashboard 48
        E  — policy_no     : match key
        F  — To            : email recipients
        G  — CC            : email recipients
        J  — Drive link    : written by write_links()
        K  — Latest As Of  : written by write_links()

    Returns:
        dict[policy_no] → {
            "to":          list[str],
            "cc":          list[str],
            "need_report": "detail" | "header",
            "row":         int,   # 1-based spreadsheet row number
        }
    Rows with a blank column C or blank column E are skipped.
    First occurrence of each policy_no is kept; duplicates are ignored.
    """
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A:K",
    ).execute()
    rows = result.get("values", [])

    master: dict[str, dict] = {}
    for i, row in enumerate(rows):
        row_number = i + 1
        if row_number == 1:
            continue  # skip header

        need_report = row[2].strip().lower() if len(row) > 2 else ""
        if not need_report:
            continue  # skip rows with no routing value

        policy_no = row[4].strip() if len(row) > 4 else ""
        if not policy_no:
            continue
        if policy_no in master:
            continue  # first occurrence only

        master[policy_no] = {
            "to":          _parse_emails(row[5] if len(row) > 5 else ""),
            "cc":          _parse_emails(row[6] if len(row) > 6 else ""),
            "need_report": need_report,
            "row":         row_number,
            "e_date":      row[8].strip() if len(row) > 8 else "",
        }

    return master


def sync_new_policies(credentials, spreadsheet_id, sheet_name, active_policies):
    """Append rows for active policies that are not yet in the master sheet.

    active_policies: list of dicts from fetch.fetch_active_policies_full()

    Reads column E directly (all rows, regardless of column C) so that policies
    added in a previous sync (but not yet assigned a routing value) are not
    duplicated.

    New rows are written with:
        A = company_name
        B = source_name
        E = policy_no
        H = policy_effective_date  (s_date)
        I = policy_renewal_date    (e_date)
    Columns C (need_report), D, F, G, J, K are left blank for the user to fill in.

    Returns list of policy_no strings that were newly added.
    """
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    # Read column E (policy_no) for ALL rows — skip header
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!E:E",
    ).execute()
    existing = {
        row[0].strip()
        for row in result.get("values", [])[1:]  # skip header row
        if row and row[0].strip()
    }

    new_rows = []
    added = []
    for rec in active_policies:
        pno = rec.get("policy_no", "").strip()
        if not pno or pno in existing:
            continue
        company     = rec.get("company_name", "").strip()
        source_name = rec.get("source_name", "").strip()
        eff         = rec.get("policy_effective_date", "").strip()
        ren         = rec.get("policy_renewal_date", "").strip()
        # Row covers A:I (9 columns); blank cols C (need_report), D, F, G
        row = [company, source_name, "", "", pno, "", "", eff, ren]
        new_rows.append(row)
        added.append(pno)

    if new_rows:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_name}'!A:I",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": new_rows},
        ).execute()
        print(f"   ➕ Added {len(added)} new policies to master sheet: {', '.join(added)}")
    else:
        print("   ✅ No new policies — master sheet is up to date.")

    return added


def write_links(credentials, spreadsheet_id, sheet_name, upload_results, as_of, master):
    """Batch-write Drive links (col J) and as_of (col K) for uploaded policies.

    Args:
        upload_results: dict[policy_no] → {web_view_link, ...} from upload.py
        as_of:          string in YYYY-MM form (e.g. '2026-05')
        master:         dict returned by read_master() — used for row numbers
    """
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    sheets = service.spreadsheets()

    data = []
    matched = 0
    unmatched = []

    for policy_no, info in upload_results.items():
        if policy_no not in master:
            unmatched.append(policy_no)
            continue
        row_num = master[policy_no]["row"]
        # J{row} = web_view_link, K{row} = as_of
        data.append({
            "range": f"'{sheet_name}'!J{row_num}:K{row_num}",
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
