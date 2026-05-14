# Alexa Sync Dokumentation

## Ziel

Alexa Sync verbindet Alexa-Einkaufslisten mit einer Home-Assistant-`todo`-Liste.
Der normale Weg ist bewusst kurz:

1. Alexa Media Player stellt die Amazon-Session bereit.
2. Alexa Sync uebernimmt diese Session.
3. Alexa Sync synchronisiert die Alexa-Einkaufslisten mit der Ziel-Liste.

## Voraussetzungen

| Voraussetzung | Warum |
| --- | --- |
| Home Assistant mit Add-ons | Alexa Sync laeuft als Home-Assistant-Add-on. |
| Alexa Media Player | Liefert die Amazon-Session-Cookies. |
| Home-Assistant-`todo`-Liste | Ziel der Synchronisation, z.B. Bring. |
| Architektur `amd64` oder `aarch64` | Dort ist Chromium im Add-on stabil verfuegbar. |

## Installation

1. In Home Assistant **Einstellungen > Add-ons > Add-on Store** oeffnen.
2. Im Drei-Punkte-Menue **Repositories** waehlen.
3. Repository hinzufuegen:

   ```text
   https://github.com/think-techDE/AlexaSync
   ```

4. **Alexa Sync** installieren.
5. Add-on starten.
6. **Weboberflaeche** oeffnen.

## Einrichtung

1. Bei **Ziel** die Bring-/Home-Assistant-Liste auswaehlen.
2. Die passende Amazon-Domain eintragen, z.B. `amazon.de`.
3. Unter **Alexa Media Player** die gewuenschten Sessions markiert lassen.
4. **Sessions uebernehmen** klicken.
5. Pruefen, ob die Konten unter **Importierte Konten** erscheinen.
6. Optional **Jetzt synchronisieren** klicken.

Beim erneuten Uebernehmen ersetzt die aktuelle Auswahl die importierte
Kontenliste. Das ist Absicht: Wenn eine alte Session nicht mehr gewuenscht ist,
einfach nicht mehr markieren und erneut uebernehmen.

## Entfernen von Konten

In **Importierte Konten** kann jedes Konto mit **Entfernen** aus der
Konfiguration geloescht werden. Danach ist es nicht mehr Teil des Syncs.

## Was beim Session-Import passiert

Alexa Sync liest Alexa-Media-Player-Dateien aus:

```text
/homeassistant/.storage/alexa_media*.pickle
```

Unterstuetzt werden:

- verschachtelte CookieJar-Pickles,
- einfache AlexaPy-Cookie-Mappings,
- moderne Cookie-Attribute wie `Partitioned`.

Die Home-Assistant-Konfiguration ist im Add-on read-only eingebunden. Alexa Sync
speichert nur die daraus extrahierten Cookies pro importiertem Konto unter
`/data`.

## Synchronisation

Mehrere Amazon-Konten koennen gegen dieselbe Ziel-Liste synchronisiert werden.

| Situation | Verhalten |
| --- | --- |
| Neuer Artikel in Alexa | Wird in die Ziel-Liste geschrieben. |
| Neuer Artikel in der Ziel-Liste | Wird in alle aktiven Alexa-Listen geschrieben. |
| Artikel in der Ziel-Liste erledigt | Wird aus den Alexa-Listen entfernt. |
| Artikel verschwindet aus Alexa | Wird in der Ziel-Liste als erledigt markiert. |

## YAML-Startwerte

Die Weboberflaeche ist der bevorzugte Weg. Diese Optionen koennen als Startwerte
gesetzt werden:

```yaml
amazon_domain: amazon.de
ha_list: todo.einkaufsliste_2
interval_seconds: 120
sync_completed: true
remove_completed: false
log_level: info
```

## Fehlerbehebung

| Problem | Ursache | Loesung |
| --- | --- | --- |
| Keine Session gefunden | Alexa Media Player hat keine Cookie-Datei geschrieben. | Alexa Media Player pruefen und einmal neu authentifizieren. |
| Konto ohne Cookie-Datei | Home Assistant kennt ein Alexa-Media-Player-Konto, aber es gibt keine passende Sessiondatei. | Alexa Media Player fuer dieses Konto erneut anmelden. |
| Amazon-Session ist nicht authentifiziert | Amazon hat die importierte Session beendet. | Session in Alexa Media Player erneuern und im Add-on erneut uebernehmen. |
| Doppelte oder alte Konten | Veraltete importierte Konten in der Konfiguration. | Gewuenschte Sessions markieren und erneut uebernehmen oder alte Konten entfernen. |

## Datenschutz

- Keine Amazon-Zugangsdaten im Add-on.
- Read-only-Zugriff auf die Home-Assistant-Konfiguration.
- Cookie-Speicherung nur im Add-on-Datenverzeichnis.
