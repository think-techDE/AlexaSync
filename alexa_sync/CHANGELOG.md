# Changelog

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
