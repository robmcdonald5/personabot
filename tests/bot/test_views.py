"""Tests for `personabot.bot.views` helpers.

Covers the date parsing / normalization contract used by
`ScrapeWindowModal`. Modal UI itself can't be unit-tested (it's a
Discord-side form), but the parser it delegates to can.
"""

from datetime import date, datetime, timezone

import pytest

from personabot.bot import views


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2024-01-15", date(2024, 1, 15)),
        ("2024-1-15", date(2024, 1, 15)),
        ("2024-01-1", date(2024, 1, 1)),
        ("2024-1-1", date(2024, 1, 1)),
        ("2024-12-31", date(2024, 12, 31)),
        ("  2024-1-15  ", date(2024, 1, 15)),  # whitespace stripped
    ],
)
def test_parse_date_ymd_accepts_valid_forms(raw: str, expected: date):
    assert views.parse_date_ymd(raw, "Start") == expected


@pytest.mark.parametrize(
    "raw",
    [
        # Shape failures
        "",
        "not-a-date",
        "2024",
        "2024-01",
        "24-1-15",  # 2-digit year — %Y requires 4 digits
        "2024/01/15",  # wrong separator
        "2024-01-15T00:00:00",  # extraneous time component
        "abcd-ef-gh",
        # Invalid calendar dates
        "2024-02-30",  # February doesn't have 30 days
        "2024-13-01",  # month 13
        "2024-00-01",  # month 0
        "2024-01-32",  # day 32
        "2023-02-29",  # 2023 is not a leap year
    ],
)
def test_parse_date_ymd_rejects_invalid(raw: str):
    with pytest.raises(ValueError, match="must be a valid date in YYYY-MM-DD"):
        views.parse_date_ymd(raw, "Start")


def test_date_to_utc_midnight_produces_tz_aware_utc():
    dt = views.date_to_utc_midnight(date(2024, 1, 15))
    assert dt == datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
    assert dt.tzinfo is timezone.utc
