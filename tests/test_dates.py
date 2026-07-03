import datetime

import pytest
from freezegun import freeze_time

import fetch
import main
import drafts


# ---------------------------------------------------------------------------
# fetch._as_of_value
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("period, expected", [
    ("Mei 2026", "2026-04"),
    ("Juni 2026", "2026-05"),
    ("Januari 2026", "2025-12"),  # year rollback
    ("Desember 2026", "2026-11"),
])
def test_as_of_value(period, expected):
    assert fetch._as_of_value(period) == expected


# ---------------------------------------------------------------------------
# fetch._claim_before_date
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("period, expected", [
    ("Juni 2026", "~2026-06-01"),
    ("Januari 2026", "~2026-01-01"),  # zero-padding for single-digit month
    ("Desember 2026", "~2026-12-01"),
])
def test_claim_before_date(period, expected):
    assert fetch._claim_before_date(period) == expected


# ---------------------------------------------------------------------------
# fetch._as_of_last_day_value
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("period, expected", [
    ("Mei 2026", "2026-05-31"),
    ("Februari 2024", "2024-02-29"),  # leap year
    ("Februari 2026", "2026-02-28"),  # non-leap year
    ("April 2026", "2026-04-30"),
])
def test_as_of_last_day_value(period, expected):
    assert fetch._as_of_last_day_value(period) == expected


# ---------------------------------------------------------------------------
# main.compute_lapse_cutoff
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("as_of_last_day, expected", [
    (datetime.date(2026, 5, 31), datetime.date(2026, 2, 28)),   # normal case, day-clamped (Feb has no 31)
    (datetime.date(2026, 1, 31), datetime.date(2025, 10, 31)),  # January -> prior year October
    (datetime.date(2026, 2, 28), datetime.date(2025, 11, 28)),  # February -> prior year November
    (datetime.date(2026, 3, 31), datetime.date(2025, 12, 31)),  # March -> prior year December
    (datetime.date(2024, 5, 31), datetime.date(2024, 2, 29)),   # day clamped to leap-year Feb 29
])
def test_compute_lapse_cutoff(as_of_last_day, expected):
    assert main.compute_lapse_cutoff(as_of_last_day) == expected


# ---------------------------------------------------------------------------
# main.get_default_period
# ---------------------------------------------------------------------------

def test_get_default_period():
    with freeze_time("2026-07-03"):
        assert main.get_default_period() == "Juli 2026"


def test_get_default_period_year_boundary():
    with freeze_time("2026-01-15"):
        assert main.get_default_period() == "Januari 2026"


# ---------------------------------------------------------------------------
# drafts._month_tokens
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("period, bulan, bulan_lalu, year", [
    ("Mei 2026", "Mei", "April", "2026"),
    ("Januari 2026", "Januari", "Desember", "2026"),  # wraps to prior year's December
    ("Desember 2026", "Desember", "November", "2026"),
])
def test_month_tokens(period, bulan, bulan_lalu, year):
    assert drafts._month_tokens(period) == (bulan, bulan_lalu, year)


def test_month_tokens_unknown_month():
    bulan, bulan_lalu, year = drafts._month_tokens("Foo 2026")
    assert bulan == "Foo"
    assert bulan_lalu == ""
    assert year == "2026"
