# Alexa Sync

Home-Assistant-Add-on-Repository fuer die Synchronisation von Alexa-
Einkaufslisten mit einer Home-Assistant-`todo`-Liste, typischerweise Bring.

Alexa Sync nutzt vorhandene Sessions aus
[Alexa Media Player](https://github.com/alandtse/alexa_media_player). Es werden
keine Amazon-Zugangsdaten im Add-on gespeichert.

## Was das Add-on macht

- Importiert Alexa-Media-Player-Sessions aus der Home-Assistant-Konfiguration.
- Legt daraus automatisch Amazon-Konten im Add-on an.
- Synchronisiert mehrere Alexa-Einkaufslisten mit einer gemeinsamen
  Home-Assistant-`todo`-Liste.
- Kopiert neue Alexa-Eintraege in die Ziel-Liste.
- Schreibt neue Ziel-Listen-Eintraege in alle aktiven Alexa-Listen.
- Entfernt in der Ziel-Liste erledigte Eintraege aus den Alexa-Listen.

## Voraussetzungen

- Home Assistant mit Add-on-Unterstuetzung.
- Eine vorhandene Home-Assistant-`todo`-Liste, z.B. Bring.
- Eingerichteter Alexa Media Player mit gueltiger Amazon-Session.
- Add-on-Architektur `amd64` oder `aarch64`.

## Installation

1. In Home Assistant **Einstellungen > Add-ons > Add-on Store** oeffnen.
2. Im Drei-Punkte-Menue **Repositories** waehlen.
3. Dieses Repository hinzufuegen:

   ```text
   https://github.com/think-techDE/AlexaSync
   ```

4. **Alexa Sync** installieren.
5. Add-on starten und die **Weboberflaeche** oeffnen.
6. Amazon-Domain, z.B. `amazon.de`, und die Bring-/Ziel-Liste auswaehlen.
7. **Sessions uebernehmen** klicken.

Beim erneuten Uebernehmen ersetzt die aktuelle Auswahl die importierten Konten.
Nicht mehr gewuenschte Konten koennen in der Weboberflaeche entfernt werden.

## Konfiguration

Die Weboberflaeche ist der normale Weg. Die YAML-Optionen bleiben als Startwerte
moeglich:

```yaml
amazon_domain: amazon.de
ha_list: todo.einkaufsliste_2
interval_seconds: 120
sync_completed: true
remove_completed: false
log_level: info
```

## Dokumentation

- Add-on-Dokumentation: [`alexa_sync/DOCS.md`](alexa_sync/DOCS.md)
- Add-on-Verzeichnis: [`alexa_sync`](alexa_sync)
- Changelog: [`alexa_sync/CHANGELOG.md`](alexa_sync/CHANGELOG.md)

## Hinweise

Amazon stellt keine offizielle stabile API fuer Alexa-Einkaufslisten bereit.
Alexa Sync oeffnet die Alexa-Einkaufsliste deshalb intern per Chromium/Selenium
und nutzt die importierten Session-Cookies aus Alexa Media Player.

Wenn Amazon eine Session beendet, muss sie in Alexa Media Player erneuert und im
Add-on erneut uebernommen werden.
