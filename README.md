# Alexa Sync

[![Version](https://img.shields.io/badge/version-0.9.8-blue.svg)](alexa_sync/CHANGELOG.md)
[![Architektur](https://img.shields.io/badge/arch-amd64%20%7C%20aarch64-lightgrey.svg)](alexa_sync/build.yaml)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Add--on-41BDF5.svg?logo=home-assistant)](https://www.home-assistant.io/addons/)

Home-Assistant-Add-on-Repository zur Synchronisation von Alexa-Einkaufslisten mit einer Home-Assistant-`todo`-Liste (z.B. Bring).

```
Alexa-Liste ──┐
              ├──► Alexa Sync ──► HA todo-Liste (z.B. Bring)
Alexa-Liste ──┘         ▲
                         │ Session-Cookies
                   Alexa Media Player
```

Alexa Sync nutzt vorhandene Sessions aus [Alexa Media Player](https://github.com/alandtse/alexa_media_player) — es werden **keine Amazon-Zugangsdaten** im Add-on gespeichert.

---

## Was das Add-on macht

| Richtung | Aktion |
|---|---|
| Alexa → HA | Neue Einträge aus Alexa-Listen importieren |
| HA → Alexa | Neue HA-Einträge in alle aktiven Alexa-Listen schreiben |
| HA → Alexa | Erledigte HA-Einträge aus Alexa-Listen entfernen |
| Mehrere Konten | Mehrere Amazon-Konten parallel synchronisieren |

Alexa Media Player-Sessions werden beim Start automatisch erkannt und als Amazon-Konten angelegt.

---

## Voraussetzungen

- [ ] Home Assistant mit Add-on-Unterstützung
- [ ] Eingerichteter [Alexa Media Player](https://github.com/alandtse/alexa_media_player) mit gültiger Amazon-Session
- [ ] Eine vorhandene Home-Assistant-`todo`-Liste (z.B. Bring)
- [ ] Architektur `amd64` oder `aarch64`

---

## Installation

1. In Home Assistant **Einstellungen → Add-ons → Add-on Store** öffnen.
2. Im Drei-Punkte-Menü **Repositories** wählen.
3. Dieses Repository hinzufügen:

   ```
   https://github.com/think-techDE/AlexaSync
   ```

4. **Alexa Sync** suchen und installieren.
5. Add-on starten und die **Weboberfläche** öffnen.
6. Amazon-Domain (z.B. `amazon.de`) und die Ziel-Liste auswählen.
7. **Sessions übernehmen** klicken.

> Beim erneuten Übernehmen ersetzt die aktuelle Auswahl die importierten Konten. Nicht mehr gewünschte Konten können in der Weboberfläche entfernt werden.

---

## Konfiguration

Die Weboberfläche ist der empfohlene Weg. Alle Optionen lassen sich alternativ auch per YAML setzen:

| Option | Standard | Beschreibung |
|---|---|---|
| `amazon_domain` | `amazon.de` | Amazon-Domain des Kontos |
| `ha_list` | _(leer)_ | Entity-ID der Ziel-`todo`-Liste |
| `interval_seconds` | `120` | Sync-Intervall in Sekunden |
| `sync_completed` | `true` | Erledigte Alexa-Einträge in HA übernehmen |
| `remove_completed` | `false` | Erledigte HA-Einträge aus Alexa entfernen |
| `log_level` | `info` | Log-Level (`debug`, `info`, `warning`, `error`) |

```yaml
amazon_domain: amazon.de
ha_list: todo.einkaufsliste_2
interval_seconds: 120
sync_completed: true
remove_completed: false
log_level: info
```

---

## Dokumentation

- Ausführliche Dokumentation: [`alexa_sync/DOCS.md`](alexa_sync/DOCS.md)
- Changelog: [`alexa_sync/CHANGELOG.md`](alexa_sync/CHANGELOG.md)
- Add-on-Verzeichnis: [`alexa_sync/`](alexa_sync)

---

## Unterstützung

Wenn Alexa Sync dir Zeit spart, freue ich mich über einen Kaffee ☕

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-thinktech-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/thinktech)

---

## Hinweise

> [!NOTE]
> Amazon stellt keine offizielle stabile API für Alexa-Einkaufslisten bereit. Alexa Sync öffnet die Einkaufsliste deshalb intern per Chromium/Selenium und nutzt die importierten Session-Cookies aus Alexa Media Player.
>
> Wenn Amazon eine Session beendet, muss sie in Alexa Media Player erneuert und im Add-on erneut übernommen werden.
