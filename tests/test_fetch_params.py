import pytest

import fetch


class _FakeResponse:
    def __init__(self, json_data=None, text="", status_code=200):
        self._json_data = json_data
        self.text = text
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# ---------------------------------------------------------------------------
# _fetch_cr_csv — parameters matched by slug, not display name
# ---------------------------------------------------------------------------

CR_CARD_DEF = {
    "parameters": [
        {"id": "p1", "type": "date/single", "slug": "As_Of", "target": ["variable", ["template-tag", "As_Of"]]},
        {"id": "p2", "type": "string/=", "slug": "policy_no", "target": ["variable", ["template-tag", "policy_no"]]},
        {"id": "p3", "type": "string/=", "slug": "Other", "target": None},  # should be ignored
    ]
}


def test_fetch_cr_csv_builds_params_by_slug(monkeypatch):
    calls = {}

    def fake_get(url, headers=None, timeout=None):
        calls["get_url"] = url
        return _FakeResponse(json_data=CR_CARD_DEF)

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["post_url"] = url
        calls["post_json"] = json
        return _FakeResponse(text="policy_no,approved\nPOL1,100\n")

    monkeypatch.setattr(fetch.requests, "get", fake_get)
    monkeypatch.setattr(fetch.requests, "post", fake_post)

    csv_text = fetch._fetch_cr_csv("http://mb", {"X-Metabase-Session": "tok"}, "552", "2026-05", ["POL1", "POL2"])

    assert csv_text == "policy_no,approved\nPOL1,100\n"
    params = calls["post_json"]["parameters"]
    assert len(params) == 2  # "Other" slug excluded
    as_of_param = next(p for p in params if p["id"] == "p1")
    assert as_of_param["value"] == "2026-05"
    policy_param = next(p for p in params if p["id"] == "p2")
    assert policy_param["value"] == ["POL1", "POL2"]


def test_fetch_cr_csv_skips_policy_no_param_when_no_policies(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse(json_data=CR_CARD_DEF)

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(text="")

    monkeypatch.setattr(fetch.requests, "get", fake_get)
    monkeypatch.setattr(fetch.requests, "post", fake_post)

    fetch._fetch_cr_csv("http://mb", {}, "552", "2026-05", None)

    params = captured["json"]["parameters"]
    assert all(p["id"] != "p2" for p in params)  # no policy_no param sent


# ---------------------------------------------------------------------------
# _fetch_dashboard_csv — dashcard/param discovery + target resolution
# ---------------------------------------------------------------------------

DASHBOARD_DEF = {
    "dashcards": [
        {
            "id": "dc1",
            "card_id": "c1",
            "parameter_mappings": [
                {"parameter_id": "pd1", "target": ["dimension", ["template-tag", "claim_date"]]},
                {"parameter_id": "pd2", "target": ["dimension", ["template-tag", "is_aso"]]},
                {"parameter_id": "pd3", "target": ["dimension", ["template-tag", "policy_no"]]},
            ],
        }
    ],
    "parameters": [
        {"id": "pd1", "type": "date/single", "slug": "claim_date"},
        {"id": "pd2", "type": "string/=", "slug": "is_aso"},
        {"id": "pd3", "type": "string/=", "slug": "policy_no"},
    ],
}


def test_fetch_dashboard_csv_builds_expected_params(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse(json_data=DASHBOARD_DEF)

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse(text="policy_no,approved\nPOL1,100\n")

    monkeypatch.setattr(fetch.requests, "get", fake_get)
    monkeypatch.setattr(fetch.requests, "post", fake_post)

    csv_text = fetch._fetch_dashboard_csv("http://mb", {}, "48", "~2026-06-01", ["POL1"])

    assert csv_text == "policy_no,approved\nPOL1,100\n"
    assert captured["url"] == "http://mb/api/dashboard/48/dashcard/dc1/card/c1/query/csv"

    params = {p["id"]: p for p in captured["json"]["parameters"]}
    assert params["pd1"]["value"] == "~2026-06-01"
    assert params["pd2"]["value"] == "false"
    assert params["pd3"]["value"] == ["POL1"]
    assert params["pd1"]["target"] == ["dimension", ["template-tag", "claim_date"]]


def test_fetch_dashboard_csv_raises_when_no_data_card(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse(json_data={"dashcards": [], "parameters": []})

    monkeypatch.setattr(fetch.requests, "get", fake_get)

    with pytest.raises(ValueError, match="No data card found"):
        fetch._fetch_dashboard_csv("http://mb", {}, "48", "~2026-06-01", None)


# ---------------------------------------------------------------------------
# fetch_active_policies_full — CSV parsing + slug matching (case-insensitive)
# ---------------------------------------------------------------------------

def test_fetch_active_policies_full_parses_csv(monkeypatch):
    card_def = {"parameters": [{"id": "pa1", "type": "date/single", "slug": "As_Of", "target": ["variable", ["template-tag", "As_Of"]]}]}
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse(json_data=card_def)

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(text="policy_no,company_name\nPOL1,ACME Corp\nPOL2,Beta Co\n")

    monkeypatch.setattr(fetch.requests, "get", fake_get)
    monkeypatch.setattr(fetch.requests, "post", fake_post)

    rows = fetch.fetch_active_policies_full("http://mb", {}, "732", "2026-05-31")

    assert rows == [
        {"policy_no": "POL1", "company_name": "ACME Corp"},
        {"policy_no": "POL2", "company_name": "Beta Co"},
    ]
    param = captured["json"]["parameters"][0]
    assert param["value"] == "2026-05-31"
