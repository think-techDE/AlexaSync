"""Synchronization logic for the Alexa Sync add-on."""

from __future__ import annotations

import logging
import time
from typing import Any

from settings import STATUS_NEEDS_ACTION, STATUS_COMPLETED, save_state
from ha_client import HomeAssistantClient, TodoItem, index_items
from alexa_client import InternalAlexaClient, account_cookie_path, enabled_amazon_accounts, sanitize_account_id

LOGGER = logging.getLogger("alexa_sync")


def resolve_status(item_a: TodoItem, item_b: TodoItem, state: dict[str, Any]) -> str | None:
    """Resolve which status should win for an item present in both lists."""
    if item_a.status == item_b.status:
        return item_a.status

    last_a = state.get("a_status")
    last_b = state.get("b_status")
    a_changed = last_a is not None and item_a.status != last_a
    b_changed = last_b is not None and item_b.status != last_b

    if a_changed and not b_changed:
        return item_a.status
    if b_changed and not a_changed:
        return item_b.status

    if STATUS_COMPLETED in {item_a.status, item_b.status}:
        return STATUS_COMPLETED
    return STATUS_NEEDS_ACTION


def remember(state: dict[str, Any], item_a: TodoItem | None, item_b: TodoItem | None) -> None:
    """Persist item sync metadata."""
    if item_a is not None:
        state["a_uid"] = item_a.uid
        state["a_status"] = item_a.status
        state["summary"] = item_a.summary
    if item_b is not None:
        state["b_uid"] = item_b.uid
        state["b_status"] = item_b.status
        state["summary"] = item_b.summary
    state["last_seen"] = time.time()


def suggest_todo_entity(entities: list[dict[str, str]]) -> str:
    """Suggest the most likely shopping list entity."""
    candidates = ("bring", "einkauf", "shopping")
    for entity in entities:
        haystack = f"{entity.get('name', '')} {entity.get('entity_id', '')}".casefold()
        if any(candidate in haystack for candidate in candidates):
            return entity["entity_id"]
    return entities[0]["entity_id"] if entities else ""


def sync_once(client: HomeAssistantClient, settings: dict[str, Any], state: dict[str, Any]) -> int:
    """Run one synchronization pass. Returns number of write operations."""
    return sync_internal_alexa_once(client, settings, state)


def get_internal_account_state(state: dict[str, Any], account_id: str) -> dict[str, Any]:
    """Return per-Amazon-account sync state, migrating the legacy state if needed."""
    from settings import DEFAULT_ACCOUNT_ID
    account_states = state.setdefault("amazon_accounts", {})
    safe_id = sanitize_account_id(account_id)
    if safe_id not in account_states:
        account_states[safe_id] = {"items": {}}
        if safe_id == DEFAULT_ACCOUNT_ID and isinstance(state.get("items"), dict):
            account_states[safe_id]["items"] = state["items"]
    return account_states[safe_id]


def sync_internal_alexa_once(
    client: HomeAssistantClient, settings: dict[str, Any], state: dict[str, Any]
) -> int:
    """Synchronize one or more built-in Alexa Selenium clients with one HA list."""
    writes = 0
    errors: list[str] = []
    account_settings = dict(settings)
    account_settings["remove_completed"] = False

    for account in enabled_amazon_accounts(settings):
        account_state = get_internal_account_state(state, account["id"])
        cookie_path = account_cookie_path(account["id"])
        try:
            with InternalAlexaClient(account["amazon_domain"], cookie_path) as alexa:
                if not alexa.is_authenticated():
                    raise RuntimeError("Amazon-Session ist nicht authentifiziert.")
                writes += sync_alexa_items_with_ha(
                    alexa,
                    client,
                    settings["ha_list"],
                    account_settings,
                    account_state,
                    alexa_label=f"Alexa-Liste {account['name']}",
                    save_after=False,
                )
        except Exception as exc:
            LOGGER.exception("Sync failed for Amazon account %s", account["name"])
            errors.append(f"{account['name']}: {exc}")

    state["updated_at"] = time.time()
    save_state(state)

    if errors:
        raise RuntimeError("Sync teilweise fehlgeschlagen: " + "; ".join(errors))

    if settings["remove_completed"]:
        LOGGER.info("Removing completed items from %s", settings["ha_list"])
        client.remove_completed_items(settings["ha_list"])
        writes += 1

    return writes


def sync_alexa_items_with_ha(
    alexa: Any,
    client: HomeAssistantClient,
    ha_entity: str,
    settings: dict[str, Any],
    state: dict[str, Any],
    *,
    alexa_label: str,
    save_after: bool = True,
) -> int:
    """Synchronize active Alexa items with one Home Assistant to-do list."""
    alexa_items = index_items(alexa.get_items())
    ha_items = index_items(client.get_items(ha_entity))
    stored_items = state.setdefault("items", {})
    keys = set(alexa_items) | set(ha_items) | set(stored_items)
    completed_alexa_items: list[TodoItem] = []
    writes = 0

    for key in sorted(keys):
        alexa_item = alexa_items.get(key)
        ha_item = ha_items.get(key)
        item_state = stored_items.setdefault(key, {})

        if alexa_item is None and ha_item is None:
            stored_items.pop(key, None)
            continue

        if alexa_item is None and ha_item is not None:
            item_state.pop("ha_only_baseline", None)
            if (
                item_state.get("a_uid")
                and item_state.get("b_status") == STATUS_NEEDS_ACTION
                and ha_item.status == STATUS_NEEDS_ACTION
            ):
                LOGGER.info(
                    "Marking '%s' in %s as completed because it disappeared from %s",
                    ha_item.summary,
                    ha_entity,
                    alexa_label,
                )
                client.update_status(ha_entity, ha_item.uid, STATUS_COMPLETED)
                ha_item.status = STATUS_COMPLETED
                writes += 1
                remember(item_state, alexa_item, ha_item)
                continue
            if ha_item.status == STATUS_NEEDS_ACTION:
                LOGGER.info("Creating '%s' in %s", ha_item.summary, alexa_label)
                alexa.add_item(ha_item)
                writes += 1
            remember(item_state, alexa_item, ha_item)
            continue

        if ha_item is None and alexa_item is not None:
            LOGGER.info("Creating '%s' in %s", alexa_item.summary, ha_entity)
            client.add_item(ha_entity, alexa_item)
            writes += 1
            remember(item_state, alexa_item, ha_item)
            continue

        if (
            alexa_item is not None
            and ha_item is not None
            and settings["sync_completed"]
            and ha_item.status == STATUS_COMPLETED
        ):
            LOGGER.info("Removing completed '%s' from Alexa", ha_item.summary)
            completed_alexa_items.append(alexa_item)

        remember(item_state, alexa_item, ha_item)

    if completed_alexa_items:
        if hasattr(alexa, "remove_items"):
            writes += alexa.remove_items(completed_alexa_items)
        else:
            for alexa_item in completed_alexa_items:
                removed = alexa.remove_item(alexa_item)
                if removed is not False:
                    writes += 1

    state["updated_at"] = time.time()
    if save_after:
        save_state(state)

    if settings["remove_completed"]:
        LOGGER.info("Removing completed items from %s", ha_entity)
        client.remove_completed_items(ha_entity)
        writes += 1

    return writes
