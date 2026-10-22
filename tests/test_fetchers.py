"""Integrationstest: alle drei Quellen aus den echten Seiten-Fixtures.

Die Zählungen sind Momentaufnahmen der Fixtures (echte Seitendaten,
Stand: Aktualisierung der Fixtures). Sie halten fest, dass keine Termine
verloren gehen, wenn die Erkennung umgebaut wird.
"""

from __future__ import annotations

import datetime

import pytest

import generate_ical as gi

START = datetime.date(2026, 10, 22)
DAYS = 60


@pytest.fixture(scope="module")
def all_events():
    import httpx
    from conftest import make_handler

    payloads = {
        gi.STUTTGART_URL: ("stuttgart_das_leuze.json", "application/json"),
        gi.BOEBLINGEN_URL: ("boeblingen_page.txt", "text/html"),
        gi.SINDELFINGEN_URL: ("sindelfingen_page.txt", "text/html"),
    }
    with httpx.Client(transport=httpx.MockTransport(make_handler(payloads))) as client:
        results = gi.collect_events(client, START, DAYS, log=lambda *a: None)
    return {result.name: result.events for result in results}


class TestCounts:
    def test_every_source_produced_events(self, all_events):
        for name in gi.SOURCE_NAMES:
            assert all_events[name], f"keine Termine für {name}"

    def test_sindelfingen_has_one_event_per_day(self, all_events):
        assert len(all_events["sindelfingen"]) == DAYS

    def test_boeblingen_skips_closed_days(self, all_events):
        # 01.11.2026 (Allerheiligen) ist in der Fixture-Liste der Seite.
        days = {event.get("dtstart").dt.date() for event in all_events["boeblingen"]}
        assert datetime.date(2026, 11, 1) not in days
        assert len(days) == len(all_events["boeblingen"])

    def test_stuttgart_counts_are_stable(self, all_events):
        # Fixture: ein Bad (DAS LEUZE) mit Schwimmbad, Sauna und Kinderbereich,
        # je 60 Tage abzüglich des Schließtags am 16.11.2026 -> 3 x 59 Termine.
        assert len(all_events["stuttgart"]) == 177
        summaries = [str(event.get("summary")) for event in all_events["stuttgart"]]
        assert summaries.count("DAS LEUZE") == 59
        assert summaries.count("DAS LEUZE (Kinderbereich)") == 59
        assert summaries.count("DAS LEUZE (Sauna)") == 59

    def test_children_area_is_included(self, all_events):
        summaries = {str(event.get("summary")) for event in all_events["stuttgart"]}
        assert "DAS LEUZE (Kinderbereich)" in summaries


class TestEventSanity:
    def test_start_before_end(self, all_events):
        for events in all_events.values():
            for event in events:
                assert event.get("dtstart").dt < event.get("dtend").dt

    def test_dates_inside_the_requested_window(self, all_events):
        end = START + datetime.timedelta(days=DAYS - 1)
        for events in all_events.values():
            for event in events:
                assert START <= event.get("dtstart").dt.date() <= end

    def test_unique_uids(self, all_events):
        uids = [str(event.get("uid")) for events in all_events.values() for event in events]
        assert len(uids) == len(set(uids))

    def test_every_event_has_a_timezone(self, all_events):
        for events in all_events.values():
            for event in events:
                assert event.get("dtstart").dt.tzinfo is not None

    def test_events_carry_location_and_description(self, all_events):
        for events in all_events.values():
            for event in events:
                assert str(event.get("summary"))
                assert str(event.get("location"))
                assert str(event.get("description"))


class TestFailures:
    def test_single_source_failure_is_isolated(self, log_calls):
        import httpx
        from conftest import log_to, make_handler

        payloads = {
            gi.BOEBLINGEN_URL: ("boeblingen_page.txt", "text/html"),
            gi.SINDELFINGEN_URL: ("sindelfingen_page.txt", "text/html"),
        }

        def handler(request):
            if "stuttgarterbaeder" in str(request.url):
                raise httpx.ConnectError("kein Netz")
            return make_handler(payloads)(request)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            results = gi.collect_events(client, START, DAYS, log=log_to(log_calls))
        by_name = {result.name: result for result in results}
        assert by_name["stuttgart"].ok is False
        assert "ConnectError" in (by_name["stuttgart"].error or "")
        assert by_name["boeblingen"].ok is True
        assert by_name["sindelfingen"].ok is True
        assert any("FEHLER" in message for message in log_calls)

    def test_unknown_source_name_raises(self, mock_client):
        with pytest.raises(KeyError):
            gi.collect_events(mock_client, START, 1, ["berlin"])
