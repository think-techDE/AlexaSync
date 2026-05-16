# Changelog

## 0.9.16

- Bereits bekannte erledigte Home-Assistant-Eintraege loesen keine nachtraegliche Alexa-Entfernung mehr aus.
- Bei mehreren aktiven Amazon-Konten wird ein in einem einzelnen Konto fehlender Alexa-Eintrag nicht mehr als global erledigt gewertet.

## 0.9.15

- Alexa-HTTP-Sync erkennt Amazons `listType=SHOP` jetzt als Einkaufsliste.
- Listen-IDs aus der HTTP-Listenmetadaten-Antwort werden robuster aus String- und Listenformen gelesen.

## 0.9.14

- Schutz gegen fehlerhafte Massen-Abschluesse: Wenn viele Alexa-Eintraege auf einmal zu fehlen scheinen, werden Home-Assistant-Eintraege nicht automatisch erledigt.
- Einzelne verschwundene Alexa-Eintraege muessen in zwei Sync-Laeufen bestaetigt werden, bevor sie in Home Assistant als erledigt markiert werden.
- HTTP-Listenfehler nennen nun erkannte Listentypen und Namen, damit die Alexa-Einkaufsliste besser diagnostiziert werden kann.

## 0.9.13

- Alexa-HTTP-Sync enger an `alexa-remote2` angepasst: offizieller Alexa-App-User-Agent, kompletter AMP-Cookie-Header und V2-Payloads mit `type`-Objekten.
- HTTP-Antworten mit `gzip` oder `deflate` werden fuer Logs und JSON-Auswertung korrekt dekodiert.
- Standard-Sync-Intervall von 120 auf 150 Sekunden erhoeht.

## 0.9.12

- Alexa-HTTP-Sync auf die aktuellen internen Listen-V2-Endpunkte umgestellt (`/lists/fetch`, `/items/fetch`).
- Cookie-Header werden fuer HTTP ohne zusaetzliches Quoting uebernommen, damit AMP-Cookies unveraendert an Amazon gehen.
- HTTP-Cookies werden wie im Browser nach Request-Host gefiltert.
- HTTP-Fallback-Logs enthalten jetzt den konkreten HTTP-Fehler statt nur eine generische Authentifizierungswarnung.

## 0.9.11

- Neuer HTTP-Sync-Pfad fuer Alexa-Shopping-Listen: AMP-Cookies werden zuerst direkt gegen Amazons interne Listen-Endpunkte genutzt.
- Chromium/Selenium wird nur noch als Fallback gestartet, wenn der HTTP-Pfad fuer ein Konto fehlschlaegt.

## 0.9.10

- Unnoetige Amazon-Seitenladungen innerhalb eines Sync-Durchlaufs vermieden, wenn die Alexa-Einkaufsliste bereits in Chromium geoeffnet ist.
- Dadurch sinkt die CPU-Last besonders bei mehreren Schreibvorgaengen oder mehreren erledigten Eintraegen pro Sync.

## 0.9.9

- CPU-Last beim Entfernen erledigter Alexa-Eintraege reduziert: mehrere Eintraege werden jetzt in einer geoeffneten Amazon-Liste gebuendelt entfernt.
- Amazon-Session-Cookies werden pro Chromium-Lauf nur noch einmal geladen, statt bei jedem Listenaufruf erneut.

## 0.9.8

- Alexa-Eintraege werden robuster entfernt, wenn Amazon die virtuelle Einkaufsliste waehrend Scroll oder Klick neu rendert.
- Dokumentation und README auf den vereinfachten Alexa-Media-Player-Sessionimport aktualisiert.

## 0.9.7

- Alexa-Media-Player-Import ersetzt jetzt die importierte Kontenliste durch die aktuell ausgewaehlten Sessions, statt alte Eintraege mitzuschleppen.
- Importierte Konten koennen in der Weboberflaeche wieder entfernt werden.

## 0.9.6

- Weboberflaeche auf den Alexa-Media-Player-Hauptpfad reduziert: Ziel-Liste waehlen, Sessions uebernehmen, synchronisieren.
- Manueller Amazon-Login, Cookie-JSON-Import und manuelle Kontoverwaltung wurden aus Weboberflaeche und API entfernt.

## 0.9.5

- Alexa-Media-Player-Cookie-Pickles mit modernem Cookie-Attribut `Partitioned` werden jetzt ohne Importfehler gelesen.

## 0.9.4

- Alexa-Media-Player-Sessionimport erkennt jetzt auch einfache AlexaPy-Cookie-Mappings neben verschachtelten CookieJar-Pickles.
- Session-Uebernahme speichert die aktuell in der Weboberflaeche gewaehlte Ziel-Liste und Sync-Optionen direkt mit.
- Alexa-Media-Player-Sessions sind in der Weboberflaeche vorausgewaehlt; der Hauptpfad ist damit Liste waehlen, gefundene Sessions uebernehmen, synchronisieren.

## 0.9.3

- Weboberflaeche grundlegend neu aufgebaut und auf die benoetigten Kernfunktionen reduziert.
- Alexa-Media-Player-Sessions werden uebersichtlicher dargestellt; nicht importierbare Konten erscheinen nur noch als Hinweis.
- Amazon-Konten, Login-Fallback, Cookie-Expertenimport, Sync-Verhalten und Status wurden klar getrennt.

## 0.9.2

- Sidebar-Icon auf das breit verfuegbare Home-Assistant-MDI-Icon `mdi:cart` umgestellt.

## 0.9.1

- Add-on-Grafiken `icon.png` und `logo.png` fuer die Home-Assistant-Darstellung hinzugefuegt.

## 0.9.0

- Backend in getrennte Module fuer Einstellungen, Home Assistant, Alexa-Client, Sync-Logik und Weboberflaeche aufgeteilt.
- Weboberflaeche auf den All-in-One-Pfad Alexa zu Bring/Ziel-Liste reduziert.
- Session-Uebernahmen aus Alexa Media Player, Login-Browser und Cookie-Import speichern das betroffene Amazon-Konto dauerhaft.
- Dokumentation und Add-on-Optionen an die vereinfachte Konfiguration angepasst.
- Umlaut-Normalisierung fuer den Listenabgleich robuster gemacht.
- Nicht mehr genutzte WebSocket-Abhaengigkeit entfernt.

## 0.8.4

- Weboberflaeche in klare Module fuer Ziel/Modus, Alexa-Media-Player, Amazon-Konten, Fallback und Sync-Verhalten gegliedert.
- Einzelne Session-Uebernahmen speichern das zugehoerige Amazon-Konto nun automatisch dauerhaft.
- Neue manuelle Amazon-Konten starten inaktiv, bis eine Session gespeichert wurde.
- Konten ohne Session werden in der Weboberflaeche klar als blockierend markiert, wenn sie aktiv sind.

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
