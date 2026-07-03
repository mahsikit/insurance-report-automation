import pandas as pd
import pytest

import sheets
import drafts
import process


# ---------------------------------------------------------------------------
# sheets._parse_emails
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("a@x.com, b@x.com", ["a@x.com", "b@x.com"]),
    ("a@x.com; b@x.com", ["a@x.com", "b@x.com"]),
    ("a@x.com;b@x.com,c@x.com", ["a@x.com", "b@x.com", "c@x.com"]),
    ("  a@x.com  ,  b@x.com  ", ["a@x.com", "b@x.com"]),
    ("", []),
    ("   ", []),
    (None, []),
])
def test_parse_emails(raw, expected):
    assert sheets._parse_emails(raw) == expected


# ---------------------------------------------------------------------------
# drafts._sanitize_email
# ---------------------------------------------------------------------------

def test_sanitize_email_strips_whitespace():
    assert drafts._sanitize_email("  a@x.com  ") == "a@x.com"


def test_sanitize_email_strips_control_chars():
    assert drafts._sanitize_email("a@x.com\r\n\t") == "a@x.com"
    assert drafts._sanitize_email("a\x00@x.com\x7f") == "a@x.com"


# ---------------------------------------------------------------------------
# process.broker_folder
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("PT ANDIKA MITRA SEJATI (EB HEALTH)", "PT ANDIKA MITRA SEJATI (EB HEALTH)"),
    ('Broker/With:Bad*Chars?"<>|', "BrokerWithBadChars"),
    ("", "UNKNOWN"),
    ("nan", "UNKNOWN"),
    (None, "UNKNOWN"),
    ("   ", "UNKNOWN"),
])
def test_broker_folder(raw, expected):
    assert process.broker_folder(raw) == expected


# ---------------------------------------------------------------------------
# process._apply_numeric
# ---------------------------------------------------------------------------

def test_apply_numeric_coerces_listed_columns():
    df = pd.DataFrame({"approved": ["100", "200.5", "bad"], "other": ["x", "y", "z"]})
    out = process._apply_numeric(df, ["approved"])
    assert out["approved"].tolist()[0] == 100.0
    assert out["approved"].tolist()[1] == 200.5
    assert pd.isna(out["approved"].tolist()[2])
    # untouched column stays as-is
    assert out["other"].tolist() == ["x", "y", "z"]


def test_apply_numeric_ignores_missing_columns():
    df = pd.DataFrame({"other": ["x"]})
    # Should not raise even though "approved" isn't in df
    out = process._apply_numeric(df, ["approved"])
    assert "approved" not in out.columns
