# Hallenbäder Stuttgart, Böblingen & Sindelfingen als iCal-Kalender

Erzeugt eine iCal-Datei (`hallenbaeder.ics`) mit den Öffnungszeiten der
Hallenbäder in Stuttgart, Böblingen und Sindelfingen und hält sie automatisch
aktuell. Der Kalender lässt sich in Google Kalender, Apple Kalender,
Thunderbird & Co. abonnieren.

## Quellen

| Quelle | Ausgewertet wird |
|---|---|
| Stuttgarter Bäder | JSON-API `stuttgarterbaeder.de` (Öffnungszeiten je Bad, Sauna, Damen-/Herrensauna, Kinderbereich, Feiertagsregelungen) |
| Hallenbad Böblingen | Öffnungszeiten und Schließtagsliste der Seite `stadtwerke-boeblingen.de` |
| Badezentrum Sindelfingen | Öffnungszeiten-Tabelle inklusive Feiertagszeiten von `badezentrum.de` |

Alle Zeiten sind Wanduhrzeiten in `Europe/Berlin`; die Datei enthält
`TZID=Europe/Berlin`, damit Kalenderprogramme die Sommerzeit korrekt
berücksichtigen.

## Benutzung

```bash
pip install -r requirements.txt
python generate_ical.py
```

Ohne weitere Angaben werden die nächsten 60 Tage ab heute nach
`hallenbaeder.ics` im aktuellen Verzeichnis geschrieben.

```bash
python generate_ical.py --days 120 --output ~/kalender/hallenbaeder.ics
```

| Schalter | Bedeutung |
|---|---|
| `--days N` | Vorlauf in Tagen (Standard: 60) |
| `--output DATEI` | Zieldatei (Standard: `hallenbaeder.ics`) |
| `--source NAME` | nur `stuttgart`, `boeblingen` oder `sindelfingen` abfragen (`all` = alle); mehrfach möglich |
| `--timeout SEK` | HTTP-Timeout (Standard: 30) |
| `--user-agent TEXT` | eigener User-Agent für die Abfragen |
| `--dtstamp WERT` | `now` (Standard), `start` oder ein ISO-8601-Zeitpunkt |
| `--check` | nichts schreiben; Rückgabewert 3, wenn sich die Datei ändern würde |
| `--dry-run` | Quellen abfragen, aber nichts schreiben |
| `--force` | Datei auch bei unverändertem Inhalt schreiben |
| `--json` | maschinenlesbare Zusammenfassung auf stdout (Fortschritt auf stderr) |
| `--quiet` | keine Fortschrittsmeldungen |
| `--version`, `--help` | Version bzw. Hilfe |

Rückgabewerte: `0` Erfolg, `1` keine Termine ermittelt (die vorhandene Datei
bleibt unverändert), `2` Aufruffehler, `3` `--check` hat eine Änderung erkannt.

Die Datei wird nur geschrieben, wenn sich ihr Inhalt ändert. Mit
`--dtstamp start` ist die Ausgabe reproduzierbar (DTSTAMP = Terminbeginn statt
"Ausführungszeitpunkt"), sodass ein Cron-Lauf ohne echte Änderung keinen neuen
Commit erzeugt.

## Wie die Zeiten ermittelt werden

* **Stuttgarter Bäder:** Die Regeln `usually_*` gelten regulär, die Regeln
  `holiday_*` haben Vorrang (Feiertags- oder Sonderöffnungszeiten, teils als
  `closed` markiert). Pro Wochentag sind mehrere Zeitfenster möglich
  (`mo`, `mo1`, `mo2`, …); Fenster, die vor ihrem Start enden, laufen über
  Mitternacht.
* **Hallenbad Böblingen:** Öffnungszeiten werden aus der Seite gelesen, die
  Schließtage aus dem Abschnitt „An folgenden Tagen sind das Hallenbad und die
  Sauna geschlossen“. Zusätzlich gelten die gesetzlichen Feiertage in
  Baden-Württemberg (berechnet, nicht fest verdrahtet) und die jährlichen
  Betriebsferien am 24.12. und 31.12. Ist die Seite nicht erreichbar, werden
  die hinterlegten Standardzeiten verwendet.
* **Badezentrum Sindelfingen:** Wochentagszeiten und Feiertagszeiten kommen aus
  der Tabelle der Seite; an gesetzlichen Feiertagen gelten die
  Feiertagszeiten.

Fällt eine Quelle aus, werden die übrigen weiterhin geschrieben und die
fehlgeschlagene Quelle wird gemeldet.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Die Tests laufen vollständig offline: die Antworten der drei Seiten liegen als
Fixtures unter `tests/fixtures/` und werden über `httpx.MockTransport`
eingespeist. Damit lassen sich auch die Feiertags- und
Sommerzeit-Sonderfälle prüfen, ohne die echten Seiten abzufragen.

## Lizenz

MIT – siehe [LICENSE](LICENSE).
