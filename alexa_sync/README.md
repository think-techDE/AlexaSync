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

Die YAML-Optionen bleiben als Start-/Fallbackwerte erhalten.

| Option | Beschreibung |
| --- | --- |
| `list_a` | Erste Home-Assistant-To-do-Entity, z.B. `todo.alexa_einkaufsliste`. |
| `list_b` | Zweite Home-Assistant-To-do-Entity, z.B. `todo.einkaufsliste_2`. |
| `interval_seconds` | Polling-Intervall in Sekunden. |
| `sync_completed` | Synchronisiert `completed`/`needs_action` zwischen beiden Listen. |
| `remove_completed` | Entfernt abgeschlossene Eintraege nach erfolgreichem Sync aus beiden Listen. |
| `log_level` | Log-Level des Add-ons. |

## Verhalten

- Neue offene Eintraege werden in beide Richtungen angelegt.
- Eintraege werden anhand des normalisierten Artikelnamens abgeglichen.
- Bei widerspruechlichen gleichzeitigen Statusaenderungen gewinnt `completed`.
- Direkte Loeschungen werden nicht synchronisiert. Das verhindert Datenverlust,
  wenn eine Liste temporaer nicht verfuegbar ist.
