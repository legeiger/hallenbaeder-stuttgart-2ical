"""Tests für die Stuttgarter Bäder (JSON-API).

Fixtures: ``stuttgart_das_leuze.json`` ist der unveränderte API-Eintrag des
Bades "DAS LEUZE" – inklusive Sauna und Kinderbereich.
"""

from __future__ import annotations

import datetime
import json

import httpx
import pytest
from conftest import make_handler, read_fixture

import generate_ical as gi

LEUZE = json.loads(read_fixture("stuttgart_das_leuze.json"))[0]


class TestExtractSlotsForDay:
    def test_single_window(self):
        rule = {"mo": {"from": "08:00", "to": "18:00"}}
        assert gi.extract_slots_for_day(rule, "mo") == [("08:00", "18:00")]

    def test_multiple_windows_are_sorted_numerically(self):
        rule = {
            "mo2": {"from": "15:00", "to": "17:00"},
            "mo": {"from": "06:00", "to": "08:00"},
            "mo1": {"from": "09:00", "to": "11:00"},
        }
        assert gi.extract_slots_for_day(rule, "mo") == [
            ("06:00", "08:00"),
            ("09:00", "11:00"),
            ("15:00", "17:00"),
        ]

    def test_more_than_three_windows_are_no_longer_dropped(self):
        # Früher wurden nur "", "1" und "2" gelesen – mo3 verschwand.
        rule = {
            "mo": {"from": "06:00", "to": "08:00"},
            "mo1": {"from": "09:00", "to": "11:00"},
            "mo2": {"from": "12:00", "to": "14:00"},
            "mo3": {"from": "15:00", "to": "17:00"},
        }
        assert len(gi.extract_slots_for_day(rule, "mo")) == 4

    def test_duplicate_windows_are_kept_once(self):
        rule = {
            "mo": {"from": "08:00", "to": "18:00"},
            "mo1": {"from": "08:00", "to": "18:00"},
        }
        assert gi.extract_slots_for_day(rule, "mo") == [("08:00", "18:00")]

    def test_only_requested_weekday(self):
        rule = {"mo": {"from": "08:00", "to": "18:00"}, "di": {"from": "09:00", "to": "17:00"}}
        assert gi.extract_slots_for_day(rule, "di") == [("09:00", "17:00")]

    @pytest.mark.parametrize(
        "value",
        [
            None,
            {},
            {"from": "", "to": "18:00"},
            {"from": "08:00"},
            {"from": "kaputt", "to": "18:00"},
            {"from": "08:00", "to": "24:30"},
            "08:00-18:00",
        ],
    )
    def test_unusable_entries_are_skipped(self, value):
        assert gi.extract_slots_for_day({"mo": value}, "mo") == []

    def test_unrelated_keys_are_ignored(self):
        rule = {
            "mo": {"from": "08:00", "to": "18:00"},
            "type": "i",
            "moX": {"from": "1", "to": "2"},
        }
        assert gi.extract_slots_for_day(rule, "mo") == [("08:00", "18:00")]


class TestAsDictList:
    def test_none_does_not_raise(self):
        # bad.get("lookups", {}).get(...) stürzte bei null ab.
        assert gi._as_dict_list(None) == []

    def test_dict_becomes_single_item_list(self):
        assert gi._as_dict_list({"a": 1}) == [{"a": 1}]

    def test_list_filters_non_dicts(self):
        assert gi._as_dict_list([{"a": 1}, None, "x", 3]) == [{"a": 1}]

    def test_other_types(self):
        assert gi._as_dict_list("x") == []
        assert gi._as_dict_list(7) == []


class TestRuleValidOn:
    def test_without_validity_always_applies(self):
        assert gi._rule_valid_on({}, datetime.date(2030, 1, 1)) is True

    def test_inside_validity(self):
        rule = {"validity": {"from": "2026-01-01", "to": "2026-12-31"}}
        assert gi._rule_valid_on(rule, datetime.date(2026, 6, 1)) is True

    def test_outside_validity(self):
        rule = {"validity": {"from": "2026-01-01", "to": "2026-12-31"}}
        assert gi._rule_valid_on(rule, datetime.date(2027, 1, 1)) is False

    def test_open_ended_validity(self):
        assert gi._rule_valid_on({"validity": {"from": "2026-01-01"}}, datetime.date(2030, 1, 1))
        assert not gi._rule_valid_on({"validity": {"to": "2025-01-01"}}, datetime.date(2026, 1, 1))

    def test_malformed_dates_do_not_raise(self):
        rule = {"validity": {"from": "kaputt", "to": "auch kaputt"}}
        assert gi._rule_valid_on(rule, datetime.date(2026, 1, 1)) is True


class TestGetStuttgartSlotsForDate:
    def test_usually_rule_matches_weekday(self):
        bh = {"usually_bhpool": [{"mo": {"from": "06:00", "to": "21:00"}}]}
        assert gi.get_stuttgart_slots_for_date(
            bh, "usually_bhpool", "holiday_bhpool", datetime.date(2026, 10, 26)
        ) == [("06:00", "21:00")]

    def test_holiday_rule_wins_over_usually(self):
        bh = {
            "usually_bhpool": [{"mo": {"from": "06:00", "to": "21:00"}}],
            "holiday_bhpool": [
                {"validity": {"from": "2026-10-26", "to": "2026-10-26"},
                 "mo": {"from": "10:00", "to": "14:00"}}
            ],
        }
        assert gi.get_stuttgart_slots_for_date(
            bh, "usually_bhpool", "holiday_bhpool", datetime.date(2026, 10, 26)
        ) == [("10:00", "14:00")]

    def test_closed_holiday_rule_means_no_slots(self):
        bh = {
            "usually_bhpool": [{"mo": {"from": "06:00", "to": "21:00"}}],
            "holiday_bhpool": [
                {"validity": {"from": "2026-10-26", "to": "2026-10-26"}, "closed": True}
            ],
        }
        assert gi.get_stuttgart_slots_for_date(
            bh, "usually_bhpool", "holiday_bhpool", datetime.date(2026, 10, 26)
        ) == []

    def test_holiday_rule_without_slots_for_weekday_is_closed(self):
        bh = {
            "usually_bhpool": [{"mo": {"from": "06:00", "to": "21:00"}}],
            "holiday_bhpool": [
                {"validity": {"from": "2026-10-26", "to": "2026-10-26"},
                 "di": {"from": "10:00", "to": "14:00"}}
            ],
        }
        assert gi.get_stuttgart_slots_for_date(
            bh, "usually_bhpool", "holiday_bhpool", datetime.date(2026, 10, 26)
        ) == []

    def test_expired_usually_rule_is_skipped(self):
        expired = {"validity": {"from": "2020-01-01", "to": "2021-01-01"},
                   "mo": {"from": "1", "to": "2"}}
        current = {"validity": {"from": "2026-01-01", "to": "2030-12-31"},
                   "mo": {"from": "06:00", "to": "21:00"}}
        bh = {"usually_bhpool": [expired, current]}
        assert gi.get_stuttgart_slots_for_date(
            bh, "usually_bhpool", "holiday_bhpool", datetime.date(2026, 10, 26)
        ) == [("06:00", "21:00")]

    def test_missing_categories_yield_no_slots(self):
        assert gi.get_stuttgart_slots_for_date(
            {}, "usually_bhpool", "holiday_bhpool", datetime.date(2026, 10, 26)
        ) == []

    def test_closed_flag_in_usually_rule_is_skipped(self):
        bh = {"usually_bhpool": [{"closed": True, "mo": {"from": "06:00", "to": "21:00"}}]}
        assert gi.get_stuttgart_slots_for_date(
            bh, "usually_bhpool", "holiday_bhpool", datetime.date(2026, 10, 26)
        ) == []


class TestRealPayload:
    """Der echte API-Eintrag des LEUZE-Bades."""

    def test_fixture_has_the_expected_categories(self):
        assert gi.STUTTGART_CATEGORIES[1] == (
            "usually_bhpoolchildren",
            "holiday_bhpoolchildren",
            "Kinderbereich",
        )
        assert "usually_bhpoolchildren" in LEUZE["businesshours"]

    def test_children_area_hours_differ_from_pool_hours(self):
        bh = LEUZE["businesshours"]
        monday = datetime.date(2026, 10, 26)  # Montag
        pool = gi.get_stuttgart_slots_for_date(bh, "usually_bhpool", "holiday_bhpool", monday)
        kids = gi.get_stuttgart_slots_for_date(
            bh, "usually_bhpoolchildren", "holiday_bhpoolchildren", monday
        )
        assert kids == [("08:00", "21:00")]
        assert pool == [("06:00", "21:00")]
        assert kids != pool  # eigener Termin ist berechtigt

    def test_location_and_url_come_from_the_entry(self):
        location, url = gi._stuttgart_location(LEUZE)
        assert location.startswith("DAS LEUZE,")
        assert url.startswith("http")

    def test_description_contains_website(self):
        _, url = gi._stuttgart_location(LEUZE)
        assert "Webseite:" in gi._stuttgart_description(LEUZE, url)

    def test_null_lookups_does_not_crash(self):
        entry = {**LEUZE, "lookups": None}
        _, url = gi._stuttgart_location(entry)
        assert gi._stuttgart_description(entry, url) == f"Webseite: {url}"

    def test_fetcher_creates_events_for_all_categories(self):
        payloads = {gi.STUTTGART_URL: ("stuttgart_das_leuze.json", "application/json")}
        with httpx.Client(transport=httpx.MockTransport(make_handler(payloads))) as client:
            events = gi.fetch_stuttgart_events(
                client, datetime.date(2026, 10, 22), 7, log=lambda *a: None
            )
        summaries = {str(event.get("summary")) for event in events}
        assert "DAS LEUZE" in summaries
        assert "DAS LEUZE (Kinderbereich)" in summaries
        assert "DAS LEUZE (Sauna)" in summaries

    def test_fetcher_skips_broken_entries_instead_of_failing(self):
        payload = [
            {
                "name": "Kaputt",
                "lookups": None,
                "communications": None,
                "building": None,
                "businesshours": None,
            },
            {**LEUZE},
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            events = gi.fetch_stuttgart_events(
                client, datetime.date(2026, 10, 22), 3, log=lambda *a: None
            )
        assert events

    def test_non_list_payload_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"nicht": "erwartet"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError):
                gi.fetch_stuttgart_events(
                    client, datetime.date(2026, 10, 22), 3, log=lambda *a: None
                )
