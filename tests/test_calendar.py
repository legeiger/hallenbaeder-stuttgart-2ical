"""Tests für iCal-Erzeugung: UID, Termine, Sortierung, Datei schreiben."""

from __future__ import annotations

import datetime

import pytest

import generate_ical as gi


def make_event(summary="Bad", start=datetime.datetime(2026, 10, 22, 8, 0, tzinfo=gi.TZ),
               end=datetime.datetime(2026, 10, 22, 18, 0, tzinfo=gi.TZ), **kwargs):
    defaults = dict(description="Beschreibung", location="Ort", url="https://example.org/")
    defaults.update(kwargs)
    return gi.create_event(summary=summary, start_dt=start, end_dt=end, **defaults)


class TestEventUid:
    def test_stable_for_the_same_event(self):
        first = make_event()
        second = make_event()
        assert first.get("uid") == second.get("uid")

    def test_differs_for_other_times(self):
        other = make_event(start=datetime.datetime(2026, 10, 23, 8, 0, tzinfo=gi.TZ))
        assert make_event().get("uid") != other.get("uid")

    def test_differs_for_other_titles(self):
        assert make_event("A").get("uid") != make_event("B").get("uid")

    def test_is_a_uuid5_string(self):
        uid = str(make_event().get("uid"))
        assert len(uid) == 36 and uid.count("-") == 4


class TestCreateEvent:
    def test_all_fields_are_set(self):
        event = make_event()
        assert str(event.get("summary")) == "Bad"
        assert str(event.get("description")) == "Beschreibung"
        assert str(event.get("location")) == "Ort"
        assert str(event.get("url")) == "https://example.org/"
        assert event.get("dtstart").dt.tzinfo is not None

    def test_optional_fields_can_be_empty(self):
        event = gi.create_event(
            summary="Bad",
            start_dt=datetime.datetime(2026, 10, 22, 8, 0, tzinfo=gi.TZ),
            end_dt=datetime.datetime(2026, 10, 22, 18, 0, tzinfo=gi.TZ),
            description="",
            location="",
        )
        assert event.get("description") is None
        assert event.get("location") is None
        assert event.get("url") is None

    def test_dtstamp_defaults_to_now(self):
        event = make_event()
        stamp = event.get("dtstamp").dt
        assert stamp.tzinfo is not None
        assert abs((datetime.datetime.now(datetime.timezone.utc) - stamp).total_seconds()) < 60

    def test_fixed_dtstamp_is_used(self):
        stamp = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
        assert make_event(dtstamp=stamp).get("dtstamp").dt == stamp


class TestSortAndDedupe:
    def test_events_are_sorted_by_start(self):
        early = make_event("Frueh", start=datetime.datetime(2026, 10, 22, 6, 0, tzinfo=gi.TZ))
        late = make_event("Spaet", start=datetime.datetime(2026, 10, 22, 20, 0, tzinfo=gi.TZ))
        assert [str(e.get("summary")) for e in gi.sort_events([late, early])] == ["Frueh", "Spaet"]

    def test_duplicates_are_removed(self):
        assert len(gi.dedupe_events([make_event(), make_event()])) == 1

    def test_dedupe_keeps_order(self):
        first = make_event("A", start=datetime.datetime(2026, 10, 22, 6, 0, tzinfo=gi.TZ))
        second = make_event("B", start=datetime.datetime(2026, 10, 22, 8, 0, tzinfo=gi.TZ))
        assert gi.dedupe_events([first, second, first]) == [first, second]

    def test_distinct_events_survive(self):
        events = [make_event("A"), make_event("B")]
        assert len(gi.dedupe_events(events)) == 2


class TestBuildCalendar:
    def test_metadata(self):
        calendar = gi.build_calendar([])
        assert str(calendar.get("prodid")).startswith("-//Hallenbaeder")
        assert str(calendar.get("version")) == "2.0"
        assert str(calendar.get("x-wr-timezone")) == "Europe/Berlin"
        assert "Hallenbäder".encode() in calendar.to_ical()
        assert "Stuttgart".encode() in calendar.to_ical()

    def test_components_are_sorted_and_deduplicated(self):
        early = make_event("Frueh", start=datetime.datetime(2026, 10, 22, 6, 0, tzinfo=gi.TZ))
        late = make_event("Spaet", start=datetime.datetime(2026, 10, 22, 20, 0, tzinfo=gi.TZ))
        calendar = gi.build_calendar([late, early, late])
        summaries = [
            str(component.get("summary")) for component in calendar.subcomponents
        ]
        assert summaries == ["Frueh", "Spaet"]

    def test_serialisation_contains_events(self):
        data = gi.build_calendar([make_event()]).to_ical()
        assert b"BEGIN:VCALENDAR" in data
        assert data.count(b"BEGIN:VEVENT") == 1
        assert "TZID=Europe/Berlin".encode() in data


class TestWriteCalendar:
    def test_writes_a_new_file(self, tmp_path):
        target = tmp_path / "out" / "hallenbaeder.ics"
        assert gi.write_calendar(gi.build_calendar([make_event()]), target) is True
        assert target.exists()

    def test_does_not_rewrite_identical_content(self, tmp_path):
        target = tmp_path / "hallenbaeder.ics"
        calendar = gi.build_calendar([make_event()])
        assert gi.write_calendar(calendar, target) is True
        before = target.stat().st_mtime_ns
        assert gi.write_calendar(calendar, target) is False
        assert target.stat().st_mtime_ns == before

    def test_force_writes_anyway(self, tmp_path):
        target = tmp_path / "hallenbaeder.ics"
        calendar = gi.build_calendar([make_event()])
        gi.write_calendar(calendar, target)
        assert gi.write_calendar(calendar, target, force=True) is True

    def test_changed_content_is_written(self, tmp_path):
        target = tmp_path / "hallenbaeder.ics"
        gi.write_calendar(gi.build_calendar([make_event("A")]), target)
        assert gi.write_calendar(gi.build_calendar([make_event("B")]), target) is True


class TestResolveDtstamp:
    @pytest.mark.parametrize("value", [None, "now", "NOW"])
    def test_now(self, value):
        assert gi.resolve_dtstamp(value) is None

    def test_start_marker(self):
        assert gi.resolve_dtstamp("start") == "start"

    def test_iso_timestamp(self):
        parsed = gi.resolve_dtstamp("2026-01-01T12:00:00+00:00")
        assert parsed == datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)

    def test_naive_timestamp_gets_utc(self):
        parsed = gi.resolve_dtstamp("2026-01-01T12:00:00")
        assert parsed.tzinfo == datetime.timezone.utc

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            gi.resolve_dtstamp("vorgestern")
