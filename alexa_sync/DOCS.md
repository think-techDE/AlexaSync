# Alexa Sync Dokumentation

## Installation

1. In Home Assistant zu **Einstellungen > Add-ons > Add-on Store** gehen.
2. Im Drei-Punkte-Menue **Repositories** waehlen.
3. Dieses Repository hinzufuegen:

   `https://github.com/think-techDE/AlexaSync`

4. **Alexa Sync** installieren.
5. Add-on starten.
6. Den Reiter **Weboberflaeche** oeffnen.
7. Zwei To-do-Listen auswaehlen und speichern.

## Beispiel

Die YAML-Konfiguration ist optional. Sie dient nur als Start-/Fallbackwert, wenn
noch keine Konfiguration ueber die Weboberflaeche gespeichert wurde.

```yaml
mode: ha_todo_pair
list_a: todo.alexa_einkaufsliste
list_b: todo.einkaufsliste_2
interval_seconds: 60
sync_completed: true
remove_completed: false
log_level: info
```

## Alexa-Server-Modus

Amazon stellt keine einfache To-do-Entity fuer Alexa-Einkaufslisten bereit.
Der Workaround aus
`madmachinations/home-assistant-alexa-shopping-list` nutzt Selenium/Chromium und
stellt die Alexa-Liste ueber einen WebSocket-Server bereit. Dieses Add-on kann
mit diesem Server sprechen.

Beispiel:

```yaml
mode: alexa_server
alexa_server_host: 192.168.1.10
alexa_server_port: 4000
ha_list: todo.einkaufsliste_2
interval_seconds: 60
sync_completed: true
remove_completed: false
log_level: info
```

## Voraussetzungen

Alexa und Bring muessen bereits als Home-Assistant-`todo`-Entities vorhanden
sein. Das Add-on spricht nicht direkt mit Amazon oder Bring, sondern nutzt die
Home-Assistant-Services `todo.get_items`, `todo.add_item`,
`todo.update_item` und optional `todo.remove_completed_items`.
