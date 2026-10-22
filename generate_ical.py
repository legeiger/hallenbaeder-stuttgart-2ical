"""Erzeugt eine iCal-Datei mit den Öffnungszeiten Stuttgarter Bäder.

Quellen:
  1. Stuttgarter Bäder (JSON-API der stuttgarterbaeder.de)
  2. Hallenbad Böblingen (HTML-Öffnungszeiten + Schließtage-Liste)
  3. Badezentrum Sindelfingen (HTML-Öffnungszeiten)

Die Datei lässt sich weiterhin wie bisher starten::

    python generate_ical.py

Alle Schalter sind optional, die Voreinstellungen entsprechen dem bisherigen
Verhalten (60 Tage Vorlauf, Ausgabe nach ``hallenbaeder.ics`` im
Arbeitsverzeichnis). Ausführliche Hilfe: ``python generate_ical.py --help``.

Fehler einzelner Quellen brechen den Lauf nicht mehr ab: die übrigen Quellen
werden weiterhin geschrieben, die fehlgeschlagene Quelle wird gemeldet.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from icalendar import Calendar, Event

from holidays_bw import is_public_holiday, public_holidays_in_range

__version__ = "1.1.0"

# Zeitzone: die Öffnungszeiten sind Wanduhrzeiten in Berliner Zeit.
# ``zoneinfo`` ersetzt das frühere ``pytz`` (eine Abhängigkeit weniger).
TZ = ZoneInfo("Europe/Berlin")

DAYS_AHEAD = 60  # Zeitraum der Kalendereinträge im Voraus (in Tagen)

# Wochentags-Mapping für die Stuttgarter Bäder JSON-API (Monday = 0 ... Sunday = 6)
WEEKDAY_KEYS = ["mo", "di", "mi", "do", "fr", "sa", "so"]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

STUTTGART_URL = "https://www.stuttgarterbaeder.de/fileadmin/jsonData/baeder.json"
BOEBLINGEN_URL = "https://www.stadtwerke-boeblingen.de/freizeit-baeder/hallenbad"
SINDELFINGEN_URL = "https://badezentrum.de/"

# Kategorien der Stuttgarter Bäder: (Regel "usually_*", Regel "holiday_*", Label).
# "Kinderbereich" fehlte bisher, obwohl die API für das LEUZE eigene Zeiten führt.
STUTTGART_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("usually_bhpool", "holiday_bhpool", "Schwimmbad"),
    ("usually_bhpoolchildren", "holiday_bhpoolchildren", "Kinderbereich"),
    ("usually_bhsauna", "holiday_bhsauna", "Sauna"),
    ("usually_bhsaunawomen", "holiday_bhsaunawomen", "Damensauna"),
    ("usually_bhsaunamen", "holiday_bhsaunamen", "Herrensauna"),
)

SOURCE_NAMES = ("stuttgart", "boeblingen", "sindelfingen")

# Wochentags-Schlüssel für Zeitfenster: mo, mo1, mo2, mo3, ...
_SLOT_KEY_RE = re.compile(r"^(mo|di|mi|do|fr|sa|so)(\d*)$")
_CLOCK_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?$")
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_GERMAN_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")

# Schließtage-Liste auf der Böblinger Seite: "An folgenden Tagen sind das
# Hallenbad und die Sauna geschlossen:" gefolgt von "TT.MM.JJJJ - Anlass".
BOEBLINGEN_CLOSURE_MARKER = "geschlossen:"
# Betriebsferien, die unabhängig von Feiertagen jedes Jahr gelten.
BOEBLINGEN_RECURRING_CLOSURES: tuple[tuple[int, int], ...] = ((12, 24), (12, 31))

BOEBLINGEN_DEFAULT_DESCRIPTION = (
    "Einlass bis 1 Stunde vor Betriebsschluss.\n\n"
    "Preise:\n"
    "- Einzeleintritt: 5,00 € (Ermäßigt: 3,00 €)\n"
    "- Einzeleintritt ab 19 Uhr (Mo.-Fr.): 3,00 € (Ermäßigt: 2,00 €)\n"
    "- 10er Karte: 45,00 € (Ermäßigt: 27,00 €)\n"
    "- Jahreskarte (1 Erw.): 252,00 € (Ermäßigt: 126,00 €)\n"
    "- Jahreskarte Familie: 441,00 € | Mini-Familie: 326,00 €\n\n"
    "Infos: https://www.stadtwerke-boeblingen.de/freizeit-baeder/hallenbad"
)

# Standard-Öffnungszeiten: Mo 14-21, Di-Fr 7-21, Sa-So 8-17
BOEBLINGEN_DEFAULT_SCHEDULE: dict[int, tuple[str, str]] = {
    0: ("14:00", "21:00"),
    1: ("07:00", "21:00"),
    2: ("07:00", "21:00"),
    3: ("07:00", "21:00"),
    4: ("07:00", "21:00"),
    5: ("08:00", "17:00"),
    6: ("08:00", "17:00"),
}

SINDELFINGEN_DEFAULT_DESCRIPTION = (
    "Kassenschluss ist 60 Minuten vor der Schließzeit.\n"
    "Badeschluss ist 15 Minuten vor der Schließzeit.\n\n"
    "Tarife Hallenbad:\n"
    "- Einzelkarte: 5,00 € (Ermäßigt: 3,60 €)\n"
    "- Abendtarif (jeden Tag ab 18 Uhr): 3,60 €\n"
    "- Familienkarte (bis 5 Pers., max. 2 Erw.): 15,00 €\n"
    "- Kinder unter 6 Jahren und Geburtstagskinder (mit Ausweis): frei\n\n"
    "Infos: https://badezentrum.de/"
)

# Standard-Öffnungszeiten: Mo-Fr 07:00-21:30, Sa/So/Feiertag 08:00-20:00
SINDELFINGEN_DEFAULT_SCHEDULE: dict[int, tuple[str, str]] = {
    0: ("07:00", "21:30"),
    1: ("07:00", "21:30"),
    2: ("07:00", "21:30"),
    3: ("07:00", "21:30"),
    4: ("07:00", "21:30"),
    5: ("08:00", "20:00"),
    6: ("08:00", "20:00"),
}
SINDELFINGEN_DEFAULT_HOLIDAY_HOURS = ("08:00", "20:00")

SINDELFINGEN_DAY_NAMES: dict[str, int] = {
    "Montag": 0,
    "Dienstag": 1,
    "Mittwoch": 2,
    "Donnerstag": 3,
    "Freitag": 4,
    "Samstag": 5,
    "Sonntag": 6,
}


class ScheduleError(ValueError):
    """Eine Zeitangabe aus einer Quelle ist nicht verwertbar."""


# ==============================================================================
# Zeit-Helfer
# ==============================================================================
def parse_clock(value: object) -> tuple[int, int] | None:
    """Zerlegt "14:30" (auch "7:00" oder "24:00") in ``(Stunde, Minute)``.

    ``24:00`` ist die übliche Schreibweise für Mitternacht am Tagesende und
    ergibt ``(24, 0)``. Nicht verwertbare Angaben ergeben ``None`` statt einer
    Ausnahme – früher beendete ein einzelnes "24:00" den kompletten Lauf
    (``ValueError: hour must be in 0..23``).
    """
    if isinstance(value, datetime.time):
        return value.hour, value.minute
    if not isinstance(value, str):
        return None

    match = _CLOCK_RE.match(value.strip())
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if hour > 24 or minute > 59 or (hour == 24 and minute != 0):
        return None
    return hour, minute


def parse_date(value: object) -> datetime.date | None:
    """Liest ein Datum aus ``YYYY-MM-DD``, ISO-Datetime oder ``DD.MM.YYYY``.

    Nicht verwertbare Angaben ergeben ``None``; früher löste etwa
    ``2026-01-01T00:00:00`` einen ValueError aus und brach den Lauf ab.
    """
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if not isinstance(value, str):
        return None

    text = value.strip()
    iso = _ISO_DATE_RE.match(text)
    if iso:
        try:
            return datetime.date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None

    german = _GERMAN_DATE_RE.match(text)
    if german:
        try:
            return datetime.date(int(german.group(3)), int(german.group(2)), int(german.group(1)))
        except ValueError:
            return None

    return None


def localize_wall_time(day: datetime.date, hour: int, minute: int = 0) -> datetime.datetime:
    """Wandelt eine Uhrzeit des Tages in einen zonenbehafteten Zeitpunkt um.

    Die Wanduhrzeit wird unverändert übernommen (``fold=0``) und mit
    ``Europe/Berlin`` verknüpft; die iCal-Ausgabe schreibt entsprechend
    ``TZID=Europe/Berlin`` plus Wanduhrzeit. ``hour`` darf 24 sein
    (Mitternacht am Tagesende).

    Hinweis zur Zeitumstellung: In der Sommerzeit-Lücke (02:00–03:00 am letzten
    Märzonntag) existiert die Wanduhrzeit nicht. Das betrifft hier keinen
    Termin, weil alle Bäder frühestens um 06:00 öffnen; eine Uhrzeit aus der
    Lücke würde von Kalenderprogrammen gemäß RFC 5545 nach vorn verschoben.
    """
    naive = datetime.datetime(day.year, day.month, day.day) + datetime.timedelta(
        hours=hour, minutes=minute
    )
    return naive.replace(tzinfo=TZ, fold=0)


def make_aware_dt(day: datetime.date, time_str: str) -> datetime.datetime:
    """Zonenbehafteter Zeitpunkt für eine Uhrzeit am Tag ``day``."""
    clock = parse_clock(time_str)
    if clock is None:
        raise ScheduleError(f"Ungültige Uhrzeit: {time_str!r}")
    return localize_wall_time(day, *clock)


def slot_datetimes(
    day: datetime.date, start_str: str, end_str: str
) -> tuple[datetime.datetime, datetime.datetime] | None:
    """Start- und Endzeitpunkt eines Zeitfensters.

    ``None`` heißt "Fenster verwerfen": ungültige Uhrzeit oder Fenster ohne
    Länge. Endet ein Fenster vor seinem Start (etwa 22:00-02:00 in der Sauna),
    läuft es bewusst über Mitternacht statt einen Termin mit Ende vor Beginn
    zu erzeugen.
    """
    start_clock = parse_clock(start_str)
    end_clock = parse_clock(end_str)
    if start_clock is None or end_clock is None:
        return None
    if start_clock == end_clock:
        return None

    start_dt = localize_wall_time(day, *start_clock)
    end_dt = localize_wall_time(day, *end_clock)
    if end_dt <= start_dt:
        end_dt = localize_wall_time(day + datetime.timedelta(days=1), *end_clock)
    if end_dt <= start_dt:
        return None
    return start_dt, end_dt


# ==============================================================================
# iCal-Helfer
# ==============================================================================
def event_uid(
    summary: str,
    start_dt: datetime.datetime,
    end_dt: datetime.datetime,
    namespace: uuid.UUID = uuid.NAMESPACE_DNS,
) -> str:
    """Stabile UID aus Titel und Zeitraum (gleicher Termin -> gleiche UID)."""
    seed = f"{summary}-{start_dt.isoformat()}-{end_dt.isoformat()}"
    return str(uuid.uuid5(namespace, seed))


def create_event(
    summary: str,
    start_dt: datetime.datetime,
    end_dt: datetime.datetime,
    description: str,
    location: str,
    url: str = "",
    dtstamp: datetime.datetime | None = None,
) -> Event:
    """Baut einen iCal-Termin (``dtstamp`` ist sonst der aktuelle Zeitpunkt)."""
    event = Event()
    event.add("uid", event_uid(summary, start_dt, end_dt))
    event.add("summary", summary)
    event.add("dtstart", start_dt)
    event.add("dtend", end_dt)
    event.add("dtstamp", dtstamp or datetime.datetime.now(datetime.timezone.utc))
    if description:
        event.add("description", description)
    if location:
        event.add("location", location)
    if url:
        event.add("url", url)
    return event


def _event_sort_key(event: Event) -> tuple[str, str, str]:
    start = event.get("dtstart")
    return (
        start.dt.isoformat() if start is not None else "",
        str(event.get("summary", "")),
        str(event.get("uid", "")),
    )


def sort_events(events: list[Event]) -> list[Event]:
    """Sortiert Termine nach Startzeit, dann Titel (stabile Ausgabe)."""
    return sorted(events, key=_event_sort_key)


def dedupe_events(events: list[Event]) -> list[Event]:
    """Entfernt doppelte Termine (gleiche UID), Reihenfolge bleibt erhalten."""
    seen: set[str] = set()
    unique: list[Event] = []
    for event in events:
        uid = str(event.get("uid", ""))
        if uid in seen:
            continue
        seen.add(uid)
        unique.append(event)
    return unique


def build_calendar(
    events: list[Event],
    *,
    calname: str = "Hallenbäder Stuttgart, Böblingen & Sindelfingen",
) -> Calendar:
    """Erzeugt den Kalender samt Metadaten und den übergebenen Terminen."""
    calendar = Calendar()
    calendar.add("prodid", "-//Hallenbaeder Stuttgart, Boeblingen & Sindelfingen//DE")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("x-wr-calname", calname)
    calendar.add("x-wr-timezone", "Europe/Berlin")
    for event in sort_events(dedupe_events(events)):
        calendar.add_component(event)
    return calendar


def write_calendar(calendar: Calendar, output_path: Path, *, force: bool = False) -> bool:
    """Schreibt den Kalender nur, wenn sich sein Inhalt ändert. True = geschrieben."""
    data = calendar.to_ical()
    if not force and output_path.exists() and output_path.read_bytes() == data:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)
    return True


# ==============================================================================
# 1. STUTTGARTER BÄDER (JSON API)
# ==============================================================================
def extract_slots_for_day(rule: dict, weekday_prefix: str) -> list[tuple[str, str]]:
    """Extrahiert Zeitfenster aus ``mo``, ``mo1``, ``mo2``, ``mo3`` usw.

    Die Schlüssel werden numerisch sortiert und doppelte Fenster (etwa ``mo1``
    mit denselben Zeiten wie ``mo``) nur einmal übernommen. Die frühere Fassung
    kannte nur die Endungen "", "1" und "2"; ein weiteres Fenster verschwand
    stillschweigend aus dem Kalender.
    """
    candidates: list[tuple[int, str, str]] = []
    for key, value in rule.items():
        if not isinstance(key, str):
            continue
        match = _SLOT_KEY_RE.match(key)
        if not match or match.group(1) != weekday_prefix:
            continue
        if not isinstance(value, dict):
            continue

        start, end = value.get("from"), value.get("to")
        if not start or not end:
            continue
        if parse_clock(start) is None or parse_clock(end) is None:
            continue

        window = (str(start), str(end))
        if any(window == (found[1], found[2]) for found in candidates):
            continue
        candidates.append((int(match.group(2) or 0), window[0], window[1]))

    candidates.sort(key=lambda item: item[0])
    return [(start, end) for _, start, end in candidates]


def _rule_valid_on(rule: dict, target_date: datetime.date) -> bool:
    """True, wenn die Gültigkeit der Regel ``target_date`` umfasst.

    Eine fehlende oder unvollständige ``validity`` gilt als "gilt immer";
    nicht lesbare Datumsangaben werden ignoriert statt den Lauf zu beenden.
    """
    validity = rule.get("validity")
    if not isinstance(validity, dict):
        return True

    from_date = parse_date(validity.get("from"))
    to_date = parse_date(validity.get("to"))
    if from_date is not None and target_date < from_date:
        return False
    if to_date is not None and target_date > to_date:
        return False
    return True


def _as_dict_list(value: object) -> list[dict]:
    """Feld als Liste von Dicts – ``None`` statt Absturz bei kaputten Daten.

    ``bad.get("lookups", {}).get(...)`` stürzte ab, sobald das Feld
    ausdrücklich ``null`` enthielt (``'NoneType' object has no attribute
    'get'``) und riss den gesamten Lauf mit.
    """
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def get_stuttgart_slots_for_date(
    bh_dict: dict, usually_key: str, holiday_key: str, target_date: datetime.date
) -> list[tuple[str, str]]:
    """Öffnungszeiten eines Stuttgarter Bades an ``target_date``.

    Sonderregelungen (``holiday_*``) haben Vorrang vor den regulären Zeiten
    (``usually_*``); greift eine Sonderregelung, gilt für diesen Tag keine
    Ersatzzeit.
    """
    weekday_prefix = WEEKDAY_KEYS[target_date.weekday()]

    # 1. Prüfe zuerst Sonder- und Feiertagsregelungen (holiday_*)
    for rule in _as_dict_list(bh_dict.get(holiday_key)):
        if not _rule_valid_on(rule, target_date):
            continue
        if rule.get("closed") is True:
            return []
        slots = extract_slots_for_day(rule, weekday_prefix)
        if slots:
            return slots
        # Sonderregelung ohne Zeiten für diesen Wochentag = geschlossen
        return []

    # 2. Prüfe reguläre Öffnungszeiten (usually_*)
    for rule in _as_dict_list(bh_dict.get(usually_key)):
        if not _rule_valid_on(rule, target_date):
            continue
        if rule.get("closed") is True:
            continue
        slots = extract_slots_for_day(rule, weekday_prefix)
        if slots:
            return slots

    return []


def _stuttgart_location(bad: dict) -> tuple[str, str]:
    """Ort und Webseite eines Stuttgarter Bades aus dem API-Eintrag."""
    name = bad.get("name") or "Stuttgarter Bad"
    building = bad.get("building") or {}
    street = building.get("street", "")
    zip_code = building.get("zip_code", "")
    city = building.get("city", "Stuttgart")
    location = f"{name}, {street}, {zip_code} {city}".strip(", ")

    bad_url = "https://www.stuttgarterbaeder.de/"
    for communication in _as_dict_list(bad.get("communications")):
        if communication.get("type") == "i" and communication.get("address"):
            bad_url = communication["address"]
            break
    return location, bad_url


def _stuttgart_description(bad: dict, bad_url: str) -> str:
    """Beschreibung eines Stuttgarter Bades (Ausstattung + Webseite)."""
    lookups = bad.get("lookups")
    characteristics: list[str] = [
        str(entry["value"])
        for entry in _as_dict_list(
            lookups.get("characteristic") if isinstance(lookups, dict) else None
        )
        if entry.get("value")
    ]
    desc_parts = []
    if characteristics:
        desc_parts.append(f"Ausstattung: {', '.join(characteristics)}")
    desc_parts.append(f"Webseite: {bad_url}")
    return "\n".join(desc_parts)


def fetch_stuttgart_events(
    client: httpx.Client,
    start_date: datetime.date,
    days: int,
    *,
    dtstamp: datetime.datetime | None = None,
    log=print,
) -> list[Event]:
    """Termine aller Stuttgarter Bäder für den Zeitraum."""
    events: list[Event] = []
    timestamp = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
    url = f"{STUTTGART_URL}?_={timestamp}"
    log(f"Lade Stuttgarter Bäder von {url} ...")

    response = client.get(url)
    response.raise_for_status()
    baeder = response.json()
    if not isinstance(baeder, list):
        raise ValueError("Unerwartete Antwort der Stuttgarter Bäder (Liste erwartet)")

    skipped_slots = 0
    for bad in baeder:
        if not isinstance(bad, dict):
            continue
        name = bad.get("name") or "Stuttgarter Bad"
        location, bad_url = _stuttgart_location(bad)
        description = _stuttgart_description(bad, bad_url)

        businesshours = bad.get("businesshours")
        if not isinstance(businesshours, dict):
            continue

        for offset in range(days):
            current_date = start_date + datetime.timedelta(days=offset)
            for usually_key, holiday_key, label in STUTTGART_CATEGORIES:
                if usually_key not in businesshours and holiday_key not in businesshours:
                    continue
                slots = get_stuttgart_slots_for_date(
                    businesshours, usually_key, holiday_key, current_date
                )
                for start_str, end_str in slots:
                    window = slot_datetimes(current_date, start_str, end_str)
                    if window is None:
                        skipped_slots += 1
                        log(
                            f"Warnung: {name} ({label}) am {current_date}: "
                            f"Zeitfenster {start_str}-{end_str} übersprungen."
                        )
                        continue
                    start_dt, end_dt = window
                    summary = f"{name} ({label})" if label != "Schwimmbad" else name
                    events.append(
                        create_event(
                            summary=summary,
                            start_dt=start_dt,
                            end_dt=end_dt,
                            description=description,
                            location=location,
                            url=bad_url,
                            dtstamp=dtstamp,
                        )
                    )

    message = f"-> {len(events)} Termine für Stuttgarter Bäder erstellt."
    if skipped_slots:
        message += f" ({skipped_slots} unbrauchbare Zeitfenster übersprungen)"
    log(message)
    return events


# ==============================================================================
# 2. HALLENBAD BÖBLINGEN
# ==============================================================================
def parse_closed_dates(page_text: str) -> set[datetime.date]:
    """Schließtage aus der Böblinger Schließtage-Liste.

    Gelesen wird nur der Abschnitt hinter dem Hinweis "... geschlossen:".
    Die frühere Fassung übernahm *jedes* Datum im Format ``TT.MM.JJJJ - Text``
    aus der ganzen Seite; ein zusätzlicher Terminabschnitt hätte das Bad damit
    stillschweigend zugesperrt.
    """
    closed: set[datetime.date] = set()
    marker = page_text.find(BOEBLINGEN_CLOSURE_MARKER)
    if marker == -1:
        return closed

    block_lines: list[str] = []
    for line in page_text[marker:].splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^\d{1,2}\.\d{1,2}\.\d{4}", stripped):
            block_lines.append(stripped)
        elif block_lines:
            break
    block = "\n".join(block_lines)

    for match in re.finditer(r"(\d{1,2})\.(\d{1,2})\.(\d{4})([^\n]*)", block):
        day = parse_date(f"{match.group(1)}.{match.group(2)}.{match.group(3)}")
        if day is None:
            continue
        closed.add(day)

        # Schreibweise "24.12.2026 - 26.12.2026": ganzer Zeitraum
        span = re.match(r"\s*[-–]\s*(\d{1,2})\.(\d{1,2})\.(\d{4})", match.group(4))
        if span:
            end_day = parse_date(f"{span.group(1)}.{span.group(2)}.{span.group(3)}")
            if end_day is not None:
                cursor = day
                while cursor < end_day:
                    cursor += datetime.timedelta(days=1)
                    closed.add(cursor)

    return closed


def fallback_closed_dates(start_date: datetime.date, days: int) -> set[datetime.date]:
    """Ersatz-Schließtage, falls die Böblinger Seite nicht lesbar ist.

    Statt der früheren fest verdrahteten Liste (nur 2026/2027) werden die
    baden-württembergischen Feiertage des Zeitraums berechnet und um die
    jährlichen Betriebsferien (24.12. und 31.12.) ergänzt.
    """
    closed = set(public_holidays_in_range(start_date, days))
    for offset in range(days):
        current = start_date + datetime.timedelta(days=offset)
        if (current.month, current.day) in BOEBLINGEN_RECURRING_CLOSURES:
            closed.add(current)
    return closed


def _format_clock(value: str) -> str:
    """``"9"`` -> ``"09:00"``, ``"9:30"`` -> ``"09:30"``.

    Die frühere Fassung las nur volle Stunden und setzte eine Seite mit
    "9:30" stillschweigend auf die hinterlegten Standardzeiten zurück.
    """
    clock = parse_clock(value if ":" in value else f"{value}:00")
    if clock is None:
        raise ScheduleError(f"Ungültige Uhrzeit: {value!r}")
    return f"{clock[0]:02d}:{clock[1]:02d}"


def parse_weekday_hours(
    page_text: str, patterns: dict[str, str]
) -> dict[str, tuple[str, str]]:
    """Öffnungszeiten aus Fließtext lesen: ``{Schlüssel: (von, bis)}``.

    ``patterns`` bildet einen Schlüssel auf ein Suchmuster ab; das Muster muss
    zwei Gruppen mit Stunde (optional Minuten) liefern.
    """
    found: dict[str, tuple[str, str]] = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, page_text)
        if not match:
            continue
        try:
            found[key] = (_format_clock(match.group(1)), _format_clock(match.group(2)))
        except ScheduleError:
            continue
    return found


def fetch_boeblingen_events(
    client: httpx.Client,
    start_date: datetime.date,
    days: int,
    *,
    dtstamp: datetime.datetime | None = None,
    log=print,
) -> list[Event]:
    """Termine des Hallenbad Böblingen für den Zeitraum."""
    location = "Hallenbad Böblingen, Schönaicher Straße 75, 71032 Böblingen"
    log(f"Lade Hallenbad Böblingen von {BOEBLINGEN_URL} ...")

    schedule = dict(BOEBLINGEN_DEFAULT_SCHEDULE)
    closed_dates: set[datetime.date] = set()
    scrape_failed = False

    try:
        response = client.get(BOEBLINGEN_URL)
        response.raise_for_status()
        text = BeautifulSoup(response.text, "html.parser").get_text("\n")

        closed_dates = parse_closed_dates(text)
        scraped = parse_weekday_hours(
            text,
            {
                "mo": r"Mo\.\s*(\d{1,2}(?::\d{2})?)\s*[–-]\s*(\d{1,2}(?::\d{2})?)\s*Uhr",
                "difr": r"Di\.[–-]Fr\.\s*(\d{1,2}(?::\d{2})?)\s*[–-]\s*(\d{1,2}(?::\d{2})?)\s*Uhr",
                "saso": r"Sa\.[–-]So\.\s*(\d{1,2}(?::\d{2})?)\s*[–-]\s*(\d{1,2}(?::\d{2})?)\s*Uhr",
            },
        )
        if "mo" in scraped:
            schedule[0] = scraped["mo"]
        if "difr" in scraped:
            for weekday in (1, 2, 3, 4):
                schedule[weekday] = scraped["difr"]
        if "saso" in scraped:
            for weekday in (5, 6):
                schedule[weekday] = scraped["saso"]

        if closed_dates:
            log(f"-> {len(closed_dates)} Schließtage von der Böblinger Seite gelesen.")
    except Exception as exc:  # noqa: BLE001 – jede Quelle darf einzeln ausfallen
        scrape_failed = True
        log(
            "Warnung: Konnte Webseite Böblingen nicht live parsen, "
            f"nutze hinterlegte Daten ({exc})."
        )

    # Die Liste der Seite ist eine Momentaufnahme. Deshalb kommen die
    # berechneten baden-württembergischen Feiertage und die jährlichen
    # Betriebsferien (24.12./31.12.) hinzu – früher war dafür eine fest
    # verdrahtete Liste im Skript hinterlegt, die nur 2026/2027 abdeckte.
    computed = fallback_closed_dates(start_date, days)
    if scrape_failed:
        log(
            f"-> Ersatz-Schließtage verwendet: {len(computed)} Tage "
            "(Feiertage Baden-Württemberg + Betriebsferien 24.12./31.12.)."
        )
    closed_dates |= computed

    events: list[Event] = []
    for offset in range(days):
        current_date = start_date + datetime.timedelta(days=offset)
        if current_date in closed_dates:
            continue
        window = slot_datetimes(current_date, *schedule[current_date.weekday()])
        if window is None:
            log(f"Warnung: Zeitfenster für {current_date} übersprungen.")
            continue
        start_dt, end_dt = window
        events.append(
            create_event(
                summary="Hallenbad Böblingen",
                start_dt=start_dt,
                end_dt=end_dt,
                description=BOEBLINGEN_DEFAULT_DESCRIPTION,
                location=location,
                url=BOEBLINGEN_URL,
                dtstamp=dtstamp,
            )
        )

    log(f"-> {len(events)} Termine für Hallenbad Böblingen erstellt.")
    return events


# ==============================================================================
# 3. BADEZENTRUM SINDELFINGEN
# ==============================================================================
def fetch_sindelfingen_events(
    client: httpx.Client,
    start_date: datetime.date,
    days: int,
    *,
    dtstamp: datetime.datetime | None = None,
    log=print,
) -> list[Event]:
    """Termine des Badezentrum Sindelfingen für den Zeitraum.

    An gesetzlichen Feiertagen gelten die Feiertags-Öffnungszeiten. Welche
    Tage das sind, wird berechnet (Baden-Württemberg, jedes Jahr) statt – wie
    früher – gegen die Schließtagsliste des Hallenbads Böblingen geprüft.
    Dadurch stimmen die Zeiten auch nach dem 06.05.2027 und an Tagen wie dem
    24.12., die keine gesetzlichen Feiertage sind.
    """
    location = "Badezentrum Sindelfingen, Hohenzollernstraße 23, 71067 Sindelfingen"
    log(f"Lade Badezentrum Sindelfingen von {SINDELFINGEN_URL} ...")

    schedule = dict(SINDELFINGEN_DEFAULT_SCHEDULE)
    holiday_hours = SINDELFINGEN_DEFAULT_HOLIDAY_HOURS

    try:
        response = client.get(SINDELFINGEN_URL)
        response.raise_for_status()
        text = BeautifulSoup(response.text, "html.parser").get_text("\n")

        patterns = {
            day_label: rf"{day_label}\s+(\d{{2}}:\d{{2}})\s*[–-]\s*(\d{{2}}:\d{{2}})\s*Uhr"
            for day_label in SINDELFINGEN_DAY_NAMES
        }
        patterns["feiertag"] = r"Feiertag\s+(\d{2}:\d{2})\s*[–-]\s*(\d{2}:\d{2})\s*Uhr"

        scraped = parse_weekday_hours(text, patterns)
        for day_label, weekday in SINDELFINGEN_DAY_NAMES.items():
            if day_label in scraped:
                schedule[weekday] = scraped[day_label]
        if "feiertag" in scraped:
            holiday_hours = scraped["feiertag"]
    except Exception as exc:  # noqa: BLE001 – jede Quelle darf einzeln ausfallen
        log(
            "Warnung: Konnte Webseite Sindelfingen nicht live parsen, "
            f"nutze hinterlegte Daten ({exc})."
        )

    events: list[Event] = []
    for offset in range(days):
        current_date = start_date + datetime.timedelta(days=offset)
        # An gesetzlichen Feiertagen gelten die Feiertags-Öffnungszeiten
        if is_public_holiday(current_date):
            start_str, end_str = holiday_hours
        else:
            start_str, end_str = schedule[current_date.weekday()]

        window = slot_datetimes(current_date, start_str, end_str)
        if window is None:
            log(f"Warnung: Zeitfenster für {current_date} übersprungen.")
            continue
        start_dt, end_dt = window
        events.append(
            create_event(
                summary="Badezentrum Sindelfingen (Hallenbad)",
                start_dt=start_dt,
                end_dt=end_dt,
                description=SINDELFINGEN_DEFAULT_DESCRIPTION,
                location=location,
                url=SINDELFINGEN_URL,
                dtstamp=dtstamp,
            )
        )

    log(f"-> {len(events)} Termine für Badezentrum Sindelfingen erstellt.")
    return events


# ==============================================================================
# ORCHESTRIERUNG
# ==============================================================================
@dataclass
class SourceResult:
    """Ergebnis einer Quelle: Termine oder Fehlermeldung."""

    name: str
    events: list[Event] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


FETCHERS = {
    "stuttgart": fetch_stuttgart_events,
    "boeblingen": fetch_boeblingen_events,
    "sindelfingen": fetch_sindelfingen_events,
}


def collect_events(
    client: httpx.Client,
    start_date: datetime.date,
    days: int,
    sources: tuple[str, ...] | list[str] = SOURCE_NAMES,
    *,
    dtstamp: datetime.datetime | None = None,
    log=print,
) -> list[SourceResult]:
    """Fragt alle Quellen ab; ein Fehler betrifft nur die jeweilige Quelle."""
    results: list[SourceResult] = []
    for name in sources:
        fetcher = FETCHERS[name]
        try:
            events = fetcher(client, start_date, days, dtstamp=dtstamp, log=log)
            results.append(SourceResult(name=name, events=events))
        except Exception as exc:  # noqa: BLE001 – Fehler werden gesammelt, nicht geworfen
            log(f"FEHLER: Quelle '{name}' fehlgeschlagen ({type(exc).__name__}: {exc}).")
            results.append(SourceResult(name=name, error=f"{type(exc).__name__}: {exc}"))
    return results


def resolve_dtstamp(value: str | None) -> datetime.datetime | str | None:
    """``--dtstamp`` auswerten: ``now``, ``start`` oder ISO-8601-Zeitpunkt."""
    if value is None or value.strip().lower() == "now":
        return None
    if value.strip().lower() == "start":
        return "start"
    parsed = datetime.datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def build_arg_parser() -> argparse.ArgumentParser:
    """Kommandozeilenschalter des Skripts."""
    parser = argparse.ArgumentParser(
        prog="generate_ical.py",
        description=(
            "Erzeugt eine iCal-Datei mit den Öffnungszeiten der Hallenbäder "
            "in Stuttgart, Böblingen und Sindelfingen."
        ),
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DAYS_AHEAD,
        metavar="N",
        help=f"Vorlauf in Tagen (Standard: {DAYS_AHEAD})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("hallenbaeder.ics"),
        metavar="DATEI",
        help="Zieldatei (Standard: hallenbaeder.ics)",
    )
    parser.add_argument(
        "--source",
        action="append",
        choices=[*SOURCE_NAMES, "all"],
        metavar="NAME",
        help=(
            "Nur diese Quelle abfragen, mehrfach möglich "
            f"({', '.join(SOURCE_NAMES)}, all)"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        metavar="SEK",
        help="HTTP-Timeout in Sekunden (Standard: 30)",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help="User-Agent für die HTTP-Abfragen",
    )
    parser.add_argument(
        "--dtstamp",
        default="now",
        metavar="WERT",
        help=(
            "'now' (Standard), 'start' (DTSTAMP = Terminbeginn, damit die Datei "
            "reproduzierbar ist) oder ein ISO-8601-Zeitpunkt"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Nichts schreiben; Rückgabewert 3, wenn sich die Datei ändern würde",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Datei auch schreiben, wenn sich ihr Inhalt nicht ändert",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Quellen abfragen, aber nichts schreiben"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Maschinenlesbare Zusammenfassung auf stdout ausgeben",
    )
    parser.add_argument("--quiet", action="store_true", help="Keine Fortschrittsmeldungen")
    parser.add_argument(
        "--version", action="version", version=f"generate_ical.py {__version__}"
    )
    return parser


def _log_to_stderr(*args, **kwargs) -> None:
    """Bei --json gehören Fortschrittsmeldungen auf stderr."""
    print(*args, file=sys.stderr, **kwargs)


def main(argv: list[str] | None = None) -> int:
    """Ablauf: Quellen abfragen, Kalender bauen, Datei schreiben."""
    args = build_arg_parser().parse_args(argv)
    if args.quiet:
        log = lambda *a, **k: None  # noqa: E731 – kurze lokale Hilfsfunktion
    else:
        log = _log_to_stderr if args.json else print

    if args.days <= 0:
        print("Fehler: --days muss größer als 0 sein.", file=sys.stderr)
        return 2

    sources = tuple(dict.fromkeys(args.source)) if args.source else SOURCE_NAMES
    if "all" in sources:
        sources = SOURCE_NAMES

    try:
        dtstamp = resolve_dtstamp(args.dtstamp)
    except ValueError as exc:
        print(
            f"Fehler: --dtstamp ist kein gültiger ISO-8601-Zeitpunkt ({exc}).",
            file=sys.stderr,
        )
        return 2

    start_date = datetime.datetime.now(TZ).date()
    headers = {"User-Agent": args.user_agent}
    fixed_dtstamp = dtstamp if isinstance(dtstamp, datetime.datetime) else None

    with httpx.Client(
        headers=headers, timeout=args.timeout, follow_redirects=True
    ) as client:
        results = collect_events(
            client, start_date, args.days, sources, dtstamp=fixed_dtstamp, log=log
        )

    events = [event for result in results for event in result.events]
    errors = {result.name: result.error for result in results if not result.ok}

    if dtstamp == "start":
        # Reproduzierbare Datei: DTSTAMP = Terminbeginn statt "jetzt".
        for event in events:
            event["dtstamp"] = event["dtstart"].dt

    summary = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "start_date": start_date.isoformat(),
        "days": args.days,
        "events": {result.name: len(result.events) for result in results},
        "total": len(events),
        "errors": errors,
        "output": str(args.output),
        "written": False,
        "changed": False,
    }

    if not events:
        # Eine leere Datei würde den veröffentlichten Kalender zerstören.
        print(
            "Fehler: keine Termine ermittelt – die vorhandene Datei bleibt unverändert.",
            file=sys.stderr,
        )
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1

    calendar = build_calendar(events)
    current = args.output.read_bytes() if args.output.exists() else b""
    would_change = calendar.to_ical() != current

    if args.check or args.dry_run:
        changed = would_change
    else:
        changed = write_calendar(calendar, args.output, force=args.force)

    summary["written"] = bool(changed) and not (args.check or args.dry_run)
    summary["changed"] = bool(changed)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        if args.check:
            log(
                f"{args.output} würde sich ändern ({summary['total']} Termine)."
                if changed
                else f"{args.output} ist aktuell ({summary['total']} Termine)."
            )
        elif args.dry_run:
            log(f"Probelauf: {summary['total']} Termine, nichts geschrieben.")
        elif changed:
            log(f"Fertig! {summary['total']} Termine in '{args.output}' gespeichert.")
        else:
            log(
                f"{args.output} unverändert ({summary['total']} Termine), nichts "
                "geschrieben (--force erzwingt das Schreiben)."
            )
        if errors:
            log(
                "Warnung: fehlgeschlagene Quellen: "
                + ", ".join(sorted(errors))
                + " – der Kalender ist unvollständig."
            )

    if args.check and changed:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
