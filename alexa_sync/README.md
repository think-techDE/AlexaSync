# Alexa Sync Add-on

Synchronisiert eine oder mehrere Alexa-Einkaufslisten mit einer oder mehreren
Home-Assistant-`todo`-Listen, typischerweise Bring- oder Einkaufslisten.

## Kurzfassung

1. Alexa Media Player in Home Assistant einrichten.
2. Alexa Sync starten.
3. Eine oder mehrere Ziel-Listen auswaehlen.
4. **Sessions uebernehmen** klicken.
5. Optional manuell **Jetzt synchronisieren** ausfuehren.

## Bedienung

### Ziel

Waehle eine oder mehrere Home-Assistant-`todo`-Listen, die als Einkaufslisten
genutzt werden sollen. Alle importierten Alexa-Konten werden gegen alle
ausgewaehlten Listen synchronisiert.

### Alexa Media Player

Das Add-on sucht unter `/homeassistant/.storage` nach Alexa-Media-Player-
Sessiondateien. Importierbare Sessions sind vorausgewaehlt.

Beim Klick auf **Sessions uebernehmen** passiert Folgendes:

- Die ausgewaehlten Sessiondateien werden gelesen.
- Daraus werden Amazon-Session-Cookies extrahiert.
- Pro Session wird ein Amazon-Konto angelegt.
- Die aktuell importierte Kontenliste wird durch diese Auswahl ersetzt.
- Die Ziel-Listen und Domain werden mitgespeichert.

Einzelne importierte Konten koennen danach in der Weboberflaeche entfernt
werden.

### Status

Der Statusbereich zeigt:

- ob die Konfiguration vollstaendig ist,
- wann zuletzt synchronisiert wurde,
- wie viele Schreibvorgaenge der letzte Sync ausgefuehrt hat,
- und den letzten Fehler, falls einer aufgetreten ist.

## Synchronisationslogik

- Neue Alexa-Eintraege werden in alle Ziel-Listen geschrieben.
- Neue Ziel-Listen-Eintraege werden in alle aktiven Alexa-Listen geschrieben.
- Erledigte Ziel-Listen-Eintraege werden aus den Alexa-Listen entfernt.
- Sync-Metadaten werden pro Amazon-Konto und Ziel-Liste gespeichert.

## Optionen

| Option | Beschreibung |
| --- | --- |
| `amazon_domain` | Amazon-Domain fuer neue Imports, z.B. `amazon.de`. |
| `amazon_accounts` | Aus Alexa Media Player importierte Amazon-Konten. |
| `ha_list` | Legacy-Ziel-Liste als Home-Assistant-`todo`-Entity. |
| `ha_lists` | Ziel-Listen als Home-Assistant-`todo`-Entities. |
| `interval_seconds` | Sync-Intervall in Sekunden. |
| `sync_completed` | Erledigte Eintraege zwischen Ziel-Listen und Alexa abgleichen. |
| `remove_completed` | Erledigte Eintraege nach erfolgreichem Sync aus den Ziel-Listen entfernen. |
| `log_level` | Log-Level des Add-ons. |

## Sicherheit

- Amazon-Benutzername und Passwort werden nicht im Add-on gespeichert.
- Die Home-Assistant-Konfiguration wird fuer den Session-Import read-only
  eingebunden.
- Importierte Amazon-Cookies werden unter `/data` gespeichert.

## Grenzen

Amazon bietet keine offizielle stabile API fuer Alexa-Einkaufslisten. Das Add-on
nutzt daher Chromium/Selenium und die importierten Session-Cookies. Wenn Amazon
eine Session beendet, muss sie in Alexa Media Player erneuert und danach im
Add-on erneut uebernommen werden.
