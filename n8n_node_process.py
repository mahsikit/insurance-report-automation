import os
import pandas as pd
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==============================================================================
# ⚙️ NODE 2: PROCESS & GENERATE EXCEL
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

raw_use_benefit = n8n_input.get("USE_BENEFIT", False)
USE_BENEFIT = str(raw_use_benefit).strip().lower() in ("true", "1", "yes")
REPORT_PERIOD = n8n_input.get("REPORT_PERIOD", "Unknown Period")


BASE_DIR = "/tmp/satria_data_report"
CONVERT_FOLDER = os.path.join(BASE_DIR, "convert_csv")
OUTPUT_FOLDER = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_FOLDER, exist_ok=True)


CR_NUMERIC = ["gross_written_premium_before_disc", "gross_earned_premium_before_disc", "policy_age_pcnt", "approval_amount", "active_insureds", "active_employees", "number_claim_submission", "Loss Ratio by GWP before Disc", "Loss Ratio by GEP before Disc"]
QUERY_NUMERIC = ["los", "incurred", "approved", "excess", "excess_paid_by_member", "age"]

def _apply_numeric(df, cols):
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


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
    broker = re.sub(r'[\/:*?"<>|]', '', str(source_name).strip()) if source_name and str(source_name).strip() not in ("", "nan") else "UNKNOWN"
    safe_company = re.sub(r'[\/:*?"<>|]', '', company_name).strip()
    
    policy_dir = os.path.join(OUTPUT_FOLDER, broker, safe_company, policy)
    os.makedirs(policy_dir, exist_ok=True)
    out_path = os.path.join(policy_dir, f"Report Claim - {report_period} - {safe_company}_{policy}.xlsx")
    
    with pd.ExcelWriter(out_path, engine='xlsxwriter') as writer:
        df_cr_filtered.to_excel(writer, sheet_name="CR", index=False)
        df_data_filtered.to_excel(writer, sheet_name=data_sheet, index=False)
        
    return {"status": "ok"}


try:


    cr_file, data_file = _detect_files(CONVERT_FOLDER)
    if not cr_file or not data_file:
        raise FileNotFoundError("Could not find CR or Data CSV files in /tmp. Did the Fetch node succeed?")
        
    def safe_read(filepath):
        fd = os.open(filepath, os.O_RDONLY)
        data = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk: break
            data.append(chunk)
        os.close(fd)
        return b"".join(data).decode('utf-8')

    cr_csv_text = safe_read(os.path.join(CONVERT_FOLDER, cr_file))
    dashboard_csv_text = safe_read(os.path.join(CONVERT_FOLDER, data_file))

    df_cr = _apply_numeric(pd.read_csv(io.StringIO(cr_csv_text), dtype=str), CR_NUMERIC)
    df_data = _apply_numeric(pd.read_csv(io.StringIO(dashboard_csv_text), dtype=str), QUERY_NUMERIC)


    df_cr['policy_no'] = df_cr['policy_no'].str.strip()
    df_data['policy_no'] = df_data['policy_no'].str.strip()
    
    policies = df_cr['policy_no'].dropna().unique()
    grouped_data = df_data.groupby('policy_no')
    data_sheet = "Benefit" if USE_BENEFIT else "Query_result"
    
    tasks = {p: (df_cr[df_cr['policy_no'] == p], grouped_data.get_group(p)) for p in policies if p in grouped_data.groups}
    

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_write_policy, p, cr_slice, data_slice, data_sheet, REPORT_PERIOD) for p, (cr_slice, data_slice) in tasks.items()]
        for future in as_completed(futures): future.result()

    output_data = n8n_input.copy()
    output_data.update({"status": "process_success", "total_processed": len(tasks)})
    return [{"json": output_data}]

except Exception as e:
    return [{"json": {"status": "error", "step": "process", "message": str(e)}}]
