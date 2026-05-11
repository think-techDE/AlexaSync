# Alexa Sync

Synchronisiert die Alexa-Einkaufsliste mit einer Home-Assistant-`todo`-Liste
oder zwei vorhandene Home-Assistant-`todo`-Listen miteinander.

Typischer Einsatz:

- Mehrere Amazon-Konten schreiben per Sprachbefehl auf ihre Alexa-Einkaufsliste.
- Das Add-on kopiert neue Eintraege in die Bring-Einkaufsliste.
- Abgehakte Eintraege in Bring werden aus allen aktiven Alexa-Listen entfernt.

## Konfiguration

Die bevorzugte Konfiguration erfolgt ueber die **Weboberflaeche** des Add-ons
oder ueber den Sidebar-Eintrag **Alexa Sync**. Fuer den Standardfall muss nur
die Bring-/Ziel-Liste gewaehlt und eine Amazon-Session gespeichert werden. Wenn
Alexa Media Player bereits in Home Assistant eingerichtet ist, kann dessen
Session per Knopfdruck uebernommen werden. Sind mehrere Alexa-Media-Player-
Sessions vorhanden, legt das Add-on nur fuer markierte Sessions Amazon-Konten
an.

Modi:

- **Alexa direkt - Home Assistant Liste**: nutzt Chromium/Selenium direkt im
  Add-on. Es ist kein separates Alexa-Server-Add-on erforderlich.
- **Home Assistant Liste - Home Assistant Liste**: synchronisiert zwei
  vorhandene `todo.*`-Entities.
- **Externer Alexa Shopping List Server - Home Assistant Liste**:
  Kompatibilitaetsmodus fuer den Selenium/WebSocket-Server aus
  `madmachinations/home-assistant-alexa-shopping-list`.

Die YAML-Optionen bleiben als Start-/Fallbackwerte erhalten.

| Option | Beschreibung |
| --- | --- |
| `mode` | `internal_alexa`, `ha_todo_pair` oder `alexa_server`. |
| `amazon_domain` | Amazon-Domain fuer den direkten Alexa-Modus, z.B. `amazon.de`. |
| `amazon_accounts` | Amazon-Konten fuer den direkten Alexa-Modus. Wird bevorzugt ueber die Weboberflaeche gepflegt. |
| `ha_list` | HA-To-do-Liste fuer den direkten Alexa- oder Server-Modus. |
| `list_a` | Erste Home-Assistant-To-do-Entity fuer den HA-Listenmodus. |
| `list_b` | Zweite Home-Assistant-To-do-Entity fuer den HA-Listenmodus. |
| `alexa_server_host` | Host/IP eines externen Alexa-Shopping-List-Servers. |
| `alexa_server_port` | Port eines externen Alexa-Shopping-List-Servers, Standard `4000`. |
| `interval_seconds` | Polling-Intervall in Sekunden. |
| `sync_completed` | Synchronisiert erledigte Eintraege. |
| `remove_completed` | Entfernt abgeschlossene Eintraege nach erfolgreichem Sync aus der HA-Liste. |
| `log_level` | Log-Level des Add-ons. |

## Amazon-Session

Amazon stellt keine offizielle stabile API fuer Alexa-Einkaufslisten bereit. Das
Add-on speichert deshalb keine Amazon-Zugangsdaten. Der einfachste Weg ist
gewuenschte Alexa-Media-Player-Sessions zu markieren und **Ausgewaehlte aus
Alexa Media Player uebernehmen** zu klicken. Dafuer liest das Add-on nur die
ausgewaehlten Alexa-Media-Player-Cookie-Dateien aus der Home-Assistant-
Konfiguration und legt je Datei ein Amazon-Konto an. Der Zugriff auf die
Home-Assistant-Konfiguration ist read-only. Alte Cookie-Dateien werden
ausgeblendet, wenn sie keinem aktiven Alexa-Media-Player-Config-Entry mehr
zugeordnet werden koennen; doppelte Dateien derselben Mailadresse erscheinen nur
einmal.

Falls keine Alexa-Media-Player-Session gefunden wird, kann die Weboberflaeche
pro Konto einen internen Amazon-Browser oeffnen. Nach der Anmeldung werden nur
die Browser-Session-Cookies mit restriktiven Dateirechten gespeichert. Wenn
Amazon die Session beendet, muss die Anmeldung fuer das betroffene Konto erneut
durchlaufen oder Cookies importiert werden.
