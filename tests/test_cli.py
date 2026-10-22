"""Tests für die Kommandozeile (main, Schalter, Rückgabewerte)."""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import make_handler

import generate_ical as gi

# Vor jedem Monkeypatch sichern: sonst würde ein zweiter Patch den ersten
# "echten" Client als Real-Client übernehmen.
REAL_CLIENT = httpx.Client


@pytest.fixture
def workdir(monkeypatch, tmp_path, payloads):
    """Arbeitsverzeichnis mit Fixture-Antworten für alle Quellen."""
    transport = httpx.MockTransport(make_handler(payloads))

    def fake_client(*args, **kwargs):
        kwargs.pop("transport", None)
        return REAL_CLIENT(transport=transport, **kwargs)

    monkeypatch.setattr(gi.httpx, "Client", fake_client)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def failing_sources(monkeypatch):
    """Alle Quellen scheitern auf Quellebene (keine Ersatzdaten möglich)."""

    def boom(*args, **kwargs):
        raise RuntimeError("Quelle nicht erreichbar")

    monkeypatch.setattr(gi, "FETCHERS", {name: boom for name in gi.SOURCE_NAMES})


def count_events(path) -> int:
    return path.read_bytes().count(b"BEGIN:VEVENT")


class TestDefaultRun:
    def test_writes_the_calendar(self, workdir, capsys):
        assert gi.main([]) == 0
        target = workdir / "hallenbaeder.ics"
        assert target.exists()
        assert count_events(target) > 0
        assert "Termine in 'hallenbaeder.ics' gespeichert" in capsys.readouterr().out

    def test_output_flag(self, workdir):
        assert gi.main(["--output", "anderes.ics"]) == 0
        assert (workdir / "anderes.ics").exists()
        assert not (workdir / "hallenbaeder.ics").exists()

    def test_output_in_subdirectory(self, workdir):
        assert gi.main(["--output", "unter/ordner/out.ics"]) == 0
        assert (workdir / "unter" / "ordner" / "out.ics").exists()

    def test_days_flag_limits_the_window(self, workdir, capsys):
        assert gi.main(["--days", "3", "--source", "sindelfingen"]) == 0
        assert count_events(workdir / "hallenbaeder.ics") == 3

    def test_quiet_suppresses_output(self, workdir, capsys):
        gi.main(["--quiet"])
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""


class TestSourceSelection:
    def test_single_source(self, workdir, capsys):
        assert gi.main(["--source", "boeblingen", "--json"]) == 0
        summary = json.loads(capsys.readouterr().out)
        assert set(summary["events"]) == {"boeblingen"}
        assert summary["events"]["boeblingen"] > 0

    def test_repeated_sources(self, workdir, capsys):
        gi.main(["--source", "boeblingen", "--source", "sindelfingen", "--json"])
        summary = json.loads(capsys.readouterr().out)
        assert set(summary["events"]) == {"boeblingen", "sindelfingen"}

    def test_all_keyword(self, workdir, capsys):
        gi.main(["--source", "all", "--json"])
        summary = json.loads(capsys.readouterr().out)
        assert set(summary["events"]) == set(gi.SOURCE_NAMES)

    def test_unknown_source_is_rejected(self, workdir):
        with pytest.raises(SystemExit) as excinfo:
            gi.main(["--source", "dresden"])
        assert excinfo.value.code == 2


class TestJsonOutput:
    def test_stdout_is_json_and_progress_goes_to_stderr(self, workdir, capsys):
        assert gi.main(["--json"]) == 0
        captured = capsys.readouterr()
        summary = json.loads(captured.out)
        assert summary["total"] > 0
        assert summary["written"] is True
        assert summary["changed"] is True
        assert summary["days"] == gi.DAYS_AHEAD
        assert summary["output"] == "hallenbaeder.ics"
        assert captured.err  # Fortschritt auf stderr

    def test_total_matches_events_in_file(self, workdir, capsys):
        gi.main(["--json"])
        summary = json.loads(capsys.readouterr().out)
        assert summary["total"] == count_events(workdir / "hallenbaeder.ics")


class TestCheckAndDryRun:
    def test_check_without_file_reports_a_change(self, workdir):
        assert gi.main(["--check"]) == 3
        assert not (workdir / "hallenbaeder.ics").exists()

    def test_check_after_a_run_is_clean(self, workdir, capsys):
        gi.main(["--dtstamp", "start"])
        capsys.readouterr()
        assert gi.main(["--check", "--dtstamp", "start"]) == 0
        assert "ist aktuell" in capsys.readouterr().out

    def test_dry_run_writes_nothing(self, workdir, capsys):
        assert gi.main(["--dry-run"]) == 0
        assert not (workdir / "hallenbaeder.ics").exists()
        assert "Probelauf" in capsys.readouterr().out


class TestDtstamp:
    def test_start_makes_the_file_reproducible(self, workdir):
        gi.main(["--dtstamp", "start"])
        first = (workdir / "hallenbaeder.ics").read_bytes()
        gi.main(["--dtstamp", "start"])
        assert (workdir / "hallenbaeder.ics").read_bytes() == first

    def test_fixed_timestamp_is_written(self, workdir):
        gi.main(["--dtstamp", "2026-01-01T00:00:00+00:00"])
        data = (workdir / "hallenbaeder.ics").read_bytes()
        assert b"DTSTAMP:20260101T000000Z" in data

    def test_invalid_timestamp(self, workdir, capsys):
        assert gi.main(["--dtstamp", "gestern"]) == 2
        assert "dtstamp" in capsys.readouterr().err

    def test_unchanged_file_is_not_rewritten(self, workdir, capsys):
        gi.main(["--dtstamp", "start"])
        capsys.readouterr()
        assert gi.main(["--dtstamp", "start"]) == 0
        assert "unverändert" in capsys.readouterr().out

    def test_force_writes_anyway(self, workdir, capsys):
        gi.main(["--dtstamp", "start"])
        capsys.readouterr()
        assert gi.main(["--dtstamp", "start", "--force"]) == 0
        assert "gespeichert" in capsys.readouterr().out


class TestArgumentErrors:
    @pytest.mark.parametrize("days", ["0", "-5"])
    def test_invalid_days(self, workdir, capsys, days):
        assert gi.main(["--days", days]) == 2
        assert "--days" in capsys.readouterr().err

    def test_non_numeric_days(self, workdir):
        with pytest.raises(SystemExit) as excinfo:
            gi.main(["--days", "viele"])
        assert excinfo.value.code == 2

    def test_version(self, workdir, capsys):
        with pytest.raises(SystemExit) as excinfo:
            gi.main(["--version"])
        assert excinfo.value.code == 0
        assert gi.__version__ in capsys.readouterr().out


class TestFailureHandling:
    def test_all_sources_failing_keeps_the_existing_file(self, workdir, monkeypatch, capsys):
        target = workdir / "hallenbaeder.ics"
        target.write_bytes(b"BESTEHEND")
        failing_sources(monkeypatch)
        assert gi.main([]) == 1
        assert target.read_bytes() == b"BESTEHEND"
        assert "keine Termine" in capsys.readouterr().err

    def test_all_sources_failing_json(self, workdir, monkeypatch, capsys):
        failing_sources(monkeypatch)
        assert gi.main(["--json"]) == 1
        summary = json.loads(capsys.readouterr().out)
        assert summary["total"] == 0
        assert set(summary["errors"]) == set(gi.SOURCE_NAMES)

    def test_http_outage_uses_fallback_data_and_reports_the_error(
        self, workdir, payloads, capsys
    ):
        # Keine Quelle erreichbar: Böblingen und Sindelfingen haben hinterlegte
        # Zeiten, Stuttgart nicht – der Kalender entsteht trotzdem.
        payloads.clear()  # der Mock-Handler antwortet jetzt mit 404
        assert gi.main([]) == 0
        output = capsys.readouterr().out
        assert "fehlgeschlagene Quellen: stuttgart" in output
        assert count_events(workdir / "hallenbaeder.ics") > 0

    def test_partial_failure_still_writes_the_calendar(self, workdir, payloads, capsys):
        del payloads[gi.STUTTGART_URL]
        assert gi.main([]) == 0
        output = capsys.readouterr().out
        assert "fehlgeschlagene Quellen: stuttgart" in output
        assert (workdir / "hallenbaeder.ics").exists()

    def test_partial_failure_is_visible_in_json(self, workdir, payloads, capsys):
        del payloads[gi.STUTTGART_URL]
        assert gi.main(["--json"]) == 0
        summary = json.loads(capsys.readouterr().out)
        assert set(summary["errors"]) == {"stuttgart"}
        assert summary["events"]["sindelfingen"] > 0


class TestHttpSettings:
    def test_timeout_and_user_agent_are_passed(self, workdir, monkeypatch, payloads):
        seen = {}
        transport = httpx.MockTransport(make_handler(payloads))

        def fake_client(*args, **kwargs):
            kwargs.pop("transport", None)
            seen.update(kwargs)
            return REAL_CLIENT(transport=transport, **kwargs)

        monkeypatch.setattr(gi.httpx, "Client", fake_client)
        gi.main(["--timeout", "7.5", "--user-agent", "Test-Agent", "--source", "sindelfingen"])
        assert seen["timeout"] == 7.5
        assert seen["headers"]["User-Agent"] == "Test-Agent"


class TestArgParser:
    def test_defaults(self):
        args = gi.build_arg_parser().parse_args([])
        assert args.days == gi.DAYS_AHEAD
        assert str(args.output) == "hallenbaeder.ics"
        assert args.source is None
        assert args.dtstamp == "now"
        assert args.check is False
        assert args.force is False
        assert args.dry_run is False
        assert args.json is False
        assert args.quiet is False

    def test_help_mentions_defaults(self):
        help_text = gi.build_arg_parser().format_help()
        assert "hallenbaeder.ics" in help_text
        assert str(gi.DAYS_AHEAD) in help_text
