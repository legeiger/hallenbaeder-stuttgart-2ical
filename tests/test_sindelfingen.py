"""Tests für das Badezentrum Sindelfingen.

Der Kern der Änderung: Feiertags-Öffnungszeiten richten sich nach den
berechneten baden-württembergischen Feiertagen, nicht mehr nach der
Schließtagsliste des Hallenbads Böblingen.
"""

from __future__ import annotations

import datetime

import httpx
import pytest
from conftest import make_handler, read_fixture

import generate_ical as gi

PAGE = read_fixture("sindelfingen_page.txt")


def fetch(start: datetime.date, days: int, payloads=None):
    payloads = payloads or {gi.SINDELFINGEN_URL: ("sindelfingen_page.txt", "text/html")}
    with httpx.Client(transport=httpx.MockTransport(make_handler(payloads))) as client:
        return gi.fetch_sindelfingen_events(client, start, days, log=lambda *a: None)


def hours_per_day(events):
    return {
        event.get("dtstart").dt.date(): (
            event.get("dtstart").dt.hour,
            event.get("dtstart").dt.minute,
            event.get("dtend").dt.hour,
        )
        for event in events
    }


class TestScrapedSchedule:
    def test_weekdays_and_weekend_from_the_page(self):
        events = fetch(datetime.date(2026, 10, 22), 60)
        table = hours_per_day(events)
        assert table[datetime.date(2026, 10, 22)] == (7, 0, 21)  # Donnerstag
        assert table[datetime.date(2026, 10, 24)] == (8, 0, 20)  # Samstag

    def test_one_event_per_day(self):
        assert len(fetch(datetime.date(2026, 10, 22), 60)) == 60


class TestHolidayHours:
    def test_allerheiligen_uses_holiday_hours(self):
        events = fetch(datetime.date(2026, 10, 22), 60)
        # 01.11.2026 ist ein Sonntag, dort gilt ohnehin 08:00-20:00
        assert hours_per_day(events)[datetime.date(2026, 11, 1)] == (8, 0, 20)

    def test_holiday_on_a_weekday_uses_holiday_hours(self):
        # Christi Himmelfahrt 2027-05-06 ist ein Donnerstag (sonst 07:00-21:30)
        events = fetch(datetime.date(2027, 5, 1), 30)
        assert hours_per_day(events)[datetime.date(2027, 5, 6)] == (8, 0, 20)

    def test_holy_evening_uses_the_normal_weekday_schedule(self):
        # 24.12.2026 ist kein gesetzlicher Feiertag; früher griff hier die
        # Schließtagsliste des Hallenbads Böblingen.
        events = fetch(datetime.date(2026, 12, 20), 10)
        assert hours_per_day(events)[datetime.date(2026, 12, 24)] == (7, 0, 21)

    def test_holidays_are_correct_after_the_old_list_expired(self):
        # Vorher endete die Referenzliste am 06.05.2027 -> danach wurden
        # Feiertage stillschweigend wie Werktage behandelt.
        events = fetch(datetime.date(2027, 12, 1), 40)
        table = hours_per_day(events)
        assert table[datetime.date(2027, 12, 25)] == (8, 0, 20)
        assert table[datetime.date(2027, 12, 26)] == (8, 0, 20)
        assert table[datetime.date(2028, 1, 6)] == (8, 0, 20)

    @pytest.mark.parametrize(
        "day",
        [
            datetime.date(2027, 5, 17),  # Pfingstmontag
            datetime.date(2027, 5, 27),  # Fronleichnam
            datetime.date(2032, 5, 6),   # Christi Himmelfahrt, weit in der Zukunft
        ],
    )
    def test_further_holidays(self, day):
        events = fetch(day, 1)
        assert hours_per_day(events)[day] == (8, 0, 20)


class TestFallbacks:
    def test_broken_page_keeps_the_default_schedule(self, log_calls):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="nicht erreichbar")

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            events = gi.fetch_sindelfingen_events(
                client,
                datetime.date(2026, 10, 22),
                2,
                log=lambda *a: log_calls.append(" ".join(map(str, a))),
            )
        assert len(events) == 2
        assert any("hinterlegte Daten" in message for message in log_calls)

    def test_holiday_hours_come_from_the_page(self):
        page = as_page(
            "Montag\n\n07:00 – 21:30 Uhr\n\nFeiertag\n\n09:00 – 18:00 Uhr\n"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=page, headers={"content-type": "text/html"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            events = gi.fetch_sindelfingen_events(
                client, datetime.date(2026, 10, 31), 2, log=lambda *a: None
            )
        table = hours_per_day(events)
        assert table[datetime.date(2026, 11, 1)] == (9, 0, 18)


def as_page(text: str) -> str:
    return f"<html><body><pre>{text}</pre></body></html>"
