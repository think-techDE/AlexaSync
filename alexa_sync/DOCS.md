# Alexa Sync Dokumentation

## Installation

1. In Home Assistant zu **Einstellungen > Add-ons > Add-on Store** gehen.
2. Im Drei-Punkte-Menue **Repositories** waehlen.
3. Dieses Repository hinzufuegen:

   `https://github.com/think-techDE/AlexaSync`

4. **Alexa Sync** installieren.
5. Add-on starten.
6. Die **Weboberflaeche** oeffnen.
7. Amazon-Domain und Bring-/Ziel-Liste auswaehlen.
8. Wenn Alexa Media Player bereits eingerichtet ist:
   gewuenschte Sessions markieren und **Ausgewaehlte aus Alexa Media Player
   uebernehmen** klicken.
9. Weitere Amazon-Konten bei Bedarf mit **Amazon-Konto hinzufuegen** anlegen.
10. Sonst pro Konto **Amazon-Anmeldung oeffnen**, im eingeblendeten Amazon-
    Browser anmelden und danach **Session uebernehmen**.

## Synchronisation

Die Synchronisation laeuft komplett in diesem Add-on. Chromium/Selenium oeffnet
die Alexa-Einkaufsliste auf der Amazon-Webseite und nutzt importierte Amazon-
Session-Cookies. Amazon-Benutzername und Passwort werden nicht im Add-on
gespeichert.

Wenn Alexa Media Player bereits laeuft, kann das Add-on dessen Session aus
`/homeassistant/.storage/alexa_media*.pickle` uebernehmen. Wenn mehrere Dateien
vorhanden sind, legt das Add-on nur fuer die in der Weboberflaeche markierten
Sessions Amazon-Konten an. Die Home-Assistant-Konfiguration wird dafuer nur
read-only eingebunden. Importierbare Cookie-Dateien werden angezeigt und als
aktiv oder ungeprueft markiert; doppelte Cookie-Dateien derselben Mailadresse
werden zusammengefasst. Aktive Alexa-Media-Player-Config-Entries ohne Cookie-
Datei werden sichtbar angezeigt, koennen aber nicht importiert werden. Falls
keine passende Session vorhanden ist, oeffnet die Weboberflaeche pro Konto einen
internen Amazon-Browser; nach erfolgreicher Anmeldung werden nur die Browser-
Cookies persistiert.

Mehrere Amazon-Konten werden gegen dieselbe `ha_list` synchronisiert:

- Neue Artikel aus einem Amazon-Konto werden in die Bring-/Ziel-Liste geschrieben.
- Neue Artikel aus Bring werden in alle aktiven Amazon-Konten geschrieben.
- Erledigte Bring-Artikel werden aus allen aktiven Alexa-Listen entfernt.
- Die Sync-Metadaten werden pro Amazon-Konto getrennt gespeichert.
- Ein Konto wird nach erfolgreicher Session-Uebernahme automatisch gespeichert.
- Aktivierte Konten ohne Session blockieren den Sync und werden in der Oberflaeche markiert.

Beispiel:

```yaml
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

## Voraussetzungen

Die Ziel-Liste, z.B. Bring, muss als Home-Assistant-`todo`-Entity vorhanden
sein. Alexa Sync spricht zusaetzlich ueber Chromium/Selenium mit
der Amazon-Webseite.

Unterstuetzte Architekturen: `amd64` und `aarch64`.
