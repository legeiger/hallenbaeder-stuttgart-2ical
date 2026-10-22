"""Gesetzliche Feiertage in Baden-Württemberg – berechnet, nicht fest verdrahtet.

Das Modul ersetzt die früher im Skript hinterlegte Liste fester Schließtage
(nur 2026/2027). Weil die Feiertage hier für jedes Jahr berechnet werden,
bleiben die Kalendereinträge auch nach 2027 korrekt, ohne dass jemand die
Liste von Hand nachziehen muss.

Beispiel:
    >>> import datetime
    >>> from holidays_bw import is_public_holiday, public_holidays
    >>> is_public_holiday(datetime.date(2027, 5, 6))   # Christi Himmelfahrt
    True
    >>> sorted(public_holidays(2027))[0]
    datetime.date(2027, 1, 1)
"""

from __future__ import annotations

import datetime

__all__ = [
    "easter_sunday",
    "public_holidays",
    "public_holidays_in_range",
    "is_public_holiday",
]

# Gesetzliche Feiertage in Baden-Württemberg: (Name, fester Tag im Jahr) –
# die beweglichen Termine hängen am Osterdatum und werden unten ergänzt.
_FIXED_HOLIDAYS: tuple[tuple[str, int, int], ...] = (
    ("Neujahr", 1, 1),
    ("Heilige Drei Könige", 1, 6),
    ("Tag der Arbeit", 5, 1),
    ("Tag der Deutschen Einheit", 10, 3),
    ("Allerheiligen", 11, 1),
    ("1. Weihnachtsfeiertag", 12, 25),
    ("2. Weihnachtsfeiertag", 12, 26),
)


def easter_sunday(year: int) -> datetime.date:
    """Ostersonntag eines Jahres (anonyme gregorianische Osterformel).

    Gültig für alle Jahre des gregorianischen Kalenders; die Formel ist die
    verbreitete Meeus/Jones/Butcher-Variante.
    """
    if year < 1583:
        raise ValueError("Osterformel gilt erst ab dem Jahr 1583")

    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - 7 * m + 114, 31)
    return datetime.date(year, month, day + 1)


def public_holidays(year: int) -> dict[datetime.date, str]:
    """Alle gesetzlichen Feiertage in Baden-Württemberg für ``year``.

    Rückgabe ist ein Dict ``Datum -> Name``, sortiert nach Datum. Ostersonntag
    und Pfingstsonntag sind enthalten, weil beide auch auf den Seiten der
    Bäder ausdrücklich als Feiertage geführt werden.
    """
    easter = easter_sunday(year)
    holidays: dict[datetime.date, str] = {
        datetime.date(year, month, day): name for name, month, day in _FIXED_HOLIDAYS
    }
    holidays.update(
        {
            easter - datetime.timedelta(days=2): "Karfreitag",
            easter: "Ostersonntag",
            easter + datetime.timedelta(days=1): "Ostermontag",
            easter + datetime.timedelta(days=39): "Christi Himmelfahrt",
            easter + datetime.timedelta(days=49): "Pfingstsonntag",
            easter + datetime.timedelta(days=50): "Pfingstmontag",
            easter + datetime.timedelta(days=60): "Fronleichnam",
        }
    )
    return dict(sorted(holidays.items()))


def public_holidays_in_range(
    start_date: datetime.date, days: int
) -> dict[datetime.date, str]:
    """Feiertage im Fenster ``[start_date, start_date + days)``.

    Das Fenster darf über einen Jahreswechsel laufen; es werden so viele
    Jahrgänge berechnet, wie das Fenster berührt.
    """
    if days <= 0:
        return {}

    end_date = start_date + datetime.timedelta(days=days - 1)
    holidays: dict[datetime.date, str] = {}
    for year in range(start_date.year, end_date.year + 1):
        for day, name in public_holidays(year).items():
            if start_date <= day <= end_date:
                holidays[day] = name
    return dict(sorted(holidays.items()))


def is_public_holiday(day: datetime.date) -> bool:
    """True, wenn ``day`` ein gesetzlicher Feiertag in Baden-Württemberg ist."""
    return day in public_holidays(day.year)
