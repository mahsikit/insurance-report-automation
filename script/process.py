import os
import pandas as pd
import re
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed


CR_NUMERIC = [
    "gross_written_premium_before_disc", "gross_earned_premium_before_disc",
    "policy_age_pcnt", "approval_amount", "active_insureds", "active_employees",
    "number_claim_submission", "Loss Ratio by GWP before Disc", "Loss Ratio by GEP before Disc",
]
QUERY_NUMERIC = ["los", "incurred", "approved", "excess", "excess_paid_by_member", "age"]


def _apply_numeric(df, cols):
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _detect_files(convert_folder):
    """Identify the CR and query CSV files by their column headers."""
    cr_file = None
    data_file = None
    for fname in os.listdir(convert_folder):
        if not fname.endswith(".csv"):
            continue
        path = os.path.join(convert_folder, fname)
        header = pd.read_csv(path, nrows=0).columns.tolist()
        if "Loss Ratio by GWP before Disc" in header or "gross_written_premium_before_disc" in header:
            cr_file = fname
        elif "claims_id" in header:
            data_file = fname
    return cr_file, data_file


def broker_folder(source_name):
    """Use the full source_name as the broker subfolder, sanitized for the filesystem.

    'PT ANDIKA MITRA SEJATI (EB HEALTH)' → 'PT ANDIKA MITRA SEJATI (EB HEALTH)'
    """
    if not source_name or str(source_name).strip() in ("", "nan"):
        return "UNKNOWN"
    name = str(source_name).strip()
    # Remove characters that are invalid in directory names on most OSes
    name = re.sub(r'[\\/:*?"<>|]', '', name)
    return name


def _write_policy(policy, df_cr_filtered, df_data_filtered, output_folder, data_sheet, report_period):
    company_name = str(df_cr_filtered.iloc[0]['company_name'])
    source_name = df_data_filtered.iloc[0].get('source_name', '')
    broker = broker_folder(source_name)

    safe_company = re.sub(r'[\\/:*?"<>|]', '', company_name).strip()

    policy_dir = os.path.join(output_folder, broker, safe_company, policy)
    os.makedirs(policy_dir, exist_ok=True)

    output_file = f"Report Claim - {report_period} - {safe_company}_{policy}.xlsx"
    output_path = os.path.join(policy_dir, output_file)

    with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
        df_cr_filtered.to_excel(writer, sheet_name="CR", index=False)
        df_data_filtered.to_excel(writer, sheet_name=data_sheet, index=False)


def process_join(convert_folder, output_folder, use_benefit=False, report_period="Mei 2026"):
    mode_label = "BENEFIT LEVEL" if use_benefit else "CLAIM LEVEL"
    print(f"🚀 START PROCESSING CSV (PER POLICY) — {mode_label}\n")

    cr_file, data_file = _detect_files(convert_folder)

    if not cr_file or not data_file:
        print("❌ File CR atau Query Result tidak ditemukan!")
        return

    df_cr = _apply_numeric(
        pd.read_csv(os.path.join(convert_folder, cr_file), dtype=str),
        CR_NUMERIC,
    )
    df_data = _apply_numeric(
        pd.read_csv(os.path.join(convert_folder, data_file), dtype=str),
        QUERY_NUMERIC,
    )

    df_cr['policy_no'] = df_cr['policy_no'].str.strip()
    df_data['policy_no'] = df_data['policy_no'].str.strip()

    policies = df_cr['policy_no'].dropna().unique()
    grouped_data = df_data.groupby('policy_no')
    data_sheet = "Benefit" if use_benefit else "Query_result"

    os.makedirs(output_folder, exist_ok=True)

    # Split policies into writable tasks vs skips up front
    tasks = {}
    skipped = 0
    for policy in policies:
        if policy not in grouped_data.groups:
            skipped += 1
            continue
        tasks[policy] = (
            df_cr[df_cr['policy_no'] == policy],
            grouped_data.get_group(policy),
        )

    print(f"📊 Total policy: {len(policies)}  |  To write: {len(tasks)}  |  Skipped: {skipped}\n")

    success = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_write_policy, policy, cr_slice, data_slice, output_folder, data_sheet, report_period): policy
            for policy, (cr_slice, data_slice) in tasks.items()
        }
        with tqdm(total=len(futures), desc="Writing", unit="policy") as pbar:
            for future in as_completed(futures):
                policy = futures[future]
                try:
                    future.result()
                    success += 1
                except Exception as e:
                    errors += 1
                    tqdm.write(f"❌ Error di policy {policy}: {e}")
                pbar.update(1)

    print("\n🎯 SELESAI!")
    print(f"✅ Success : {success}")
    print(f"⚠️ Skipped : {skipped}")
    if errors:
        print(f"❌ Errors  : {errors}")
    print(f"📦 Output  : {output_folder}")
