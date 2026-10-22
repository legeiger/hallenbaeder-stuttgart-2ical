"""Tests für Zeit- und Datumshelfer (parse_clock, parse_date, Zeitfenster)."""

from __future__ import annotations

import datetime
import zoneinfo

import pytest

import generate_ical as gi


class TestParseClock:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("14:30", (14, 30)),
            ("7:00", (7, 0)),
            ("07:00", (7, 0)),
            ("0:00", (0, 0)),
            ("23:59", (23, 59)),
            ("24:00", (24, 0)),  # Mitternacht am Tagesende
            ("  8:15  ", (8, 15)),
            ("12", (12, 0)),  # Stundenangabe ohne Minuten
            (datetime.time(9, 5), (9, 5)),
        ],
    )
    def test_valid_values(self, value, expected):
        assert gi.parse_clock(value) == expected

    @pytest.mark.parametrize(
        "value",
        ["", "junk", "25:00", "24:30", "12:60", "-1:00", "12:3", "1:2", None, 42, {}],
    )
    def test_invalid_values_return_none(self, value):
        assert gi.parse_clock(value) is None

    def test_midnight_rolls_over_instead_of_raising(self):
        # Früher: ValueError: hour must be in 0..23
        day = datetime.date(2026, 10, 22)
        assert gi.make_aware_dt(day, "24:00") == datetime.datetime(
            2026, 10, 23, 0, 0, tzinfo=gi.TZ
        )


class TestParseDate:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("2026-10-22", datetime.date(2026, 10, 22)),
            ("2026-10-22T00:00:00", datetime.date(2026, 10, 22)),
            ("2026-10-22 18:00:00", datetime.date(2026, 10, 22)),
            ("24.12.2027", datetime.date(2027, 12, 24)),
            ("1.6.2027", datetime.date(2027, 6, 1)),
            (datetime.date(2027, 1, 1), datetime.date(2027, 1, 1)),
            (datetime.datetime(2027, 1, 1, 12, 0), datetime.date(2027, 1, 1)),
        ],
    )
    def test_valid_values(self, value, expected):
        assert gi.parse_date(value) == expected

    @pytest.mark.parametrize(
        "value", ["", "junk", "2026-13-01", "31.02.2027", None, 20261022, [], "2026/10/22"]
    )
    def test_invalid_values_return_none(self, value):
        assert gi.parse_date(value) is None

    def test_iso_datetime_no_longer_raises(self):
        # Früher: ValueError: unconverted data remains: T00:00:00
        assert gi.parse_date("2026-01-01T00:00:00") == datetime.date(2026, 1, 1)


class TestLocalizeWallTime:
    def test_winter_and_summer_offset(self):
        winter = gi.localize_wall_time(datetime.date(2026, 1, 15), 6, 0)
        summer = gi.localize_wall_time(datetime.date(2026, 7, 15), 6, 0)
        assert winter.utcoffset() == datetime.timedelta(hours=1)
        assert summer.utcoffset() == datetime.timedelta(hours=2)

    def test_hour_24_is_next_midnight(self):
        assert gi.localize_wall_time(datetime.date(2026, 10, 22), 24) == datetime.datetime(
            2026, 10, 23, tzinfo=gi.TZ
        )

    def test_uses_zoneinfo_not_pytz(self):
        assert isinstance(gi.TZ, zoneinfo.ZoneInfo)
        assert str(gi.TZ) == "Europe/Berlin"

    @pytest.mark.parametrize("day", [datetime.date(2026, 10, 25), datetime.date(2027, 3, 28)])
    def test_dst_transition_days_keep_usual_opening_times(self, day):
        # Öffnungszeiten liegen außerhalb der Umstellungsstunde -> unveränderte Wanduhrzeit
        start = gi.localize_wall_time(day, 6, 0)
        assert (start.hour, start.minute) == (6, 0)
        assert start.utcoffset() in (
            datetime.timedelta(hours=1),
            datetime.timedelta(hours=2),
        )


class TestSlotDatetimes:
    def test_normal_window(self):
        day = datetime.date(2026, 10, 22)
        start, end = gi.slot_datetimes(day, "08:00", "18:00")
        assert end - start == datetime.timedelta(hours=10)
        assert start.date() == day

    def test_window_over_midnight_runs_to_next_day(self):
        day = datetime.date(2026, 10, 22)
        start, end = gi.slot_datetimes(day, "22:00", "02:00")
        assert start.date() == day
        assert end.date() == day + datetime.timedelta(days=1)
        assert end > start

    def test_window_ending_at_24_00(self):
        day = datetime.date(2026, 10, 22)
        start, end = gi.slot_datetimes(day, "06:00", "24:00")
        assert end == datetime.datetime(2026, 10, 23, 0, 0, tzinfo=gi.TZ)

    def test_zero_length_window_is_dropped(self):
        assert gi.slot_datetimes(datetime.date(2026, 10, 22), "08:00", "08:00") is None

    @pytest.mark.parametrize(("start", "end"), [("junk", "18:00"), ("08:00", "junk"), ("", "")])
    def test_invalid_window_is_dropped(self, start, end):
        assert gi.slot_datetimes(datetime.date(2026, 10, 22), start, end) is None

    def test_make_aware_dt_raises_schedule_error(self):
        with pytest.raises(gi.ScheduleError):
            gi.make_aware_dt(datetime.date(2026, 10, 22), "kaputt")

    def test_schedule_error_is_a_value_error(self):
        assert issubclass(gi.ScheduleError, ValueError)
