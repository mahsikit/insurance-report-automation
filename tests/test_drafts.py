import base64
import email
import os

import pytest

import drafts


# ---------------------------------------------------------------------------
# group_by_recipients
# ---------------------------------------------------------------------------

def _master(to=None, cc=None):
    return {"to": to or [], "cc": cc or []}


def test_group_by_recipients_bundles_shared_to_cc():
    master = {
        "POL1": _master(to=["a@x.com"], cc=["b@x.com"]),
        "POL2": _master(to=["a@x.com"], cc=["b@x.com"]),
    }
    upload_results = {
        "POL1": {"company_name": "A"},
        "POL2": {"company_name": "B"},
    }
    groups = drafts.group_by_recipients(master, upload_results)
    assert len(groups) == 1
    (group,) = groups.values()
    assert {p for p, _ in group["policies"]} == {"POL1", "POL2"}


def test_group_by_recipients_splits_blank_to_cc():
    master = {
        "POL1": _master(),  # blank to/cc
        "POL2": _master(),  # blank to/cc — must NOT be bundled with POL1
    }
    upload_results = {
        "POL1": {"company_name": "A"},
        "POL2": {"company_name": "B"},
    }
    groups = drafts.group_by_recipients(master, upload_results)
    assert len(groups) == 2
    all_policies = [p for g in groups.values() for p, _ in g["policies"]]
    assert sorted(all_policies) == ["POL1", "POL2"]


def test_group_by_recipients_excludes_policies_missing_from_master():
    master = {"POL1": _master(to=["a@x.com"])}
    upload_results = {
        "POL1": {"company_name": "A"},
        "POL2": {"company_name": "B"},  # not in master
    }
    groups = drafts.group_by_recipients(master, upload_results)
    all_policies = [p for g in groups.values() for p, _ in g["policies"]]
    assert all_policies == ["POL1"]


# ---------------------------------------------------------------------------
# _build_html_body
# ---------------------------------------------------------------------------

def _policies_sorted():
    return [
        ("POL1", {"company_name": "ACME Corp", "policy_effective_date": "2026-01-01", "policy_renewal_date": "2027-01-01"}),
    ]


def test_build_html_body_omits_jolly_paragraph_for_andika():
    html = drafts._build_html_body("Mei", "April", "2026", "PT ANDIKA MITRA SEJATI", _policies_sorted())
    assert "Jolly HR" not in html


def test_build_html_body_omits_jolly_paragraph_case_insensitive():
    html = drafts._build_html_body("Mei", "April", "2026", "andika insurance broker", _policies_sorted())
    assert "Jolly HR" not in html


def test_build_html_body_includes_jolly_paragraph_for_other_brokers():
    html = drafts._build_html_body("Mei", "April", "2026", "PT OTHER BROKER", _policies_sorted())
    assert "Jolly HR" in html


def test_build_html_body_has_one_row_per_policy():
    policies = _policies_sorted() * 3
    policies = [(f"POL{i}", info) for i, (_, info) in enumerate(policies)]
    html = drafts._build_html_body("Mei", "April", "2026", "Broker", policies)
    # The header row uses `<tr style=...>` so a plain `<tr>` count reflects only data rows.
    assert html.count("<tr>") == len(policies)


# ---------------------------------------------------------------------------
# create_drafts — end-to-end with a fake Gmail service (no network)
# ---------------------------------------------------------------------------

class _FakeExecute:
    def __init__(self, ret):
        self._ret = ret

    def execute(self):
        return self._ret


class _FakeDrafts:
    def __init__(self, calls):
        self._calls = calls

    def create(self, userId, body):
        self._calls.append({"userId": userId, "body": body})
        return _FakeExecute({"id": f"draft{len(self._calls)}"})


class _FakeUsers:
    def __init__(self, calls):
        self._drafts = _FakeDrafts(calls)

    def drafts(self):
        return self._drafts


class _FakeGmailService:
    def __init__(self):
        self.calls = []
        self._users = _FakeUsers(self.calls)

    def users(self):
        return self._users


def _make_xlsx(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"dummy-xlsx-bytes")
    return path


def test_create_drafts_creates_one_draft_per_group(tmp_path, monkeypatch):
    fake_service = _FakeGmailService()
    monkeypatch.setattr(drafts, "build", lambda *a, **k: fake_service)

    master = {
        "POL1": {"to": ["a@x.com"], "cc": [], "need_report": "detail", "row": 2},
        "POL2": {"to": ["b@x.com"], "cc": [], "need_report": "header", "row": 3},
    }
    upload_results = {
        "POL1": {
            "file_path": _make_xlsx(str(tmp_path / "POL1.xlsx")),
            "company_name": "ACME Corp",
            "source_name": "BrokerX",
            "policy_effective_date": "2026-01-01",
            "policy_renewal_date": "2027-01-01",
        },
        "POL2": {
            "file_path": _make_xlsx(str(tmp_path / "POL2.xlsx")),
            "company_name": "Beta Co",
            "source_name": "BrokerY",
            "policy_effective_date": "2026-02-01",
            "policy_renewal_date": "2027-02-01",
        },
    }

    count = drafts.create_drafts(credentials=None, master=master, upload_results=upload_results, report_period="Mei 2026")

    assert count == 2  # different To addresses -> two separate drafts
    assert len(fake_service.calls) == 2


def test_create_drafts_leaves_headers_unset_when_blank(tmp_path, monkeypatch):
    fake_service = _FakeGmailService()
    monkeypatch.setattr(drafts, "build", lambda *a, **k: fake_service)

    master = {
        "POL1": {"to": [], "cc": [], "need_report": "detail", "row": 2},
    }
    upload_results = {
        "POL1": {
            "file_path": _make_xlsx(str(tmp_path / "POL1.xlsx")),
            "company_name": "ACME Corp",
            "source_name": "BrokerX",
            "policy_effective_date": "2026-01-01",
            "policy_renewal_date": "2027-01-01",
        },
    }

    count = drafts.create_drafts(credentials=None, master=master, upload_results=upload_results, report_period="Mei 2026")
    assert count == 1

    raw = fake_service.calls[0]["body"]["message"]["raw"]
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg.get("To") is None
    assert msg.get("Cc") is None
    assert "Mei 2026" in msg.get("Subject")
