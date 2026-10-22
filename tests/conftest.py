"""Gemeinsame Test-Hilfen: Pfade, Mock-Transport, Hilfsfunktionen.

Alle Tests laufen offline: die HTTP-Antworten kommen aus ``tests/fixtures``
und werden über ``httpx.MockTransport`` eingespeist.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import generate_ical as gi  # noqa: E402  (Pfad muss vorher gesetzt sein)


def read_fixture(name: str) -> str:
    """Fixture einlesen und Zeilenenden auf LF normalisieren."""
    return (FIXTURES / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def as_html(text: str) -> str:
    """Seitentext als minimales HTML, damit ``get_text`` ihn unverändert liefert."""
    return f"<html><body><pre>{text}</pre></body></html>"


@pytest.fixture
def payloads() -> dict[str, tuple[str, str]]:
    """URL -> (Fixture-Datei, Content-Type) für die abgefragten Quellen."""
    return {
        gi.STUTTGART_URL: ("stuttgart_das_leuze.json", "application/json"),
        gi.BOEBLINGEN_URL: ("boeblingen_page.txt", "text/html"),
        gi.SINDELFINGEN_URL: ("sindelfingen_page.txt", "text/html"),
    }


def make_handler(payloads: dict[str, tuple[str, str]]):
    """httpx-Handler, der die Fixtures ausliefert (Query-Strings werden ignoriert)."""

    def handler(request: httpx.Request) -> httpx.Response:
        key = str(request.url).split("?")[0]
        if key not in payloads:
            return httpx.Response(404, text="nicht gefunden")
        name, content_type = payloads[key]
        body = read_fixture(name)
        if content_type == "application/json":
            return httpx.Response(200, text=body, headers={"content-type": content_type})
        return httpx.Response(200, text=as_html(body), headers={"content-type": content_type})

    return handler


@pytest.fixture
def mock_client(payloads):
    """httpx-Client mit Mock-Transport für alle drei Quellen."""
    with httpx.Client(transport=httpx.MockTransport(make_handler(payloads))) as client:
        yield client


@pytest.fixture
def log_calls() -> list[str]:
    """Liste, in die Tests ihre Logausgaben schreiben können."""
    return []


def log_to(store: list[str]):
    """Logfunktion für die Quellen, die in ``store`` schreibt."""

    def log(*args, **kwargs) -> None:
        store.append(" ".join(str(arg) for arg in args))

    return log
