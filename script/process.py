import os
import pandas as pd
import re
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed

from fetch import CR_FILENAME, QUERY_FILENAME, BENEFIT_FILENAME


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

    # Pull effective/renewal dates from the CR row for email table
    cr_row = df_cr_filtered.iloc[0]
    effective_date = str(cr_row.get('policy_effective_date', '')) if 'policy_effective_date' in df_cr_filtered.columns else ''
    renewal_date = str(cr_row.get('policy_renewal_date', '')) if 'policy_renewal_date' in df_cr_filtered.columns else ''

    return {
        "file_path": output_path,
        "policy_no": policy,
        "company_name": company_name,
        "source_name": str(source_name).strip(),
        "policy_effective_date": effective_date,
        "policy_renewal_date": renewal_date,
    }


def process_join(convert_folder, output_folder, master, report_period="Mei 2026"):
    """Write one Excel file per policy, routing each to the correct data source.

    Args:
        master: dict[policy_no] → {"need_report": "detail"|"header", ...}
                from sheets.read_master().  Used to decide which CSV to join per policy.
    """
    print(f"🚀 START PROCESSING CSV (PER POLICY)\n")

    # Load CR (always present)
    cr_path = os.path.join(convert_folder, CR_FILENAME)
    if not os.path.exists(cr_path):
        print("❌ File CR tidak ditemukan!")
        return []

    df_cr = _apply_numeric(
        pd.read_csv(cr_path, dtype=str),
        CR_NUMERIC,
    )
    df_cr['policy_no'] = df_cr['policy_no'].str.strip()

    # Load claim (header) data — dashboard 48 output
    query_path = os.path.join(convert_folder, QUERY_FILENAME)
    df_query = None
    grouped_query = None
    if os.path.exists(query_path):
        df_query = _apply_numeric(
            pd.read_csv(query_path, dtype=str),
            QUERY_NUMERIC,
        )
        df_query['policy_no'] = df_query['policy_no'].str.strip()
        grouped_query = df_query.groupby('policy_no')

    # Load benefit (detail) data — dashboard 38 output
    benefit_path = os.path.join(convert_folder, BENEFIT_FILENAME)
    df_benefit = None
    grouped_benefit = None
    if os.path.exists(benefit_path):
        df_benefit = _apply_numeric(
            pd.read_csv(benefit_path, dtype=str),
            QUERY_NUMERIC,
        )
        df_benefit['policy_no'] = df_benefit['policy_no'].str.strip()
        grouped_benefit = df_benefit.groupby('policy_no')

    policies = df_cr['policy_no'].dropna().unique()

    os.makedirs(output_folder, exist_ok=True)

    # Build tasks, routing each policy to the correct data source
    tasks = {}
    skipped = 0
    skipped_reasons = []

    for policy in policies:
        need_report = master.get(policy, {}).get("need_report", "")

        if need_report == "detail":
            if grouped_benefit is None or policy not in grouped_benefit.groups:
                skipped += 1
                skipped_reasons.append(f"{policy} (detail — no benefit rows)")
                continue
            tasks[policy] = (
                df_cr[df_cr['policy_no'] == policy],
                grouped_benefit.get_group(policy),
                "Benefit",
            )
        elif need_report == "header":
            if grouped_query is None or policy not in grouped_query.groups:
                skipped += 1
                skipped_reasons.append(f"{policy} (header — no query rows)")
                continue
            tasks[policy] = (
                df_cr[df_cr['policy_no'] == policy],
                grouped_query.get_group(policy),
                "Query_result",
            )
        else:
            # Policy is in CR but not in master sheet (or no routing value) → skip
            skipped += 1
            skipped_reasons.append(f"{policy} (not in master / no routing)")
            continue

    print(f"📊 Total policy (CR): {len(policies)}  |  To write: {len(tasks)}  |  Skipped: {skipped}\n")

    success = 0
    errors = 0
    written_files: list[dict] = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(
                _write_policy, policy, cr_slice, data_slice, output_folder, data_sheet, report_period
            ): policy
            for policy, (cr_slice, data_slice, data_sheet) in tasks.items()
        }
        with tqdm(total=len(futures), desc="Writing", unit="policy") as pbar:
            for future in as_completed(futures):
                policy = futures[future]
                try:
                    written_files.append(future.result())
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

    return written_files
