# Alexa Sync

Home-Assistant-Add-on-Repository fuer die Synchronisation der Alexa-
Einkaufsliste mit einer Home-Assistant-`todo`-Liste, zum Beispiel Bring.

Seit Version `0.8.0` koennen mehrere Amazon-Konten mit derselben Bring-/Ziel-
Liste synchronisiert werden. Neue Bring-Eintraege werden in alle aktiven Alexa-
Listen geschrieben, erledigte Bring-Eintraege werden aus allen aktiven Alexa-
Listen entfernt.

Seit Version `0.7.0` kann das Add-on eine vorhandene Alexa-Media-Player-
Session aus Home Assistant uebernehmen. Damit ist im Normalfall keine zweite
Amazon-Anmeldung noetig.

Seit Version `0.6.0` bringt das Add-on den Alexa-Workaround direkt mit:
Chromium/Selenium laeuft im Add-on und liest die Alexa-Einkaufsliste mit
importierten Amazon-Session-Cookies. Das basiert auf dem technischen Ansatz von
[`madmachinations/home-assistant-alexa-shopping-list`](https://github.com/madmachinations/home-assistant-alexa-shopping-list),
aber ohne separates Server-Add-on.

## Installation

1. In Home Assistant zu **Einstellungen > Add-ons > Add-on Store** gehen.
2. Im Drei-Punkte-Menue **Repositories** waehlen.
3. Dieses Repository hinzufuegen:

   `https://github.com/think-techDE/AlexaSync`

4. **Alexa Sync** installieren.
5. Add-on starten und die **Weboberflaeche** oeffnen.
6. Amazon-Domain, z.B. `amazon.de`, und die Bring-/Ziel-Liste auswaehlen.
7. **Sessions uebernehmen** klicken. Das Add-on importiert die gefundenen
   Alexa-Media-Player-Sessions, legt daraus automatisch die Amazon-Konten an
   und speichert die aktuelle Ziel-Listen-Konfiguration mit.

Beim erneuten Uebernehmen ersetzt die aktuelle Auswahl die importierten Konten.
Nicht mehr gewuenschte Konten koennen in der Weboberflaeche entfernt werden.

Die YAML-Konfiguration bleibt optional als Startwert moeglich:

```yaml
mode: internal_alexa
amazon_domain: amazon.de
ha_list: todo.einkaufsliste_2
interval_seconds: 120
sync_completed: true
remove_completed: false
log_level: info
```

## Add-on

Der eigentliche Add-on-Code liegt in [`alexa_sync`](alexa_sync).

Das Add-on nutzt den Home-Assistant-Core-API-Proxy des Supervisors. Dafuer ist
in `alexa_sync/config.yaml` `homeassistant_api: true` gesetzt. Fuer den Import
aus Alexa Media Player wird die Home-Assistant-Konfiguration zusaetzlich
read-only unter `/homeassistant` eingebunden.
