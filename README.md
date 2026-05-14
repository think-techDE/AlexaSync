# Alexa Sync

[![Version](https://img.shields.io/badge/version-0.9.8-blue.svg)](alexa_sync/CHANGELOG.md)
[![Architektur](https://img.shields.io/badge/arch-amd64%20%7C%20aarch64-lightgrey.svg)](alexa_sync/build.yaml)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Add--on-41BDF5.svg?logo=home-assistant)](https://www.home-assistant.io/addons/)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-thinktech-FFDD00?logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/thinktech)

🇩🇪 [Deutsch](README.de.md) | 🇬🇧 English

Home Assistant add-on repository for synchronizing Alexa shopping lists with a Home Assistant `todo` list (e.g. Bring).

```
Alexa list ──┐
             ├──► Alexa Sync ──► HA todo list (e.g. Bring)
Alexa list ──┘        ▲
                       │ session cookies
                 Alexa Media Player
```

Alexa Sync reuses existing sessions from [Alexa Media Player](https://github.com/alandtse/alexa_media_player) — **no Amazon credentials** are stored in the add-on.

---

## What the add-on does

| Direction | Action |
|---|---|
| Alexa → HA | Import new items from Alexa lists |
| HA → Alexa | Write new HA items to all active Alexa lists |
| HA → Alexa | Remove completed HA items from Alexa lists |
| Multi-account | Sync multiple Amazon accounts in parallel |

Alexa Media Player sessions are detected automatically on start and registered as Amazon accounts.

---

## Requirements

- [ ] Home Assistant with add-on support
- [ ] [Alexa Media Player](https://github.com/alandtse/alexa_media_player) configured with a valid Amazon session
- [ ] An existing Home Assistant `todo` list (e.g. Bring)
- [ ] Architecture `amd64` or `aarch64`

---

## Installation

1. Open **Settings → Add-ons → Add-on Store** in Home Assistant.
2. Select **Repositories** from the three-dot menu.
3. Add this repository:

   ```
   https://github.com/think-techDE/AlexaSync
   ```

4. Find and install **Alexa Sync**.
5. Start the add-on and open the **web UI**.
6. Select the Amazon domain (e.g. `amazon.de`) and the target list.
7. Click **Import Sessions**.

> Re-importing replaces the current account list with the new selection. Accounts you no longer need can also be removed individually in the web UI.

---

## Configuration

The web UI is the recommended way. All options can also be set via YAML:

| Option | Default | Description |
|---|---|---|
| `amazon_domain` | `amazon.de` | Amazon domain of the account |
| `ha_list` | _(empty)_ | Entity ID of the target `todo` list |
| `interval_seconds` | `120` | Sync interval in seconds |
| `sync_completed` | `true` | Import completed Alexa items into HA |
| `remove_completed` | `false` | Remove completed HA items from Alexa |
| `log_level` | `info` | Log level (`debug`, `info`, `warning`, `error`) |

```yaml
amazon_domain: amazon.de
ha_list: todo.shopping_list
interval_seconds: 120
sync_completed: true
remove_completed: false
log_level: info
```

---

## Support

Alexa Sync is a private open-source project. If it saves you time, I'd appreciate a coffee ☕

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-thinktech-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/thinktech)

---

## Documentation

- Full documentation: [`alexa_sync/DOCS.en.md`](alexa_sync/DOCS.en.md)
- Changelog: [`alexa_sync/CHANGELOG.md`](alexa_sync/CHANGELOG.md)
- Add-on directory: [`alexa_sync/`](alexa_sync)

---

## Notes

> [!NOTE]
> Amazon does not provide an official stable API for Alexa shopping lists. Alexa Sync therefore opens the shopping list internally via Chromium/Selenium and uses the session cookies imported from Alexa Media Player.
>
> If Amazon terminates a session, it must be renewed in Alexa Media Player and re-imported in the add-on.
