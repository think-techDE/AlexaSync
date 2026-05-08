# Alexa Sync

Synchronisiert zwei Home-Assistant-`todo`-Listen bidirektional.

Typischer Einsatz:

- Alexa schreibt per Sprachbefehl auf die Alexa-Einkaufsliste.
- Das Add-on kopiert neue Eintraege in die Bring-Einkaufsliste.
- Abgehakte Eintraege in Bring werden zurueck auf die Alexa-Liste synchronisiert.

## Konfiguration

Die bevorzugte Konfiguration erfolgt ueber die **Weboberflaeche** des Add-ons
oder ueber den Sidebar-Eintrag **Alexa Sync**. Dort werden vorhandene
`todo.*`-Entities als Dropdown angezeigt.

Es gibt zwei Modi:

- **Home Assistant Liste ↔ Home Assistant Liste**: synchronisiert zwei
  vorhandene `todo.*`-Entities.
- **Alexa Shopping List Server ↔ Home Assistant Liste**: nutzt den
  Selenium/WebSocket-Server aus
  `madmachinations/home-assistant-alexa-shopping-list`, um die Alexa-
  Einkaufsliste mit einer HA-`todo`-Entity zu synchronisieren.

Die YAML-Optionen bleiben als Start-/Fallbackwerte erhalten.

| Option | Beschreibung |
| --- | --- |
| `list_a` | Erste Home-Assistant-To-do-Entity, z.B. `todo.alexa_einkaufsliste`. |
| `list_b` | Zweite Home-Assistant-To-do-Entity, z.B. `todo.einkaufsliste_2`. |
| `mode` | `ha_todo_pair` oder `alexa_server`. |
| `alexa_server_host` | Host/IP des Alexa-Shopping-List-Servers. |
| `alexa_server_port` | Port des Alexa-Shopping-List-Servers, Standard `4000`. |
| `ha_list` | HA-To-do-Liste fuer den Alexa-Server-Modus. |
| `interval_seconds` | Polling-Intervall in Sekunden. |
| `sync_completed` | Synchronisiert `completed`/`needs_action` zwischen beiden Listen. |
| `remove_completed` | Entfernt abgeschlossene Eintraege nach erfolgreichem Sync aus beiden Listen. |
| `log_level` | Log-Level des Add-ons. |

## Verhalten

- Neue offene Eintraege werden in beide Richtungen angelegt.
- Eintraege werden anhand des normalisierten Artikelnamens abgeglichen.
- Bei widerspruechlichen gleichzeitigen Statusaenderungen gewinnt `completed`.
- Im Alexa-Server-Modus bedeutet ein abgehakter HA/Bring-Eintrag: Der Eintrag
  wird aus der aktiven Alexa-Liste entfernt.
- Direkte Loeschungen werden nicht synchronisiert. Das verhindert Datenverlust,
  wenn eine Liste temporaer nicht verfuegbar ist.
