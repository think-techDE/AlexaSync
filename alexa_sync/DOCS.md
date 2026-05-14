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
8. **Sessions uebernehmen** klicken. Die gefundenen Alexa-Media-Player-Sessions
   sind vorausgewaehlt; das Add-on legt daraus die Amazon-Konten an und
   speichert die aktuelle Ziel-Liste direkt mit.

## Synchronisation

Die Synchronisation laeuft komplett in diesem Add-on. Chromium/Selenium oeffnet
die Alexa-Einkaufsliste auf der Amazon-Webseite und nutzt importierte Amazon-
Session-Cookies. Amazon-Benutzername und Passwort werden nicht im Add-on
gespeichert.

Wenn Alexa Media Player bereits laeuft, kann das Add-on dessen Session aus
`/homeassistant/.storage/alexa_media*.pickle` uebernehmen. Wenn mehrere Dateien
vorhanden sind, legt das Add-on fuer die vorausgewaehlten Sessions Amazon-
Konten an. Dabei werden verschachtelte CookieJar-Pickles und einfache AlexaPy-
Cookie-Mappings akzeptiert. Die Home-Assistant-Konfiguration wird dafuer nur
read-only eingebunden. Importierbare Cookie-Dateien werden angezeigt und als
aktiv oder ungeprueft markiert; doppelte Cookie-Dateien derselben Mailadresse
werden zusammengefasst. Aktive Alexa-Media-Player-Config-Entries ohne Cookie-
Datei werden sichtbar angezeigt, koennen aber nicht importiert werden.

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

Wenn Amazon eine importierte Session beendet, muss die betroffene Session in
Alexa Media Player erneuert und danach im Add-on erneut uebernommen werden.

## Voraussetzungen

Die Ziel-Liste, z.B. Bring, muss als Home-Assistant-`todo`-Entity vorhanden
sein. Alexa Sync spricht zusaetzlich ueber Chromium/Selenium mit
der Amazon-Webseite.

Unterstuetzte Architekturen: `amd64` und `aarch64`.
