import datetime
import re
import time
import uuid
import httpx
import pytz
from bs4 import BeautifulSoup
from icalendar import Calendar, Event

TZ = pytz.timezone("Europe/Berlin")
DAYS_AHEAD = 60  # Zeitraum der Kalendereinträge im Voraus (in Tagen)

# Wochentags-Mapping für die Stuttgarter Bäder JSON-API (Monday = 0 ... Sunday = 6)
WEEKDAY_KEYS = ["mo", "di", "mi", "do", "fr", "sa", "so"]


def parse_date(date_str: str) -> datetime.date:
    return datetime.datetime.strptime(date_str, "%Y-%m-%d").date()


def make_aware_dt(day: datetime.date, time_str: str) -> datetime.datetime:
    h, m = map(int, time_str.strip().split(":"))
    dt = datetime.datetime(day.year, day.month, day.day, h, m)
    return TZ.localize(dt)


def create_event(
    summary: str,
    start_dt: datetime.datetime,
    end_dt: datetime.datetime,
    description: str,
    location: str,
    url: str = "",
) -> Event:
    event = Event()
    uid_seed = f"{summary}-{start_dt.isoformat()}-{end_dt.isoformat()}"
    event.add("uid", str(uuid.uuid5(uuid.NAMESPACE_DNS, uid_seed)))
    event.add("summary", summary)
    event.add("dtstart", start_dt)
    event.add("dtend", end_dt)
    event.add("dtstamp", datetime.datetime.now(pytz.utc))
    if description:
        event.add("description", description)
    if location:
        event.add("location", location)
    if url:
        event.add("url", url)
    return event


# ==============================================================================
# 1. STUTTGARTER BÄDER (JSON API)
# ==============================================================================
def extract_slots_for_day(rule: dict, weekday_prefix: str) -> list[tuple[str, str]]:
    """Extrahiert Zeitfenster aus mo, mo1, mo2 usw."""
    slots = []
    for suffix in ["", "1", "2"]:
        key = f"{weekday_prefix}{suffix}"
        val = rule.get(key)
        if isinstance(val, dict) and val.get("from") and val.get("to"):
            slots.append((val["from"], val["to"]))
    return slots


def get_stuttgart_slots_for_date(
    bh_dict: dict, usually_key: str, holiday_key: str, target_date: datetime.date
) -> list[tuple[str, str]]:
    weekday_prefix = WEEKDAY_KEYS[target_date.weekday()]

    # 1. Prüfe zuerst Sonder- und Feiertagsregelungen (holiday_*)
    holiday_rules = bh_dict.get(holiday_key) or []
    for rule in holiday_rules:
        val = rule.get("validity") or {}
        if not val.get("from") or not val.get("to"):
            continue
        from_date = parse_date(val["from"])
        to_date = parse_date(val["to"])
        if from_date <= target_date <= to_date:
            if rule.get("closed") is True:
                return []
            slots = extract_slots_for_day(rule, weekday_prefix)
            if slots:
                return slots
            # Falls closed=False, aber für diesen Wochentag keine Zeiten hinterlegt sind:
            return []

    # 2. Prüfe reguläre Öffnungszeiten (usually_*)
    usually_rules = bh_dict.get(usually_key) or []
    for rule in usually_rules:
        val = rule.get("validity") or {}
        if val.get("from") and val.get("to"):
            from_date = parse_date(val["from"])
            to_date = parse_date(val["to"])
            if not (from_date <= target_date <= to_date):
                continue
        slots = extract_slots_for_day(rule, weekday_prefix)
        if slots:
            return slots

    return []


def fetch_stuttgart_events(client: httpx.Client, start_date: datetime.date, days: int) -> list[Event]:
    events = []
    ts = int(time.time() * 1000)
    url = f"https://www.stuttgarterbaeder.de/fileadmin/jsonData/baeder.json?_={ts}"
    print(f"Lade Stuttgarter Bäder von {url} ...")

    resp = client.get(url)
    resp.raise_for_status()
    baeder = resp.json()

    categories = [
        ("usually_bhpool", "holiday_bhpool", "Schwimmbad"),
        ("usually_bhsauna", "holiday_bhsauna", "Sauna"),
        ("usually_bhsaunawomen", "holiday_bhsaunawomen", "Damensauna"),
        ("usually_bhsaunamen", "holiday_bhsaunamen", "Herrensauna"),
    ]

    for bad in baeder:
        name = bad.get("name", "Stuttgarter Bad")
        building = bad.get("building") or {}
        street = building.get("street", "")
        zip_code = building.get("zip_code", "")
        city = building.get("city", "Stuttgart")
        location = f"{name}, {street}, {zip_code} {city}".strip(", ")

        bad_url = "https://www.stuttgarterbaeder.de/"
        for comm in bad.get("communications") or []:
            if comm.get("type") == "i" and comm.get("address"):
                bad_url = comm["address"]
                break

        characteristics = [
            c.get("value")
            for c in (bad.get("lookups", {}).get("characteristic") or [])
            if c.get("value")
        ]
        desc_parts = []
        if characteristics:
            desc_parts.append(f"Ausstattung: {', '.join(characteristics)}")
        desc_parts.append(f"Webseite: {bad_url}")
        description = "\n".join(desc_parts)

        bh = bad.get("businesshours") or {}
        if not isinstance(bh, dict):
            continue

        for offset in range(days):
            current_date = start_date + datetime.timedelta(days=offset)
            for usually_key, holiday_key, label in categories:
                if usually_key not in bh and holiday_key not in bh:
                    continue
                slots = get_stuttgart_slots_for_date(bh, usually_key, holiday_key, current_date)
                for start_str, end_str in slots:
                    start_dt = make_aware_dt(current_date, start_str)
                    end_dt = make_aware_dt(current_date, end_str)
                    summary = f"{name} ({label})" if label != "Schwimmbad" else name
                    events.append(
                        create_event(
                            summary=summary,
                            start_dt=start_dt,
                            end_dt=end_dt,
                            description=description,
                            location=location,
                            url=bad_url,
                        )
                    )

    print(f"-> {len(events)} Termine für Stuttgarter Bäder erstellt.")
    return events


# ==============================================================================
# 2. HALLENBAD BÖBLINGEN
# ==============================================================================
BOEBLINGEN_CLOSED_DATES = {
    datetime.date(2026, 10, 3),   # Tag der Deutschen Einheit
    datetime.date(2026, 11, 1),   # Allerheiligen
    datetime.date(2026, 12, 24),  # Heiligabend
    datetime.date(2026, 12, 25),  # 1. Weihnachtsfeiertag
    datetime.date(2026, 12, 26),  # 2. Weihnachtsfeiertag
    datetime.date(2026, 12, 31),  # Silvester
    datetime.date(2027, 1, 1),    # Neujahr
    datetime.date(2027, 1, 6),    # Heilige Drei Könige
    datetime.date(2027, 3, 26),   # Karfreitag
    datetime.date(2027, 3, 28),   # Ostersonntag
    datetime.date(2027, 3, 29),   # Ostermontag
    datetime.date(2027, 5, 1),    # Tag der Arbeit
    datetime.date(2027, 5, 6),    # Christi Himmelfahrt
}

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


def fetch_boeblingen_events(client: httpx.Client, start_date: datetime.date, days: int) -> list[Event]:
    url = "https://www.stadtwerke-boeblingen.de/freizeit-baeder/hallenbad"
    location = "Hallenbad Böblingen, Schönaicher Straße 75, 71032 Böblingen"
    print(f"Lade Hallenbad Böblingen von {url} ...")

    closed_dates = set(BOEBLINGEN_CLOSED_DATES)
    # Standard-Öffnungszeiten: Mo 14-21, Di-Fr 7-21, Sa-So 8-17
    schedule = {
        0: ("14:00", "21:00"),
        1: ("07:00", "21:00"),
        2: ("07:00", "21:00"),
        3: ("07:00", "21:00"),
        4: ("07:00", "21:00"),
        5: ("08:00", "17:00"),
        6: ("08:00", "17:00"),
    }

    try:
        resp = client.get(url)
        resp.raise_for_status()
        text = BeautifulSoup(resp.text, "html.parser").get_text("\n")

        # Dynamische Erkennung weiterer geschlossener Feiertage im Format DD.MM.YYYY
        for match in re.finditer(r"(\d{2})\.(\d{2})\.(\d{4})\s*-\s*([^\n]+)", text):
            d, m, y = map(int, match.groups()[:3])
            closed_dates.add(datetime.date(y, m, d))

        # Dynamische Erkennung der Uhrzeiten falls auf der Seite angepasst
        mo_match = re.search(r"Mo\.\s*(\d{1,2})[–-](\d{1,2})\s*Uhr", text)
        difr_match = re.search(r"Di\.[–-]Fr\.\s*(\d{1,2})[–-](\d{1,2})\s*Uhr", text)
        saso_match = re.search(r"Sa\.[–-]So\.\s*(\d{1,2})[–-](\d{1,2})\s*Uhr", text)

        if mo_match:
            schedule[0] = (f"{int(mo_match.group(1)):02d}:00", f"{int(mo_match.group(2)):02d}:00")
        if difr_match:
            s_t = f"{int(difr_match.group(1)):02d}:00"
            e_t = f"{int(difr_match.group(2)):02d}:00"
            for wd in (1, 2, 3, 4):
                schedule[wd] = (s_t, e_t)
        if saso_match:
            s_t = f"{int(saso_match.group(1)):02d}:00"
            e_t = f"{int(saso_match.group(2)):02d}:00"
            for wd in (5, 6):
                schedule[wd] = (s_t, e_t)
    except Exception as exc:
        print(f"Warnung: Konnte Webseite Böblingen nicht live parsen, nutze hinterlegte Daten ({exc}).")

    events = []
    for offset in range(days):
        current_date = start_date + datetime.timedelta(days=offset)
        if current_date in closed_dates:
            continue
        start_str, end_str = schedule[current_date.weekday()]
        start_dt = make_aware_dt(current_date, start_str)
        end_dt = make_aware_dt(current_date, end_str)
        events.append(
            create_event(
                summary="Hallenbad Böblingen",
                start_dt=start_dt,
                end_dt=end_dt,
                description=BOEBLINGEN_DEFAULT_DESCRIPTION,
                location=location,
                url=url,
            )
        )

    print(f"-> {len(events)} Termine für Hallenbad Böblingen erstellt.")
    return events


# ==============================================================================
# 3. BADEZENTRUM SINDELFINGEN
# ==============================================================================
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


def fetch_sindelfingen_events(client: httpx.Client, start_date: datetime.date, days: int) -> list[Event]:
    url = "https://badezentrum.de/"
    location = "Badezentrum Sindelfingen, Hohenzollernstraße 23, 71067 Sindelfingen"
    print(f"Lade Badezentrum Sindelfingen von {url} ...")

    # Standard-Öffnungszeiten: Mo-Fr 07:00-21:30, Sa/So/Feiertag 08:00-20:00
    schedule = {
        0: ("07:00", "21:30"),
        1: ("07:00", "21:30"),
        2: ("07:00", "21:30"),
        3: ("07:00", "21:30"),
        4: ("07:00", "21:30"),
        5: ("08:00", "20:00"),
        6: ("08:00", "20:00"),
    }
    holiday_hours = ("08:00", "20:00")
    extra_notice = ""

    day_names = {
        "Montag": 0,
        "Dienstag": 1,
        "Mittwoch": 2,
        "Donnerstag": 3,
        "Freitag": 4,
        "Samstag": 5,
        "Sonntag": 6,
    }

    try:
        resp = client.get(url)
        resp.raise_for_status()
        text = BeautifulSoup(resp.text, "html.parser").get_text("\n")

        for day_label, wd_idx in day_names.items():
            m = re.search(
                rf"{day_label}\s+(\d{{2}}:\d{{2}})\s*[–-]\s*(\d{{2}}:\d{{2}})\s*Uhr", text
            )
            if m:
                schedule[wd_idx] = (m.group(1), m.group(2))

        m_hol = re.search(r"Feiertag\s+(\d{2}:\d{2})\s*[–-]\s*(\d{2}:\d{2})\s*Uhr", text)
        if m_hol:
            holiday_hours = (m_hol.group(1), m_hol.group(2))

    except Exception as exc:
        print(f"Warnung: Konnte Webseite Sindelfingen nicht live parsen, nutze hinterlegte Daten ({exc}).")

    description = extra_notice + SINDELFINGEN_DEFAULT_DESCRIPTION

    events = []
    for offset in range(days):
        current_date = start_date + datetime.timedelta(days=offset)
        # An gesetzlichen Feiertagen gelten die Feiertags-Öffnungszeiten (08:00 - 20:00)
        if current_date in BOEBLINGEN_CLOSED_DATES:
            start_str, end_str = holiday_hours
        else:
            start_str, end_str = schedule[current_date.weekday()]

        start_dt = make_aware_dt(current_date, start_str)
        end_dt = make_aware_dt(current_date, end_str)
        events.append(
            create_event(
                summary="Badezentrum Sindelfingen (Hallenbad)",
                start_dt=start_dt,
                end_dt=end_dt,
                description=description,
                location=location,
                url=url,
            )
        )

    print(f"-> {len(events)} Termine für Badezentrum Sindelfingen erstellt.")
    return events


# ==============================================================================
# MAIN
# ==============================================================================
def main():
    cal = Calendar()
    cal.add("prodid", "-//Hallenbaeder Stuttgart, Boeblingen & Sindelfingen//DE")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", "Hallenbäder Stuttgart, Böblingen & Sindelfingen")
    cal.add("x-wr-timezone", "Europe/Berlin")

    today = datetime.datetime.now(TZ).date()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
    }

    all_events = []
    with httpx.Client(headers=headers, timeout=30.0, follow_redirects=True) as client:
        all_events.extend(fetch_stuttgart_events(client, today, DAYS_AHEAD))
        all_events.extend(fetch_boeblingen_events(client, today, DAYS_AHEAD))
        all_events.extend(fetch_sindelfingen_events(client, today, DAYS_AHEAD))

    for ev in all_events:
        cal.add_component(ev)

    output_file = "hallenbaeder.ics"
    with open(output_file, "wb") as f:
        f.write(cal.to_ical())

    print(f"Fertig! {len(all_events)} Termine in '{output_file}' gespeichert.")


if __name__ == "__main__":
    main()
