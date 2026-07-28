"""Synchronization logic for the Alexa Sync add-on."""

from __future__ import annotations

import logging
import time
from typing import Any

from settings import STATUS_NEEDS_ACTION, STATUS_COMPLETED, save_state
from ha_client import HomeAssistantClient, TodoItem, index_items
from alexa_client import (
    HttpAlexaClient,
    InternalAlexaClient,
    account_cookie_path,
    enabled_amazon_accounts,
    sanitize_account_id,
)

LOGGER = logging.getLogger("alexa_sync")
MISSING_COMPLETION_CONFIRMATIONS = 2
MISSING_COMPLETION_BULK_LIMIT = 5
HA_DELETION_BULK_LIMIT = 5


def configured_ha_targets(settings: dict[str, Any]) -> list[str]:
    """Return configured Home Assistant target lists."""
    targets = settings.get("ha_lists") or ([settings.get("ha_list")] if settings.get("ha_list") else [])
    return [str(target).strip() for target in targets if str(target).strip()]


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
        state.pop("a_missing_count", None)
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


def get_internal_target_state(
    account_state: dict[str, Any],
    ha_entity: str,
    *,
    migrate_legacy: bool = False,
) -> dict[str, Any]:
    """Return per-target sync state, migrating the legacy account item state if needed."""
    target_states = account_state.setdefault("targets", {})
    if ha_entity not in target_states:
        target_states[ha_entity] = {"items": {}}
        if migrate_legacy and isinstance(account_state.get("items"), dict):
            target_states[ha_entity]["items"] = account_state["items"]
            account_state.pop("items", None)
    return target_states[ha_entity]


def get_ha_targets_state(state: dict[str, Any], targets: list[str]) -> dict[str, Any]:
    """Return HA target sync state, migrating known per-account target metadata."""
    ha_state = state.get("ha_targets")
    if not isinstance(ha_state, dict):
        ha_state = {"targets": {}, "initialized": False}
        state["ha_targets"] = ha_state

    target_states = ha_state.setdefault("targets", {})
    for target in targets:
        target_states.setdefault(target, {"items": {}})

    if ha_state.get("initialized"):
        return ha_state

    migrated = False
    for account_state in (state.get("amazon_accounts") or {}).values():
        if not isinstance(account_state, dict):
            continue
        account_targets = account_state.get("targets") or {}
        if not isinstance(account_targets, dict):
            continue
        for target in targets:
            source_target = account_targets.get(target) or {}
            source_items = source_target.get("items") or {}
            if not isinstance(source_items, dict):
                continue
            target_items = target_states.setdefault(target, {"items": {}}).setdefault("items", {})
            for key, item_state in source_items.items():
                if not isinstance(item_state, dict) or key in target_items:
                    continue
                uid = item_state.get("b_uid")
                status = item_state.get("b_status")
                summary = item_state.get("summary")
                if not (uid or status or summary):
                    continue
                target_items[key] = {
                    "uid": uid,
                    "status": status or STATUS_NEEDS_ACTION,
                    "summary": summary or key,
                    "last_seen": item_state.get("last_seen") or time.time(),
                }
                migrated = True

    if migrated:
        ha_state["initialized"] = True
    return ha_state


def remember_ha_target_item(item_state: dict[str, Any], item: TodoItem) -> None:
    """Persist HA target item metadata."""
    item_state["uid"] = item.uid
    item_state["status"] = item.status
    item_state["summary"] = item.summary
    item_state["last_seen"] = time.time()


def is_new_active_occurrence(
    item: TodoItem | None,
    item_state: dict[str, Any],
    *,
    uid_key: str,
    status_key: str,
    missing_uid_is_new: bool,
) -> bool:
    """Return whether an active item is a new occurrence of a reused name."""
    if item is None or item.status != STATUS_NEEDS_ACTION:
        return False

    stored_uid = item_state.get(uid_key)
    if not stored_uid:
        return missing_uid_is_new
    return stored_uid != item.uid or item_state.get(status_key) == STATUS_COMPLETED


def reset_reused_item_state(
    item_state: dict[str, Any],
    *,
    alexa_item: TodoItem | None,
    ha_item: TodoItem | None,
) -> tuple[bool, bool]:
    """Drop historical decisions when either side created a new occurrence."""
    new_alexa_occurrence = is_new_active_occurrence(
        alexa_item,
        item_state,
        uid_key="a_uid",
        status_key="a_status",
        missing_uid_is_new=False,
    )
    new_ha_occurrence = is_new_active_occurrence(
        ha_item,
        item_state,
        uid_key="b_uid",
        status_key="b_status",
        missing_uid_is_new=False,
    )
    if new_alexa_occurrence or new_ha_occurrence:
        LOGGER.info(
            "Resetting stale sync history for newly created '%s'",
            (ha_item or alexa_item).summary,
        )
        item_state.clear()
    return new_alexa_occurrence, new_ha_occurrence


def sync_ha_targets(
    client: HomeAssistantClient,
    settings: dict[str, Any],
    state: dict[str, Any],
) -> int:
    """Synchronize configured Home Assistant target lists with each other."""
    targets = configured_ha_targets(settings)
    if len(targets) < 2:
        return 0

    ha_state = get_ha_targets_state(state, targets)
    target_states = ha_state.setdefault("targets", {})
    target_items = {target: index_items(client.get_items(target)) for target in targets}

    if not ha_state.get("initialized"):
        for target, items in target_items.items():
            stored_items = target_states.setdefault(target, {"items": {}}).setdefault("items", {})
            for key, item in items.items():
                remember_ha_target_item(stored_items.setdefault(key, {}), item)
        ha_state["initialized"] = True
        ha_state["updated_at"] = time.time()
        return 0

    unreliable_targets: set[str] = set()
    for target in targets:
        stored_items = target_states.setdefault(target, {"items": {}}).setdefault("items", {})
        missing_known_active = [
            key
            for key, item_state in stored_items.items()
            if key not in target_items[target]
            and item_state.get("uid")
            and item_state.get("status") == STATUS_NEEDS_ACTION
        ]
        if len(missing_known_active) > HA_DELETION_BULK_LIMIT:
            unreliable_targets.add(target)
            LOGGER.warning(
                "Skipping Home Assistant target sync for %s because %s known items are missing",
                target,
                len(missing_known_active),
            )

    keys: set[str] = set()
    for target in targets:
        keys.update(target_items[target])
        keys.update(target_states.setdefault(target, {"items": {}}).setdefault("items", {}))

    completed_keys: set[str] = set()
    writes = 0

    for key in sorted(keys):
        current_by_target = {target: target_items[target].get(key) for target in targets}
        if not any(current_by_target.values()):
            for target in targets:
                if target not in unreliable_targets:
                    target_states[target]["items"].pop(key, None)
            continue

        new_active_sources = [
            (target, item)
            for target, item in current_by_target.items()
            if is_new_active_occurrence(
                item,
                target_states[target]["items"].get(key) or {},
                uid_key="uid",
                status_key="status",
                missing_uid_is_new=True,
            )
        ]
        if new_active_sources:
            source_target, source_item = new_active_sources[0]
            for target, item in current_by_target.items():
                if (
                    target in unreliable_targets
                    or (item is not None and item.status == STATUS_NEEDS_ACTION)
                ):
                    continue
                LOGGER.info(
                    "Creating new occurrence of '%s' in %s because it was newly added in %s",
                    source_item.summary,
                    target,
                    source_target,
                )
                client.add_item(target, source_item)
                added_item = TodoItem(
                    uid=source_item.summary,
                    summary=source_item.summary,
                    status=STATUS_NEEDS_ACTION,
                    description=source_item.description,
                )
                target_items[target][key] = added_item
                current_by_target[target] = added_item
                writes += 1
            continue

        completion_source = False
        if settings.get("sync_completed", True):
            completion_source = any(
                item is not None
                and item.status == STATUS_COMPLETED
                and target not in unreliable_targets
                for target, item in current_by_target.items()
            )
            if not completion_source:
                for target in targets:
                    if target in unreliable_targets or current_by_target[target] is not None:
                        continue
                    item_state = target_states[target]["items"].get(key) or {}
                    if (
                        item_state.get("uid")
                        and item_state.get("status") == STATUS_NEEDS_ACTION
                    ):
                        completion_source = True
                        break

        if completion_source:
            completed_keys.add(key)
            for target, item in current_by_target.items():
                if (
                    target in unreliable_targets
                    or item is None
                    or item.status == STATUS_COMPLETED
                ):
                    continue
                LOGGER.info(
                    "Marking '%s' in %s as completed because another target list completed it",
                    item.summary,
                    target,
                )
                client.update_status(target, item.uid, STATUS_COMPLETED)
                item.status = STATUS_COMPLETED
                writes += 1
            continue

        active_sources = [
            (target, item)
            for target, item in current_by_target.items()
            if item is not None and item.status == STATUS_NEEDS_ACTION
        ]
        if not active_sources:
            continue

        source_target, source_item = active_sources[0]
        for target in targets:
            if target in unreliable_targets or current_by_target[target] is not None:
                continue
            LOGGER.info(
                "Creating '%s' in %s because it exists in %s",
                source_item.summary,
                target,
                source_target,
            )
            client.add_item(target, source_item)
            added_item = TodoItem(
                uid=source_item.summary,
                summary=source_item.summary,
                status=STATUS_NEEDS_ACTION,
                description=source_item.description,
            )
            target_items[target][key] = added_item
            current_by_target[target] = added_item
            writes += 1

    for target in targets:
        if target in unreliable_targets:
            continue
        stored_items = target_states[target]["items"]
        for key in list(stored_items):
            if key not in target_items[target] and key not in completed_keys:
                stored_items.pop(key, None)
        for key, item in target_items[target].items():
            remember_ha_target_item(stored_items.setdefault(key, {}), item)
        for key in completed_keys:
            if key not in target_items[target] and key in stored_items:
                stored_items[key]["status"] = STATUS_COMPLETED
                stored_items[key]["last_seen"] = time.time()

    ha_state["updated_at"] = time.time()
    return writes


def sync_account_with_alexa_client(
    alexa: Any,
    client: HomeAssistantClient,
    settings: dict[str, Any],
    account: dict[str, Any],
    account_state: dict[str, Any],
    account_settings: dict[str, Any],
) -> int:
    """Synchronize one configured Amazon account with all configured HA targets."""
    if not alexa.is_authenticated():
        detail = getattr(alexa, "last_auth_error", None)
        if detail:
            raise RuntimeError(f"Amazon-Session ist nicht authentifiziert. HTTP-Details: {detail}")
        raise RuntimeError("Amazon-Session ist nicht authentifiziert.")

    writes = 0
    targets = configured_ha_targets(settings)
    for target_index, ha_entity in enumerate(targets):
        target_state = get_internal_target_state(
            account_state,
            ha_entity,
            migrate_legacy=target_index == 0,
        )
        writes += sync_alexa_items_with_ha(
            alexa,
            client,
            ha_entity,
            account_settings,
            target_state,
            alexa_label=f"Alexa-Liste {account['name']}",
            save_after=False,
        )
    return writes


def sync_internal_alexa_once(
    client: HomeAssistantClient, settings: dict[str, Any], state: dict[str, Any]
) -> int:
    """Synchronize one or more Alexa accounts with configured HA lists."""
    writes = 0
    errors: list[str] = []
    account_settings = dict(settings)
    account_settings["remove_completed"] = False
    accounts = enabled_amazon_accounts(settings)
    account_settings["allow_missing_completion"] = len(accounts) == 1
    writes += sync_ha_targets(client, settings, state)

    for account in accounts:
        account_state = get_internal_account_state(state, account["id"])
        cookie_path = account_cookie_path(account["id"])
        try:
            try:
                LOGGER.debug("Trying Alexa HTTP sync for Amazon account %s", account["name"])
                with HttpAlexaClient(account["amazon_domain"], cookie_path) as alexa:
                    writes += sync_account_with_alexa_client(
                        alexa,
                        client,
                        settings,
                        account,
                        account_state,
                        account_settings,
                    )
                LOGGER.debug("Alexa HTTP sync succeeded for Amazon account %s", account["name"])
            except Exception as http_exc:
                LOGGER.warning(
                    "Alexa HTTP sync failed for %s, falling back to Chromium: %s",
                    account["name"],
                    http_exc,
                )
                with InternalAlexaClient(account["amazon_domain"], cookie_path) as alexa:
                    writes += sync_account_with_alexa_client(
                        alexa,
                        client,
                        settings,
                        account,
                        account_state,
                        account_settings,
                    )
        except Exception as exc:
            LOGGER.exception("Sync failed for Amazon account %s", account["name"])
            errors.append(f"{account['name']}: {exc}")

    state["updated_at"] = time.time()
    save_state(state)

    if errors:
        raise RuntimeError("Sync teilweise fehlgeschlagen: " + "; ".join(errors))

    if settings["remove_completed"]:
        for ha_entity in configured_ha_targets(settings):
            LOGGER.info("Removing completed items from %s", ha_entity)
            client.remove_completed_items(ha_entity)
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
    completed_alexa_items: list[tuple[dict[str, Any], TodoItem]] = []
    missing_completion_candidates: list[tuple[dict[str, Any], TodoItem]] = []
    ha_deletion_candidates: list[tuple[dict[str, Any], TodoItem]] = []
    writes = 0

    for key in sorted(keys):
        alexa_item = alexa_items.get(key)
        ha_item = ha_items.get(key)
        item_state = stored_items.setdefault(key, {})

        if alexa_item is None and ha_item is None:
            stored_items.pop(key, None)
            continue

        new_alexa_occurrence, _new_ha_occurrence = reset_reused_item_state(
            item_state,
            alexa_item=alexa_item,
            ha_item=ha_item,
        )
        if (
            new_alexa_occurrence
            and alexa_item is not None
            and ha_item is not None
            and ha_item.status == STATUS_COMPLETED
        ):
            LOGGER.info(
                "Creating new occurrence of '%s' in %s because Alexa added it again",
                alexa_item.summary,
                ha_entity,
            )
            client.add_item(ha_entity, alexa_item)
            writes += 1
            ha_item = TodoItem(
                uid=alexa_item.summary,
                summary=alexa_item.summary,
                status=STATUS_NEEDS_ACTION,
                description=alexa_item.description,
            )
            ha_items[key] = ha_item

        if alexa_item is None and ha_item is not None:
            item_state.pop("ha_only_baseline", None)
            if (
                item_state.get("a_uid")
                and item_state.get("b_status") == STATUS_NEEDS_ACTION
                and ha_item.status == STATUS_NEEDS_ACTION
            ):
                if settings.get("allow_missing_completion", True):
                    missing_completion_candidates.append((item_state, ha_item))
                else:
                    LOGGER.info(
                        "Ignoring disappearance for '%s' in %s because multiple Alexa accounts share the target list",
                        ha_item.summary,
                        alexa_label,
                    )
                remember(item_state, alexa_item, ha_item)
                continue
            if ha_item.status == STATUS_NEEDS_ACTION:
                LOGGER.info("Creating '%s' in %s", ha_item.summary, alexa_label)
                alexa.add_item(ha_item)
                writes += 1
            remember(item_state, alexa_item, ha_item)
            continue

        if ha_item is None and alexa_item is not None:
            if (
                settings["sync_completed"]
                and item_state.get("b_uid")
                and (
                    item_state.get("b_status") == STATUS_NEEDS_ACTION
                    or item_state.get("pending_alexa_remove")
                )
            ):
                LOGGER.info(
                    "Detected deleted '%s' in %s; scheduling Alexa removal",
                    alexa_item.summary,
                    ha_entity,
                )
                item_state["pending_alexa_remove"] = True
                ha_deletion_candidates.append((item_state, alexa_item))
                remember(item_state, alexa_item, ha_item)
                continue
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
            if item_state.get("b_status") != STATUS_COMPLETED or item_state.get("pending_alexa_remove"):
                LOGGER.info("Removing completed '%s' from Alexa", ha_item.summary)
                item_state["pending_alexa_remove"] = True
                completed_alexa_items.append((item_state, alexa_item))
            else:
                LOGGER.debug("Skipping old completed '%s' for Alexa removal", ha_item.summary)

        remember(item_state, alexa_item, ha_item)

    if len(ha_deletion_candidates) > HA_DELETION_BULK_LIMIT:
        LOGGER.warning(
            "Skipping %s Home Assistant deletions for %s because the list read looks incomplete",
            len(ha_deletion_candidates),
            ha_entity,
        )
        for item_state, _alexa_item in ha_deletion_candidates:
            item_state.pop("pending_alexa_remove", None)
    else:
        completed_alexa_items.extend(ha_deletion_candidates)

    if len(missing_completion_candidates) > MISSING_COMPLETION_BULK_LIMIT:
        LOGGER.warning(
            "Skipping %s Alexa disappearance completions for %s because the list read looks incomplete",
            len(missing_completion_candidates),
            alexa_label,
        )
        for item_state, _ha_item in missing_completion_candidates:
            item_state["a_missing_count"] = 0
    else:
        for item_state, ha_item in missing_completion_candidates:
            missing_count = int(item_state.get("a_missing_count") or 0) + 1
            item_state["a_missing_count"] = missing_count
            if missing_count < MISSING_COMPLETION_CONFIRMATIONS:
                LOGGER.info(
                    "Delaying completion for '%s' in %s until Alexa disappearance is confirmed",
                    ha_item.summary,
                    ha_entity,
                )
                continue
            LOGGER.info(
                "Marking '%s' in %s as completed because it disappeared from %s",
                ha_item.summary,
                ha_entity,
                alexa_label,
            )
            client.update_status(ha_entity, ha_item.uid, STATUS_COMPLETED)
            ha_item.status = STATUS_COMPLETED
            writes += 1
            item_state["b_status"] = STATUS_COMPLETED
            item_state.pop("a_missing_count", None)

    if completed_alexa_items:
        if hasattr(alexa, "remove_items"):
            removed_count = alexa.remove_items([item for _item_state, item in completed_alexa_items])
            writes += removed_count
            for item_state, _item in completed_alexa_items[:removed_count]:
                item_state.pop("pending_alexa_remove", None)
        else:
            for item_state, alexa_item in completed_alexa_items:
                removed = alexa.remove_item(alexa_item)
                if removed is not False:
                    writes += 1
                    item_state.pop("pending_alexa_remove", None)

    state["updated_at"] = time.time()
    if save_after:
        save_state(state)

    if settings["remove_completed"]:
        LOGGER.info("Removing completed items from %s", ha_entity)
        client.remove_completed_items(ha_entity)
        writes += 1

    return writes
