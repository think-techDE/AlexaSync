# Alexa Sync Documentation

## Goal

Alexa Sync connects Alexa shopping lists with one or more Home Assistant
`todo` lists.
The intended workflow is intentionally short:

1. Alexa Media Player provides the Amazon session.
2. Alexa Sync imports that session.
3. Alexa Sync synchronizes the Alexa shopping lists with the target lists.

## Requirements

| Requirement | Why |
| --- | --- |
| Home Assistant with add-ons | Alexa Sync runs as a Home Assistant add-on. |
| Alexa Media Player | Provides the Amazon session cookies. |
| Home Assistant `todo` list | Synchronization target, e.g. Bring. Multiple targets are supported. |
| Architecture `amd64` or `aarch64` | Chromium is stably available on these platforms. |

## Installation

1. Open **Settings → Add-ons → Add-on Store** in Home Assistant.
2. Select **Repositories** from the three-dot menu.
3. Add the repository:

   ```text
   https://github.com/think-techDE/AlexaSync
   ```

4. Install **Alexa Sync**.
5. Start the add-on.
6. Open the **web UI**.

## Setup

1. Under **Targets**, select one or more Bring / Home Assistant lists.
2. Enter the correct Amazon domain, e.g. `amazon.de`.
3. Under **Alexa Media Player**, leave the desired sessions checked.
4. Click **Import Sessions**.
5. Verify that the accounts appear under **Imported Accounts**.
6. Optionally click **Sync Now**.

Re-importing replaces the current account list with the new selection. This is intentional: to stop syncing an old session, simply uncheck it and re-import.

## Removing Accounts

In **Imported Accounts**, any account can be deleted from the configuration using **Remove**. After removal it is no longer part of the sync.

## What Happens During Session Import

Alexa Sync reads Alexa Media Player files from:

```text
/homeassistant/.storage/alexa_media*.pickle
```

Supported formats:

- nested CookieJar pickles,
- simple AlexaPy cookie mappings,
- modern cookie attributes such as `Partitioned`.

The Home Assistant configuration is mounted read-only in the add-on. Alexa Sync only stores the extracted cookies per imported account under `/data`.

## Synchronization

Multiple Amazon accounts and multiple Home Assistant target lists can be
synchronized. All selected target lists are matched against the same Alexa
shopping list.

| Situation | Behaviour |
| --- | --- |
| New item in Alexa | Written to all target lists. |
| New item in a target list | Written to all active Alexa lists. |
| Item completed in the target list | Removed from all Alexa lists. |
| Item disappears from Alexa | Marked as completed in the target lists. |

## YAML Default Values

The web UI is the preferred way. These options can be set as initial values:

```yaml
amazon_domain: amazon.de
ha_list: todo.shopping_list
ha_lists:
  - todo.shopping_list
  - todo.hardware_store
interval_seconds: 150
sync_completed: true
remove_completed: false
log_level: info
```

## Troubleshooting

| Problem | Cause | Solution |
| --- | --- | --- |
| No session found | Alexa Media Player has not written a cookie file. | Check Alexa Media Player and re-authenticate once. |
| Account without cookie file | Home Assistant knows an Alexa Media Player account but there is no matching session file. | Re-authenticate Alexa Media Player for that account. |
| Amazon session not authenticated | Amazon has terminated the imported session. | Renew the session in Alexa Media Player and re-import it in the add-on. |
| Duplicate or old accounts | Outdated imported accounts in the configuration. | Check the desired sessions and re-import, or remove old accounts individually. |

## Privacy

- No Amazon credentials stored in the add-on.
- Read-only access to the Home Assistant configuration.
- Cookies are stored only in the add-on data directory.
