import os
import requests
import calendar
from concurrent.futures import ThreadPoolExecutor

CR_FILENAME = "claim_ratio_CR.csv"
QUERY_FILENAME = "query_result_Query_result.csv"

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
    """'Mei 2026' → '~2026-06-01'  (Metabase 'before' filter = up to end of May)"""
    parts = report_period.strip().split()
    month = INDONESIAN_MONTHS[parts[0].lower()]
    year = int(parts[1])
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1
    return f"~{next_year:04d}-{next_month:02d}-01"


def _as_of_value(report_period):
    """'Mei 2026' → '2026-05'  (Metabase date/month-year filter for As_Of)"""
    parts = report_period.strip().split()
    month = INDONESIAN_MONTHS[parts[0].lower()]
    year = int(parts[1])
    return f"{year:04d}-{month:02d}"


def _as_of_last_day_value(report_period):
    """'Mei 2026' → '2026-05-31'  (Last day of the month)"""
    parts = report_period.strip().split()
    month = INDONESIAN_MONTHS[parts[0].lower()]
    year = int(parts[1])
    _, last_day = calendar.monthrange(year, month)
    return f"{year:04d}-{month:02d}-{last_day:02d}"


def _fetch_active_policy_nos(base_url, headers, card_id, as_of_date):
    """Fetch the active policy list from a saved question → list of policy_no strings."""
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

        # The user mentioned 'As_of' but we'll accept 'As_Of' too to be safe
        if slug.lower() == "as_of":
            entry = {"id": param_id, "type": param_type, "value": as_of_date}
            if target:
                entry["target"] = target
            params_payload.append(entry)

    resp = requests.post(
        f"{base_url}/api/card/{card_id}/query/csv",
        headers=headers,
        json={"parameters": params_payload},
        timeout=60,
    )
    resp.raise_for_status()
    lines = resp.text.strip().splitlines()
    if len(lines) < 2:
        return []
    # Find the policy_no column
    col_headers = lines[0].split(",")
    try:
        idx = col_headers.index("policy_no")
    except ValueError:
        raise ValueError(f"Card {card_id} has no 'policy_no' column. Columns: {col_headers}")
    return [line.split(",")[idx].strip() for line in lines[1:] if line.strip()]


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
    print(f"\n--- DEBUG: CR CSV OUTPUT (first 300 chars) ---\n{resp.text[:300]}\n----------------------------------------------")
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
    print(f"\n--- DEBUG: DASHBOARD CSV OUTPUT (first 300 chars) ---\n{resp.text[:300]}\n----------------------------------------------")
    return resp.text


def fetch_from_metabase(convert_folder, use_benefit=False, report_period="Mei 2026", manual_policies=None):
    base_url = _require_env("METABASE_URL").rstrip("/")
    user = _require_env("METABASE_USER")
    password = _require_env("METABASE_PASSWORD")
    cr_card_id = _require_env("METABASE_CR_CARD_ID")

    if use_benefit:
        query_dashboard_id = _require_env("METABASE_BENEFIT_CARD_ID")
        query_label = "Benefit Level"
    else:
        query_dashboard_id = _require_env("METABASE_QUERY_CARD_ID")
        query_label = "Query Result (Claim Level)"

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

    active_policy_nos = None
    if manual_policies:
        active_policy_nos = manual_policies
        print(f"📋 Using {len(active_policy_nos)} manually provided policies...\n")
    else:
        # Optional: fetch active policy list to filter dashboard results
        active_policy_card_id = os.environ.get("METABASE_ACTIVE_POLICY_CARD_ID")
        if active_policy_card_id:
            as_of_last_day = _as_of_last_day_value(report_period)
            print(f"📋 Fetching active policy list (card {active_policy_card_id}, As_of={as_of_last_day})...")
            active_policy_nos = _fetch_active_policy_nos(base_url, session_headers, active_policy_card_id, as_of_last_day)
            print(f"   ✅ {len(active_policy_nos)} active policies\n")

    # 1+2. Fetch CR and claim/benefit data in parallel (both depend on active_policy_nos)
    policy_count_label = f", {len(active_policy_nos)} policies" if active_policy_nos else ""
    print(f"📥 Fetching CR (card {cr_card_id}, As_Of={as_of}{policy_count_label}) "
          f"+ {query_label} (dashboard {query_dashboard_id}) in parallel...")

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_cr = executor.submit(
            _fetch_cr_csv, base_url, session_headers, cr_card_id, as_of, active_policy_nos
        )
        future_dashboard = executor.submit(
            _fetch_dashboard_csv, base_url, session_headers, query_dashboard_id,
            claim_before, active_policy_nos
        )
        cr_csv = future_cr.result()
        dashboard_csv = future_dashboard.result()

    with open(os.path.join(convert_folder, CR_FILENAME), "w", encoding="utf-8") as f:
        f.write(cr_csv)
    print(f"   ✅ {CR_FILENAME} ({cr_csv.count(chr(10))} lines)")

    with open(os.path.join(convert_folder, QUERY_FILENAME), "w", encoding="utf-8") as f:
        f.write(dashboard_csv)
    print(f"   ✅ {QUERY_FILENAME} ({dashboard_csv.count(chr(10))} lines)\n")

    print("🎯 FETCH SELESAI\n")
