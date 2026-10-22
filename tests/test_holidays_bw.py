"""Tests für die berechneten Feiertage in Baden-Württemberg."""

from __future__ import annotations

import datetime

import pytest

from holidays_bw import (
    easter_sunday,
    is_public_holiday,
    public_holidays,
    public_holidays_in_range,
)


class TestEasterSunday:
    @pytest.mark.parametrize(
        ("year", "expected"),
        [
            (2024, datetime.date(2024, 3, 31)),
            (2025, datetime.date(2025, 4, 20)),
            (2026, datetime.date(2026, 4, 5)),
            (2027, datetime.date(2027, 3, 28)),
            (2028, datetime.date(2028, 4, 16)),
            (2030, datetime.date(2030, 4, 21)),
            (2040, datetime.date(2040, 4, 1)),
        ],
    )
    def test_known_dates(self, year, expected):
        assert easter_sunday(year) == expected

    def test_easter_is_always_a_sunday(self):
        for year in range(2000, 2101):
            assert easter_sunday(year).weekday() == 6

    def test_easter_stays_in_march_or_april(self):
        for year in range(2000, 2101):
            assert easter_sunday(year).month in (3, 4)

    def test_rejects_years_before_1583(self):
        with pytest.raises(ValueError):
            easter_sunday(1582)


class TestPublicHolidays:
    def test_bw_2027_matches_the_bathing_site_list(self):
        # Die Böblinger Seite führt diese Tage als Schließtage.
        expected = {
            datetime.date(2027, 1, 1),
            datetime.date(2027, 1, 6),
            datetime.date(2027, 3, 26),
            datetime.date(2027, 3, 28),
            datetime.date(2027, 3, 29),
            datetime.date(2027, 5, 1),
            datetime.date(2027, 5, 6),
        }
        assert expected <= set(public_holidays(2027))
        assert public_holidays(2027)[datetime.date(2027, 5, 6)] == "Christi Himmelfahrt"

    def test_movable_holidays_follow_easter(self):
        easter = easter_sunday(2027)
        holidays = public_holidays(2027)
        assert holidays[easter - datetime.timedelta(days=2)] == "Karfreitag"
        assert holidays[easter + datetime.timedelta(days=1)] == "Ostermontag"
        assert holidays[easter + datetime.timedelta(days=39)] == "Christi Himmelfahrt"
        assert holidays[easter + datetime.timedelta(days=50)] == "Pfingstmontag"
        assert holidays[easter + datetime.timedelta(days=60)] == "Fronleichnam"

    def test_sorted_and_without_duplicates(self):
        holidays = public_holidays(2026)
        days = list(holidays)
        assert days == sorted(days)
        assert len(days) == len(set(days))

    @pytest.mark.parametrize("year", [2026, 2027, 2028, 2035])
    def test_every_year_has_the_same_number_of_holidays(self, year):
        assert len(public_holidays(year)) == 14  # 12 gesetzliche + Oster-/Pfingstsonntag

    def test_works_far_beyond_the_old_hardcoded_list(self):
        # Die frühere Liste endete am 06.05.2027.
        assert datetime.date(2032, 5, 6) in public_holidays(2032)
        assert datetime.date(2040, 12, 25) in public_holidays(2040)


class TestIsPublicHoliday:
    @pytest.mark.parametrize(
        "day",
        [
            datetime.date(2026, 10, 3),   # Tag der Deutschen Einheit
            datetime.date(2026, 11, 1),   # Allerheiligen
            datetime.date(2027, 5, 6),    # Christi Himmelfahrt
            datetime.date(2027, 5, 17),   # Pfingstmontag
            datetime.date(2027, 5, 27),   # Fronleichnam
            datetime.date(2028, 1, 6),    # Heilige Drei Könige
        ],
    )
    def test_public_holidays(self, day):
        assert is_public_holiday(day) is True

    @pytest.mark.parametrize(
        "day",
        [
            datetime.date(2026, 12, 24),  # Heiligabend: kein gesetzlicher Feiertag
            datetime.date(2026, 12, 31),  # Silvester: kein gesetzlicher Feiertag
            datetime.date(2027, 5, 18),   # Dienstag nach Pfingsten
            datetime.date(2027, 7, 4),
        ],
    )
    def test_non_holidays(self, day):
        assert is_public_holiday(day) is False


class TestPublicHolidaysInRange:
    def test_window_within_one_year(self):
        holidays = public_holidays_in_range(datetime.date(2026, 10, 22), 60)
        assert list(holidays) == [datetime.date(2026, 11, 1)]

    def test_window_across_new_year(self):
        holidays = public_holidays_in_range(datetime.date(2026, 12, 20), 30)
        assert datetime.date(2027, 1, 1) in holidays
        assert datetime.date(2027, 1, 6) in holidays
        assert datetime.date(2026, 12, 25) in holidays

    def test_range_is_inclusive_and_length_matches(self):
        start = datetime.date(2027, 4, 30)
        holidays = public_holidays_in_range(start, 4)
        assert list(holidays) == [datetime.date(2027, 5, 1)]

    def test_empty_range(self):
        assert public_holidays_in_range(datetime.date(2027, 5, 1), 0) == {}
        assert public_holidays_in_range(datetime.date(2027, 5, 1), -5) == {}

    def test_every_day_of_the_range_is_covered(self):
        # Fenster über drei Jahre: jedes Jahr liefert Feiertage
        holidays = public_holidays_in_range(datetime.date(2027, 1, 1), 365 * 3)
        years = {day.year for day in holidays}
        assert years == {2027, 2028, 2029}
