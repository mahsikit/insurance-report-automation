import os
import pandas as pd
import re
from tqdm import tqdm

# 🔥 SET MANUAL REPORT PERIOD DI SINI
REPORT_PERIOD = "Mei 2026"

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
    """Derive a short broker directory name from source_name.

    'PT ANDIKA MITRA SEJATI (EB HEALTH)' → 'ANDIKA'
    Add an explicit mapping dict here if multiple brokers need custom names.
    """
    if not source_name or str(source_name).strip() in ("", "nan"):
        return "UNKNOWN"
    name = str(source_name).strip()
    if name.upper().startswith("PT "):
        name = name[3:].strip()
    name = re.sub(r'\s*\(.*?\)\s*$', '', name).strip()
    token = name.split()[0] if name else "UNKNOWN"
    return token.upper()


def process_join(convert_folder, output_folder, use_benefit=False):
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

    os.makedirs(output_folder, exist_ok=True)

    total = len(policies)
    success = 0
    skipped = 0

    print(f"📊 Total policy: {total}\n")

    for policy in tqdm(policies, desc="Processing", unit="policy"):
        try:
            df_cr_filtered = df_cr[df_cr['policy_no'] == policy]

            if policy not in grouped_data.groups:
                skipped += 1
                continue

            df_data_filtered = grouped_data.get_group(policy)

            company_name = str(df_cr_filtered.iloc[0]['company_name'])
            safe_name = re.sub(r'[^\w\s-]', '', company_name)[:50]

            source_name = df_data_filtered.iloc[0].get('source_name', '')
            broker = broker_folder(source_name)

            broker_dir = os.path.join(output_folder, broker)
            os.makedirs(broker_dir, exist_ok=True)

            output_file = f"Report Claim - {REPORT_PERIOD} - {safe_name}_{policy}.xlsx"
            output_path = os.path.join(broker_dir, output_file)

            data_sheet = "Benefit" if use_benefit else "Query_result"
            with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
                df_cr_filtered.to_excel(writer, sheet_name="CR", index=False)
                df_data_filtered.to_excel(writer, sheet_name=data_sheet, index=False)

            success += 1

        except Exception as e:
            print(f"\n❌ Error di policy {policy}: {e}")

    print("\n🎯 SELESAI!")
    print(f"✅ Success : {success}")
    print(f"⚠️ Skipped : {skipped}")
    print(f"📦 Output  : {output_folder}")
