import os
import requests
import calendar
from concurrent.futures import ThreadPoolExecutor

CR_FILENAME = "claim_ratio_CR.csv"
QUERY_FILENAME = "query_result_Query_result.csv"   # dashboard 48 (claim history)
BENEFIT_FILENAME = "query_result_Benefit.csv"      # dashboard 38 (benefit detail)

INDONESIAN_MONTHS = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "agustus": 8,
    "september": 9, "oktober": 10, "november": 11, "desember": 12,
}


def _require_env(name):
    val = os.environ.get(name)
    if not val:
        raise EnvironmentError(
            f"Missing required env var: {name}\n"
            "Set METABASE_URL, METABASE_USER, METABASE_PASSWORD, "
            "METABASE_CR_CARD_ID, METABASE_QUERY_CARD_ID before running."
        )
    return val


def _claim_before_date(report_period):
    """'Juni 2026' → '~2026-06-01'  (Metabase 'before' filter = up to end of May)"""
    parts = report_period.strip().split()
    month = INDONESIAN_MONTHS[parts[0].lower()]
    year = int(parts[1])
    return f"~{year:04d}-{month:02d}-01"


def _as_of_value(report_period):
    """'Juni 2026' → '2026-05'  (As_Of = previous month of report period)"""
    parts = report_period.strip().split()
    month = INDONESIAN_MONTHS[parts[0].lower()]
    year = int(parts[1])
    if month == 1:
        prev_month, prev_year = 12, year - 1
    else:
        prev_month, prev_year = month - 1, year
    return f"{prev_year:04d}-{prev_month:02d}"


def _as_of_last_day_value(report_period):
    """'Mei 2026' → '2026-05-31'  (Last day of the month)"""
    parts = report_period.strip().split()
    month = INDONESIAN_MONTHS[parts[0].lower()]
    year = int(parts[1])
    _, last_day = calendar.monthrange(year, month)
    return f"{year:04d}-{month:02d}-{last_day:02d}"


def fetch_active_policies_full(base_url, headers, card_id, as_of_date):
    """Fetch active policy list from card 732 → list of dicts.

    Each dict contains at minimum: policy_no, company_name,
    policy_effective_date, policy_renewal_date.
    """
    import csv as _csv
    import io as _io

    card = requests.get(f"{base_url}/api/card/{card_id}", headers=headers, timeout=30).json()

    params_payload = []
    for param in card.get("parameters", []):
        if param.get("slug", "").lower() == "as_of":
            entry = {"id": param["id"], "type": param.get("type", ""), "value": as_of_date}
            if param.get("target"):
                entry["target"] = param["target"]
            params_payload.append(entry)

    resp = requests.post(
        f"{base_url}/api/card/{card_id}/query/csv",
        headers=headers,
        json={"parameters": params_payload},
        timeout=60,
    )
    resp.raise_for_status()
    return list(_csv.DictReader(_io.StringIO(resp.text)))


def _fetch_cr_csv(base_url, headers, card_id, as_of, policy_nos=None):
    """Pull CR CSV from a saved question, filtered by As_Of and optionally policy_no."""
    card = requests.get(
        f"{base_url}/api/card/{card_id}",
        headers=headers,
        timeout=30,
    ).json()

    params_payload = []
    for param in card.get("parameters", []):
        slug = param.get("slug", "")
        param_id = param["id"]
        param_type = param.get("type", "")
        target = param.get("target")

        if slug == "As_Of":
            entry = {"id": param_id, "type": param_type, "value": as_of}
            if target:
                entry["target"] = target
            params_payload.append(entry)

        elif slug == "policy_no" and policy_nos:
            entry = {"id": param_id, "type": param_type, "value": policy_nos}
            if target:
                entry["target"] = target
            params_payload.append(entry)

    resp = requests.post(
        f"{base_url}/api/card/{card_id}/query/csv",
        headers=headers,
        json={"parameters": params_payload},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.text


def _fetch_dashboard_csv(base_url, headers, dashboard_id, claim_before_date, policy_nos=None):
    """Pull CSV from the main table card on a dashboard with claim_date, is_aso, and optional policy_no filters."""

    # Discover dashcard and parameter structure
    resp = requests.get(
        f"{base_url}/api/dashboard/{dashboard_id}",
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    dashboard = resp.json()

    # Find the first dashcard that has an underlying question
    dashcard = None
    for card in dashboard.get("dashcards", dashboard.get("ordered_cards", [])):
        if card.get("card_id"):
            dashcard = card
            break

    if not dashcard:
        raise ValueError(f"No data card found on dashboard {dashboard_id}")

    dashcard_id = dashcard["id"]
    card_id = dashcard["card_id"]

    # Build filter parameters from dashboard definition
    params_payload = []
    for param in dashboard.get("parameters", []):
        slug = param.get("slug", "")
        param_id = param["id"]
        param_type = param.get("type", "")

        # Resolve the target field mapping for this dashcard
        target = None
        for mapping in dashcard.get("parameter_mappings", []):
            if mapping.get("parameter_id") == param_id:
                target = mapping.get("target")
                break

        if slug == "claim_date":
            entry = {"id": param_id, "type": param_type, "value": claim_before_date}
            if target:
                entry["target"] = target
            params_payload.append(entry)

        elif slug == "is_aso":
            entry = {"id": param_id, "type": param_type, "value": "false"}
            if target:
                entry["target"] = target
            params_payload.append(entry)

        elif slug == "policy_no" and policy_nos:
            entry = {"id": param_id, "type": param_type, "value": policy_nos}
            if target:
                entry["target"] = target
            params_payload.append(entry)

    resp = requests.post(
        f"{base_url}/api/dashboard/{dashboard_id}/dashcard/{dashcard_id}/card/{card_id}/query/csv",
        headers=headers,
        json={"parameters": params_payload},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.text


def fetch_from_metabase(convert_folder, report_period,
                        header_policies=None, detail_policies=None):
    """Fetch CR data plus two dashboard CSVs (one per routing group).

    Args:
        header_policies: list of policy_no routed to dashboard 48 (claim history).
                         Pass None to skip that fetch.
        detail_policies: list of policy_no routed to dashboard 38 (benefit detail).
                         Pass None to skip that fetch.

    The combined set of all policies is sent to the CR card so the summary sheet
    contains rows for every policy regardless of routing.
    """
    base_url = _require_env("METABASE_URL").rstrip("/")
    user = _require_env("METABASE_USER")
    password = _require_env("METABASE_PASSWORD")
    cr_card_id = _require_env("METABASE_CR_CARD_ID")
    claim_dash_id = _require_env("METABASE_QUERY_CARD_ID")      # dashboard 48
    benefit_dash_id = _require_env("METABASE_BENEFIT_CARD_ID")  # dashboard 38

    claim_before = _claim_before_date(report_period)
    as_of = _as_of_value(report_period)
    os.makedirs(convert_folder, exist_ok=True)

    print("🌐 Authenticating to Metabase...")
    resp = requests.post(
        f"{base_url}/api/session",
        json={"username": user, "password": password},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json()["id"]
    print("✅ Authenticated\n")

    session_headers = {"X-Metabase-Session": token}

    # Combined policy set for CR — every policy that will be written to Excel
    all_policies = list({*(header_policies or []), *(detail_policies or [])}) or None

    label_h = f"{len(header_policies)} header" if header_policies else "none"
    label_d = f"{len(detail_policies)} detail" if detail_policies else "none"
    print(f"📥 Fetching CR (card {cr_card_id}, As_Of={as_of}) "
          f"+ dash48 [{label_h}] + dash38 [{label_d}] in parallel...")

    def _fetch_cr():
        return _fetch_cr_csv(base_url, session_headers, cr_card_id, as_of, all_policies)

    def _fetch_header():
        if not header_policies:
            return None
        return _fetch_dashboard_csv(
            base_url, session_headers, claim_dash_id, claim_before, header_policies
        )

    def _fetch_detail():
        if not detail_policies:
            return None
        return _fetch_dashboard_csv(
            base_url, session_headers, benefit_dash_id, claim_before, detail_policies
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        f_cr = executor.submit(_fetch_cr)
        f_header = executor.submit(_fetch_header)
        f_detail = executor.submit(_fetch_detail)
        cr_csv = f_cr.result()
        header_csv = f_header.result()
        detail_csv = f_detail.result()

    with open(os.path.join(convert_folder, CR_FILENAME), "w", encoding="utf-8") as f:
        f.write(cr_csv)
    print(f"   ✅ {CR_FILENAME} ({cr_csv.count(chr(10))} lines)")

    if header_csv is not None:
        with open(os.path.join(convert_folder, QUERY_FILENAME), "w", encoding="utf-8") as f:
            f.write(header_csv)
        print(f"   ✅ {QUERY_FILENAME} ({header_csv.count(chr(10))} lines)")

    if detail_csv is not None:
        with open(os.path.join(convert_folder, BENEFIT_FILENAME), "w", encoding="utf-8") as f:
            f.write(detail_csv)
        print(f"   ✅ {BENEFIT_FILENAME} ({detail_csv.count(chr(10))} lines)")

    print("\n🎯 FETCH SELESAI\n")
