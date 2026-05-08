# Alexa Sync

Home-Assistant-Add-on-Repository fuer die bidirektionale Synchronisation zweier
Home-Assistant-`todo`-Listen, zum Beispiel Alexa Einkaufsliste und Bring
Einkaufsliste.

## Installation

1. In Home Assistant zu **Einstellungen > Add-ons > Add-on Store** gehen.
2. Im Drei-Punkte-Menue **Repositories** waehlen.
3. Dieses Repository hinzufuegen:

   `https://github.com/think-techDE/AlexaSync`

4. **Alexa Sync** installieren.
5. Add-on starten und den Reiter **Weboberflaeche** oeffnen.
6. Die beiden Listen per Dropdown auswaehlen und speichern.

Die YAML-Konfiguration bleibt optional als Fallback moeglich:

   ```yaml
   list_a: todo.alexa_einkaufsliste
   list_b: todo.einkaufsliste_2
   interval_seconds: 60
   sync_completed: true
   remove_completed: false
   log_level: info
   ```

## Add-on

Der eigentliche Add-on-Code liegt in [`alexa_sync`](alexa_sync).

Das Add-on nutzt den Home-Assistant-Core-API-Proxy des Supervisors. Dafuer ist
in `alexa_sync/config.yaml` `homeassistant_api: true` gesetzt.
