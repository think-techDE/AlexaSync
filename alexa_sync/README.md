# Alexa Sync

Synchronisiert eine oder mehrere Alexa-Einkaufslisten mit einer
Home-Assistant-`todo`-Liste, typischerweise der Bring-Einkaufsliste.

Typischer Einsatz:

- Mehrere Amazon-Konten schreiben per Sprachbefehl auf ihre Alexa-Einkaufsliste.
- Das Add-on kopiert neue Eintraege in die Bring-Einkaufsliste.
- Abgehakte Eintraege in Bring werden aus allen aktiven Alexa-Listen entfernt.

## Konfiguration

Die bevorzugte Konfiguration erfolgt ueber die **Weboberflaeche** des Add-ons
oder ueber den Sidebar-Eintrag **Alexa Sync**. Es muss nur die
Bring-/Ziel-Liste gewaehlt und mindestens eine Amazon-Session gespeichert werden. Wenn
Alexa Media Player bereits in Home Assistant eingerichtet ist, kann dessen
Session per Knopfdruck uebernommen werden. Sind mehrere Alexa-Media-Player-
Sessions vorhanden, sind sie vorausgewaehlt; beim Uebernehmen legt das Add-on
die Amazon-Konten automatisch an und speichert die aktuelle Ziel-Listen-
Konfiguration mit. Ein erneutes Uebernehmen ersetzt die importierte Kontenliste
durch die aktuell ausgewaehlten Sessions; einzelne importierte Konten koennen
in der Weboberflaeche entfernt werden.

Die YAML-Optionen bleiben als Startwerte erhalten.

| Option | Beschreibung |
| --- | --- |
| `amazon_domain` | Standard-Amazon-Domain fuer neue Konten, z.B. `amazon.de`. |
| `amazon_accounts` | Aus Alexa Media Player importierte Amazon-Konten. |
| `ha_list` | Bring-/Ziel-Liste als Home-Assistant-`todo`-Entity. |
| `interval_seconds` | Polling-Intervall in Sekunden. |
| `sync_completed` | Synchronisiert erledigte Eintraege. |
| `remove_completed` | Entfernt abgeschlossene Eintraege nach erfolgreichem Sync aus der HA-Liste. |
| `log_level` | Log-Level des Add-ons. |

## Amazon-Session

Amazon stellt keine offizielle stabile API fuer Alexa-Einkaufslisten bereit. Das
Add-on speichert deshalb keine Amazon-Zugangsdaten. Der einfachste Weg ist,
die gefundenen Alexa-Media-Player-Sessions mit **Sessions uebernehmen** zu
importieren. Dafuer liest das Add-on nur die gewaehlten
Alexa-Media-Player-Cookie-Dateien aus der Home-Assistant-Konfiguration und legt
je Datei ein Amazon-Konto an. Es akzeptiert dabei verschachtelte CookieJar-
Pickles und einfache AlexaPy-Cookie-Mappings. Der Zugriff auf die
Home-Assistant-Konfiguration ist read-only. Alte Cookie-Dateien werden
als ungeprueft markiert, wenn sie keinem aktiven Alexa-Media-Player-Config-Entry
mehr zugeordnet werden koennen; doppelte Dateien derselben Mailadresse erscheinen
nur einmal. Aktive Alexa-Media-Player-Konten ohne Cookie-Datei werden angezeigt,
koennen aber nicht aus Alexa Media Player importiert werden.

Wenn Amazon eine importierte Session beendet, muss die betroffene Session in
Alexa Media Player erneuert und danach im Add-on erneut uebernommen werden.
