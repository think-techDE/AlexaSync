# Alexa Sync Dokumentation

## Installation

1. In Home Assistant zu **Einstellungen > Add-ons > Add-on Store** gehen.
2. Im Drei-Punkte-Menue **Repositories** waehlen.
3. Dieses Repository hinzufuegen:

   `https://github.com/think-techDE/AlexaSync`

4. **Alexa Sync** installieren.
5. Add-on starten.
6. Die **Weboberflaeche** oeffnen.
7. Modus **Alexa direkt - Home Assistant Liste** waehlen.
8. Amazon-Domain und Bring-/Ziel-Liste auswaehlen.
9. **Amazon-Anmeldung oeffnen**, im eingeblendeten Amazon-Browser anmelden und
   danach **Session uebernehmen**.

## Direkter Alexa-Modus

Der direkte Modus laeuft komplett in diesem Add-on. Chromium/Selenium oeffnet
die Alexa-Einkaufsliste auf der Amazon-Webseite und nutzt importierte Amazon-
Session-Cookies. Amazon-Benutzername und Passwort werden nicht im Add-on
gespeichert. Die Weboberflaeche oeffnet dafuer einen internen Amazon-Browser;
nach erfolgreicher Anmeldung werden nur die Browser-Cookies persistiert.

Beispiel:

```yaml
mode: internal_alexa
amazon_domain: amazon.de
ha_list: todo.einkaufsliste_2
interval_seconds: 120
sync_completed: true
remove_completed: false
log_level: info
```

Falls der interne Browser nicht ausreicht, koennen Cookies weiterhin in der
Weboberflaeche importiert werden. Erwartet wird eine JSON-Liste im Format, das
Browser-Cookie-Export-Erweiterungen typischerweise liefern.

## HA-Listenmodus

```yaml
mode: ha_todo_pair
list_a: todo.einkaufsliste_2
list_b: todo.zuhause
interval_seconds: 120
sync_completed: true
remove_completed: false
log_level: info
```

## Externer Alexa-Server-Modus

Dieser Modus bleibt als Kompatibilitaetsmodus verfuegbar, falls bereits ein
Server aus `madmachinations/home-assistant-alexa-shopping-list` laeuft.

```yaml
mode: alexa_server
alexa_server_host: 192.168.1.10
alexa_server_port: 4000
ha_list: todo.einkaufsliste_2
interval_seconds: 120
sync_completed: true
remove_completed: false
log_level: info
```

## Voraussetzungen

Die Ziel-Liste, z.B. Bring, muss als Home-Assistant-`todo`-Entity vorhanden
sein. Der direkte Alexa-Modus spricht zusaetzlich ueber Chromium/Selenium mit
der Amazon-Webseite.

Unterstuetzte Architekturen: `amd64` und `aarch64`.
