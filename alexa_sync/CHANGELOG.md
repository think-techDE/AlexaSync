# Changelog

## 0.8.3

- Alexa-Media-Player-Sessions werden nicht mehr unsichtbar gefiltert, sondern als aktiv, ungeprueft oder ohne Cookie-Datei markiert.
- Aktive Alexa-Media-Player-Config-Entries ohne importierbare Cookie-Datei werden in der Weboberflaeche sichtbar angezeigt.
- Die Session-Auswahl und Konto-Zuordnung zeigen nur importierbare Cookie-Dateien als auswaehlbar an.

## 0.8.2

- Alexa-Entfernen ist robuster gegen Amazon-DOM-Refreshes und retryt stale Selenium-Elemente.
- Alexa-Media-Player-Sessionauswahl wird gegen aktive Alexa-Media-Player-Config-Entries gefiltert.
- Doppelte Alexa-Media-Player-Sessiondateien fuer dieselbe Mailadresse werden nur noch einmal angeboten.

## 0.8.1

- Alexa-Media-Player-Import uebernimmt nur noch explizit ausgewaehlte Sessions.
- Weboberflaeche zeigt gefundene Alexa-Media-Player-Sessions als Auswahl an.
- Konto-Import verlangt eine konkrete Alexa-Media-Player-Session statt automatisch die erste passende zu nehmen.

## 0.8.0

- Mehrere Amazon-Konten koennen mit einer gemeinsamen Bring-/HA-Ziel-Liste synchronisiert werden.
- Alexa-Media-Player-Import kann alle gefundenen Sessions automatisch als Konten anlegen.
- Amazon-Session-Cookies und Sync-Metadaten werden pro Konto getrennt gespeichert.
- Weboberflaeche um Kontoverwaltung, Konto-Status und kontoabhaengige Session-Aktionen erweitert.

## 0.7.0

- Alexa-Media-Player-Session kann per Knopfdruck aus der Home-Assistant-Konfiguration uebernommen werden.
- Home-Assistant-Konfiguration wird dafuer read-only in das Add-on eingebunden.
- Weboberflaeche zeigt an, ob eine Alexa-Media-Player-Session gefunden wurde.

## 0.1.0

- Initiale Add-on-Version.
- Bidirektionale Synchronisation zweier Home-Assistant-To-do-Listen.
- Persistenter Status unter `/data/sync_state.json`.

## 0.2.0

- Ingress-Weboberflaeche fuer die Listenauswahl und Sync-Optionen.
- Manuelle Synchronisation ueber die Add-on-Weboberflaeche.

## 0.2.1

- Versions-Bump fuer sauberes Add-on-Update auf die Weboberflaechen-Version.
- Sidebar-Panel fuer die Weboberflaeche aktiviert.

## 0.3.0

- Alexa-Shopping-List-Server-Modus hinzugefuegt.
- Weboberflaeche um Sync-Modus, Alexa-Server-Host und Alexa-Server-Port erweitert.
- Docker-Image installiert `websockets` fuer die Kommunikation mit dem Alexa-Server.

## 0.4.0

- Direkten Alexa-Modus integriert: Chromium/Selenium laeuft im Add-on.
- Weboberflaeche um Amazon-Domain, Cookie-Import und Authentifizierungspruefung erweitert.
- Docker-Image installiert Chromium, Chromedriver und Selenium.

## 0.5.0

- Standardoberflaeche auf Alexa-zu-Bring-Sync vereinfacht.
- Erweiterte Modi eingeklappt, damit der Hauptpfad klar bleibt.
- Alpine-Edge-Repositories entfernt und Build auf die Home-Assistant-Base-Repositories zurueckgefuehrt.
- Unterstuetzte Architekturen auf `amd64` und `aarch64` begrenzt, weil Chromium dort am stabilsten verfuegbar ist.
- Amazon-Cookies werden mit restriktiven Dateirechten gespeichert.
- Wenn ein bereits synchronisierter Alexa-Eintrag dort verschwindet, wird der HA-Eintrag als erledigt markiert statt erneut nach Alexa geschrieben.

## 0.6.0

- Interaktiven Amazon-Setup-Browser in die Weboberflaeche integriert.
- Direkten Amazon-Passwort-Endpoint entfernt.
- Session kann nach manueller Anmeldung im internen Browser uebernommen werden.

## 0.6.1

- Setup-Browser startet ueber die Amazon-Konto-Seite statt ueber die fehleranfaellige `/ap/signin`-URL.
- Amazon-Domain-Eingabe wird normalisiert, z.B. `https://www.amazon.de/...` zu `amazon.de`.
- Fallback auf Amazon-Startseite, falls die Konto-URL eine Fehlerseite liefert.

## 0.6.2

- Sync wartet ohne Fehler-Stacktrace, solange noch keine Amazon-Session gespeichert ist.
- Selenium-Verbindungswarnungen aus der interaktiven Browsersteuerung werden unterdrueckt.
- Erster Alexa/Bring-Abgleich ist konservativer: bestehende Bring-Eintraege werden als Bestand markiert und nicht massenhaft nach Alexa kopiert.

## 0.6.3

- Erstabgleich bildet wieder bewusst die Vereinigung aus Alexa- und Bring-/HA-Liste.
- Bereits beidseitig vorhandene Artikel werden per normalisiertem Namen referenziert, statt doppelt angelegt.
- Normalisierung fuer Artikelnamen verbessert, inklusive Umlauten und Satzzeichen.
