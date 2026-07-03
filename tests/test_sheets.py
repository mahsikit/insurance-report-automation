import pytest

import sheets


class _FakeExecute:
    def __init__(self, ret):
        self._ret = ret

    def execute(self):
        return self._ret


class _FakeValues:
    """Records calls and returns a canned response for `.get()`."""

    def __init__(self, get_return=None):
        self.get_return = get_return or {}
        self.get_calls = []
        self.append_calls = []
        self.batch_update_calls = []

    def get(self, spreadsheetId, range):
        self.get_calls.append(range)
        return _FakeExecute(self.get_return)

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self.append_calls.append(dict(
            spreadsheetId=spreadsheetId, range=range,
            valueInputOption=valueInputOption, insertDataOption=insertDataOption, body=body,
        ))
        return _FakeExecute({})

    def batchUpdate(self, spreadsheetId, body):
        self.batch_update_calls.append(dict(spreadsheetId=spreadsheetId, body=body))
        return _FakeExecute({})


class _FakeSpreadsheets:
    def __init__(self, values):
        self._values = values

    def values(self):
        return self._values


class _FakeSheetsService:
    def __init__(self, values):
        self._spreadsheets = _FakeSpreadsheets(values)

    def spreadsheets(self):
        return self._spreadsheets


def _patch_build(monkeypatch, values):
    service = _FakeSheetsService(values)
    monkeypatch.setattr(sheets, "build", lambda *a, **k: service)
    return service


# ---------------------------------------------------------------------------
# read_master
# ---------------------------------------------------------------------------

def test_read_master_skips_header_and_blank_rows(monkeypatch):
    rows = [
        ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K"],  # header — skipped
        ["Co1", "Src1", "detail", "", "POL1", "a@x.com", "b@x.com", "", "2027-01-01"],
        ["Co2", "Src2", "", "", "POL2", "c@x.com", "", "", "2027-02-01"],  # blank col C -> skipped
        ["Co3", "Src3", "header", "", "", "d@x.com", "", "", "2027-03-01"],  # blank col E -> skipped
    ]
    values = _FakeValues(get_return={"values": rows})
    _patch_build(monkeypatch, values)

    master = sheets.read_master(None, "sheet-id", "FINAL")

    assert set(master.keys()) == {"POL1"}
    assert master["POL1"]["need_report"] == "detail"
    assert master["POL1"]["to"] == ["a@x.com"]
    assert master["POL1"]["cc"] == ["b@x.com"]
    assert master["POL1"]["row"] == 2
    assert master["POL1"]["e_date"] == "2027-01-01"
    assert values.get_calls == ["'FINAL'!A:K"]


def test_read_master_keeps_first_occurrence_of_duplicate_policy(monkeypatch):
    rows = [
        ["header"] * 11,
        ["Co1", "Src1", "detail", "", "POL1", "first@x.com", "", "", "2027-01-01"],
        ["Co1", "Src1", "detail", "", "POL1", "second@x.com", "", "", "2027-01-01"],
    ]
    values = _FakeValues(get_return={"values": rows})
    _patch_build(monkeypatch, values)

    master = sheets.read_master(None, "sheet-id", "FINAL")
    assert master["POL1"]["to"] == ["first@x.com"]


# ---------------------------------------------------------------------------
# sync_new_policies
# ---------------------------------------------------------------------------

def test_sync_new_policies_appends_only_new(monkeypatch):
    existing_col_e = {"values": [["header"], ["POL1"], ["POL2"]]}
    values = _FakeValues(get_return=existing_col_e)
    _patch_build(monkeypatch, values)

    active_policies = [
        {"policy_no": "POL1", "company_name": "Co1", "source_name": "Src1",
         "policy_effective_date": "2026-01-01", "policy_renewal_date": "2027-01-01"},
        {"policy_no": "POL3", "company_name": "Co3", "source_name": "Src3",
         "policy_effective_date": "2026-03-01", "policy_renewal_date": "2027-03-01"},
    ]

    added = sheets.sync_new_policies(None, "sheet-id", "FINAL", active_policies)

    assert added == ["POL3"]
    assert len(values.append_calls) == 1
    appended_row = values.append_calls[0]["body"]["values"][0]
    # Row = [company, source_name, "", "", pno, "", "", eff, ren]
    assert appended_row == ["Co3", "Src3", "", "", "POL3", "", "", "2026-03-01", "2027-03-01"]


def test_sync_new_policies_noop_when_nothing_new(monkeypatch):
    existing_col_e = {"values": [["header"], ["POL1"]]}
    values = _FakeValues(get_return=existing_col_e)
    _patch_build(monkeypatch, values)

    active_policies = [{"policy_no": "POL1", "company_name": "Co1", "source_name": "Src1",
                         "policy_effective_date": "", "policy_renewal_date": ""}]

    added = sheets.sync_new_policies(None, "sheet-id", "FINAL", active_policies)
    assert added == []
    assert values.append_calls == []


# ---------------------------------------------------------------------------
# write_links
# ---------------------------------------------------------------------------

def test_write_links_writes_matched_and_reports_unmatched(monkeypatch, capsys):
    values = _FakeValues()
    _patch_build(monkeypatch, values)

    master = {"POL1": {"row": 5}}
    upload_results = {
        "POL1": {"web_view_link": "https://drive.google.com/file/d/abc/view"},
        "POL2": {"web_view_link": "https://drive.google.com/file/d/def/view"},  # not in master
    }

    sheets.write_links(None, "sheet-id", "FINAL", upload_results, "2026-05", master)

    assert len(values.batch_update_calls) == 1
    data = values.batch_update_calls[0]["body"]["data"]
    assert data == [{
        "range": "'FINAL'!J5:K5",
        "values": [["https://drive.google.com/file/d/abc/view", "2026-05"]],
    }]
    out = capsys.readouterr().out
    assert "POL2" in out  # unmatched policy reported


def test_write_links_no_batch_update_when_nothing_matches(monkeypatch):
    values = _FakeValues()
    _patch_build(monkeypatch, values)

    sheets.write_links(None, "sheet-id", "FINAL", {"POLX": {"web_view_link": "x"}}, "2026-05", {})
    assert values.batch_update_calls == []
