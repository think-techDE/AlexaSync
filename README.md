# Alexa Sync

Home-Assistant-Add-on-Repository fuer die bidirektionale Synchronisation zweier
Home-Assistant-`todo`-Listen, zum Beispiel Alexa Einkaufsliste und Bring
Einkaufsliste.

Seit Version `0.3.0` kann das Add-on alternativ mit dem Alexa-Shopping-List-
Server aus dem Projekt
[`madmachinations/home-assistant-alexa-shopping-list`](https://github.com/madmachinations/home-assistant-alexa-shopping-list)
verbunden werden. Das ist der relevante Workaround, weil Amazon den direkten
Drittanbieterzugriff auf Alexa-Einkaufslisten abgeschaltet hat.

## Installation

1. In Home Assistant zu **Einstellungen > Add-ons > Add-on Store** gehen.
2. Im Drei-Punkte-Menue **Repositories** waehlen.
3. Dieses Repository hinzufuegen:

   `https://github.com/think-techDE/AlexaSync`

4. **Alexa Sync** installieren.
5. Add-on starten und die **Weboberflaeche** oeffnen.
6. Sync-Modus waehlen:
   - **Home Assistant Liste ↔ Home Assistant Liste**
   - **Alexa Shopping List Server ↔ Home Assistant Liste**
7. Listen bzw. Alexa-Serverdaten eintragen und speichern.

Nach dem Update auf Version `0.2.1` kann die Oberflaeche auch als Sidebar-Panel
**Alexa Sync** erscheinen.

Die YAML-Konfiguration bleibt optional als Fallback moeglich:

   ```yaml
   list_a: todo.alexa_einkaufsliste
list_b: todo.einkaufsliste_2
mode: ha_todo_pair
interval_seconds: 60
sync_completed: true
remove_completed: false
log_level: info
   ```

## Add-on

Der eigentliche Add-on-Code liegt in [`alexa_sync`](alexa_sync).

Das Add-on nutzt den Home-Assistant-Core-API-Proxy des Supervisors. Dafuer ist
in `alexa_sync/config.yaml` `homeassistant_api: true` gesetzt.
