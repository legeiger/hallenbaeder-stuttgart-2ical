"""Tests für das Hallenbad Böblingen (Öffnungszeiten und Schließtage).

Fixtures: ``boeblingen_page.txt`` ist der ausgelesene Text der echten Seite
(Öffnungszeiten, Preise, Schließtage-Liste, danach weitere Abschnitte).
"""

from __future__ import annotations

import datetime

import httpx
import pytest
from conftest import make_handler, read_fixture

import generate_ical as gi

PAGE = read_fixture("boeblingen_page.txt")

# Absichtlich künstliche Seite: ein Datum außerhalb der Schließtage-Liste darf
# nicht als Schließtag gelten.
PAGE_WITH_DECOY = (
    "Öffnungszeiten\n\nMo.\n\n\n14–21 Uhr\n\n\nDi.–Fr.\n\n\n7–21 Uhr\n\n\n"
    "Sa.–So.\n\n\n8–17 Uhr\n\n\nÖffnungszeiten Feiertage\n\n"
    "An folgenden Tagen sind das Hallenbad und die Sauna geschlossen:\n\n"
    "24.12.2026 - Heiligabend\n25.12.2026 - 1. Weihnachtsfeiertag\n\n"
    "Schwimmkurse\n\nAnmeldung ab 05.01.2027 - Kursstart im Februar\n\n"
    "Kursgebühr: 90,00 €\n"
)


class TestParseClosedDates:
    def test_reads_all_dates_from_the_real_page(self):
        closed = gi.parse_closed_dates(PAGE)
        assert len(closed) == 12
        assert datetime.date(2026, 11, 1) in closed
        assert datetime.date(2027, 5, 6) in closed

    def test_all_dates_are_bw_holidays_or_site_closures(self):
        closed = gi.parse_closed_dates(PAGE)
        recurring = {(12, 24), (12, 31)}
        for day in closed:
            assert gi.is_public_holiday(day) or (day.month, day.day) in recurring

    def test_dates_outside_the_closure_block_are_ignored(self):
        closed = gi.parse_closed_dates(PAGE_WITH_DECOY)
        assert closed == {datetime.date(2026, 12, 24), datetime.date(2026, 12, 25)}
        assert datetime.date(2027, 1, 5) not in closed  # "Kursstart"-Datum

    def test_page_without_closure_block_yields_nothing(self):
        assert gi.parse_closed_dates("Öffnungszeiten\n\nMo. 14–21 Uhr\n") == set()

    def test_empty_input(self):
        assert gi.parse_closed_dates("") == set()

    def test_range_notation_is_expanded(self):
        page = (
            "An folgenden Tagen sind das Hallenbad und die Sauna geschlossen:\n\n"
            "24.12.2026 - 26.12.2026\n"
        )
        assert gi.parse_closed_dates(page) == {
            datetime.date(2026, 12, 24),
            datetime.date(2026, 12, 25),
            datetime.date(2026, 12, 26),
        }

    def test_invalid_dates_are_skipped(self):
        page = (
            "An folgenden Tagen sind das Hallenbad und die Sauna geschlossen:\n\n"
            "31.02.2027 - gibt es nicht\n24.12.2026 - Heiligabend\n"
        )
        assert gi.parse_closed_dates(page) == {datetime.date(2026, 12, 24)}


class TestFallbackClosedDates:
    def test_current_window_matches_the_scraped_list(self):
        # Fenster groß genug, um alle Schließtage der Seite zu umfassen
        scraped = gi.parse_closed_dates(PAGE)
        computed = gi.fallback_closed_dates(datetime.date(2026, 10, 1), 260)
        assert scraped <= computed

    def test_works_after_the_old_hardcoded_list(self):
        # Die frühere Liste endete am 06.05.2027.
        computed = gi.fallback_closed_dates(datetime.date(2032, 1, 1), 400)
        assert datetime.date(2032, 1, 6) in computed
        assert datetime.date(2032, 12, 25) in computed
        assert datetime.date(2032, 12, 24) in computed  # Betriebsferien
        assert datetime.date(2032, 12, 31) in computed

    def test_holy_evening_is_not_a_public_holiday(self):
        assert gi.is_public_holiday(datetime.date(2032, 12, 24)) is False
        assert datetime.date(2032, 12, 24) in gi.fallback_closed_dates(
            datetime.date(2032, 12, 1), 40
        )

    def test_window_is_respected(self):
        assert gi.fallback_closed_dates(datetime.date(2032, 7, 1), 5) == set()


class TestFormatClock:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("9", "09:00"), ("14", "14:00"), ("9:30", "09:30"), ("23:05", "23:05"), ("24", "24:00")],
    )
    def test_values(self, value, expected):
        assert gi._format_clock(value) == expected

    def test_invalid_raises_schedule_error(self):
        with pytest.raises(gi.ScheduleError):
            gi._format_clock("kaputt")


class TestParseWeekdayHours:
    def test_real_page_hours(self):
        found = gi.parse_weekday_hours(
            PAGE,
            {
                "mo": r"Mo\.\s*(\d{1,2}(?::\d{2})?)\s*[–-]\s*(\d{1,2}(?::\d{2})?)\s*Uhr",
                "difr": r"Di\.[–-]Fr\.\s*(\d{1,2}(?::\d{2})?)\s*[–-]\s*(\d{1,2}(?::\d{2})?)\s*Uhr",
                "saso": r"Sa\.[–-]So\.\s*(\d{1,2}(?::\d{2})?)\s*[–-]\s*(\d{1,2}(?::\d{2})?)\s*Uhr",
            },
        )
        assert found == {
            "mo": ("14:00", "21:00"),
            "difr": ("07:00", "21:00"),
            "saso": ("08:00", "17:00"),
        }

    def test_half_hours_are_no_longer_lost(self):
        page = "Öffnungszeiten\n\nMo.\n\n9:30 – 18:30 Uhr\n"
        pattern = r"Mo\.\s*(\d{1,2}:\d{2})\s*[–-]\s*(\d{1,2}:\d{2})\s*Uhr"
        assert gi.parse_weekday_hours(page, {"mo": pattern}) == {"mo": ("09:30", "18:30")}

    def test_missing_patterns_are_simply_absent(self):
        pattern = r"Mo\.\s*(\d{1,2})\s*[–-]\s*(\d{1,2})\s*Uhr"
        assert gi.parse_weekday_hours("kein Text", {"mo": pattern}) == {}


class TestFetchBoeblingenEvents:
    def test_uses_hours_and_closure_list_from_the_page(self):
        payloads = {gi.BOEBLINGEN_URL: ("boeblingen_page.txt", "text/html")}
        with httpx.Client(transport=httpx.MockTransport(make_handler(payloads))) as client:
            events = gi.fetch_boeblingen_events(
                client, datetime.date(2026, 10, 22), 60, log=lambda *a: None
            )
        days = {event.get("dtstart").dt.date() for event in events}
        assert datetime.date(2026, 11, 1) not in days  # Allerheiligen
        monday = next(
            event for event in events
            if event.get("dtstart").dt.date() == datetime.date(2026, 10, 26)
        )
        assert monday.get("dtstart").dt.hour == 14  # Montag laut Seite
        assert monday.get("dtend").dt.hour == 21

    def test_weekend_hours_from_the_page(self):
        payloads = {gi.BOEBLINGEN_URL: ("boeblingen_page.txt", "text/html")}
        with httpx.Client(transport=httpx.MockTransport(make_handler(payloads))) as client:
            events = gi.fetch_boeblingen_events(
                client, datetime.date(2026, 10, 22), 7, log=lambda *a: None
            )
        saturday = next(
            event for event in events
            if event.get("dtstart").dt.date() == datetime.date(2026, 10, 24)
        )
        assert (saturday.get("dtstart").dt.hour, saturday.get("dtend").dt.hour) == (8, 17)

    def test_broken_page_falls_back_to_computed_closures(self, log_calls):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Serverfehler")

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            events = gi.fetch_boeblingen_events(
                client,
                datetime.date(2026, 10, 22),
                60,
                log=lambda *a: log_calls.append(" ".join(map(str, a))),
            )
        days = {event.get("dtstart").dt.date() for event in events}
        assert datetime.date(2026, 11, 1) not in days
        assert any("hinterlegte Daten" in message for message in log_calls)
        assert events  # trotz Fehler werden Termine erzeugt

    def test_default_schedule_without_scrape(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Serverfehler")

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            events = gi.fetch_boeblingen_events(
                client, datetime.date(2026, 12, 21), 1, log=lambda *a: None
            )
        # Montag: Standard 14:00-21:00
        assert (events[0].get("dtstart").dt.hour, events[0].get("dtend").dt.hour) == (14, 21)
