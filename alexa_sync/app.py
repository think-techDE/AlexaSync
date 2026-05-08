"""Alexa Sync Home Assistant add-on."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import signal
import sys
import time
from typing import Any
from urllib import error, request

API_BASE = "http://supervisor/core/api"
OPTIONS_PATH = Path("/data/options.json")
STATE_PATH = Path("/data/sync_state.json")

STATUS_NEEDS_ACTION = "needs_action"
STATUS_COMPLETED = "completed"

LOGGER = logging.getLogger("alexa_sync")
STOP_REQUESTED = False


@dataclass
class TodoItem:
    """Normalized Home Assistant to-do item."""

    uid: str
    summary: str
    status: str
    description: str | None = None


def handle_stop(_signum: int, _frame: Any) -> None:
    """Request clean shutdown."""
    global STOP_REQUESTED
    STOP_REQUESTED = True


def load_options() -> dict[str, Any]:
    """Load add-on options."""
    with OPTIONS_PATH.open("r", encoding="utf-8") as options_file:
        options = json.load(options_file)

    missing = [key for key in ("list_a", "list_b") if not str(options.get(key, "")).strip()]
    if missing:
        raise ValueError(f"Missing required add-on option(s): {', '.join(missing)}")
    if options["list_a"] == options["list_b"]:
        raise ValueError("list_a and list_b must be different entities")

    return {
        "list_a": options["list_a"].strip(),
        "list_b": options["list_b"].strip(),
        "interval_seconds": int(options.get("interval_seconds", 60)),
        "sync_completed": bool(options.get("sync_completed", True)),
        "remove_completed": bool(options.get("remove_completed", False)),
        "log_level": str(options.get("log_level", "info")).upper(),
    }


def setup_logging(level: str) -> None:
    """Configure logging."""
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_state() -> dict[str, Any]:
    """Load persistent sync state."""
    if not STATE_PATH.exists():
        return {"items": {}}
    with STATE_PATH.open("r", encoding="utf-8") as state_file:
        return json.load(state_file)


def save_state(state: dict[str, Any]) -> None:
    """Persist sync state."""
    tmp_path = STATE_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as state_file:
        json.dump(state, state_file, ensure_ascii=True, indent=2, sort_keys=True)
    tmp_path.replace(STATE_PATH)


class HomeAssistantClient:
    """Small Home Assistant REST API client."""

    def __init__(self) -> None:
        """Initialize client."""
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            raise RuntimeError("SUPERVISOR_TOKEN is not available")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def call_service(
        self,
        domain: str,
        service: str,
        payload: dict[str, Any],
        *,
        return_response: bool = False,
    ) -> Any:
        """Call a Home Assistant service."""
        suffix = "?return_response" if return_response else ""
        url = f"{API_BASE}/services/{domain}/{service}{suffix}"
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=body, headers=self.headers, method="POST")
        try:
            with request.urlopen(req, timeout=30) as response:
                content = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Home Assistant service call failed: {domain}.{service} "
                f"HTTP {exc.code}: {detail}"
            ) from exc
        except error.URLError as exc:
            raise RuntimeError(f"Home Assistant API unavailable: {exc}") from exc

        if not content:
            return None
        return json.loads(content)

    def get_items(self, entity_id: str) -> list[TodoItem]:
        """Return all items from a to-do entity."""
        response = self.call_service(
            "todo",
            "get_items",
            {
                "entity_id": entity_id,
                "status": [STATUS_NEEDS_ACTION, STATUS_COMPLETED],
            },
            return_response=True,
        )
        raw_items = extract_items(response, entity_id)
        items: list[TodoItem] = []
        for raw in raw_items:
            summary = str(raw.get("summary") or raw.get("item") or "").strip()
            uid = str(raw.get("uid") or raw.get("id") or summary).strip()
            if not summary or not uid:
                continue
            items.append(
                TodoItem(
                    uid=uid,
                    summary=summary,
                    status=normalize_status(raw.get("status")),
                    description=raw.get("description") or None,
                )
            )
        return items

    def add_item(self, entity_id: str, item: TodoItem) -> None:
        """Add an item to a to-do list."""
        payload: dict[str, Any] = {"entity_id": entity_id, "item": item.summary}
        if item.description:
            payload["description"] = item.description
        self.call_service("todo", "add_item", payload)

    def update_status(self, entity_id: str, uid: str, status: str) -> None:
        """Update an item status in a to-do list."""
        self.call_service(
            "todo",
            "update_item",
            {"entity_id": entity_id, "item": uid, "status": status},
        )

    def remove_completed_items(self, entity_id: str) -> None:
        """Remove completed items from a to-do list."""
        self.call_service("todo", "remove_completed_items", {"entity_id": entity_id})


def extract_items(response: Any, entity_id: str) -> list[dict[str, Any]]:
    """Extract to-do items from REST service response variants."""
    if not isinstance(response, dict):
        return []

    service_response = response.get("service_response")
    if isinstance(service_response, dict):
        entity_response = service_response.get(entity_id)
        if isinstance(entity_response, dict) and isinstance(entity_response.get("items"), list):
            return entity_response["items"]
        for value in service_response.values():
            if isinstance(value, dict) and isinstance(value.get("items"), list):
                return value["items"]

    entity_response = response.get(entity_id)
    if isinstance(entity_response, dict) and isinstance(entity_response.get("items"), list):
        return entity_response["items"]

    if isinstance(response.get("items"), list):
        return response["items"]

    return []


def normalize_summary(summary: str) -> str:
    """Normalize item names for matching."""
    return re.sub(r"\s+", " ", summary.strip().casefold())


def normalize_status(status: Any) -> str:
    """Normalize Home Assistant to-do statuses."""
    raw = str(status or STATUS_NEEDS_ACTION).lower()
    if raw in {"complete", "completed", STATUS_COMPLETED}:
        return STATUS_COMPLETED
    return STATUS_NEEDS_ACTION


def index_items(items: list[TodoItem]) -> dict[str, TodoItem]:
    """Index items by normalized summary."""
    indexed: dict[str, TodoItem] = {}
    for item in items:
        key = normalize_summary(item.summary)
        if key and key not in indexed:
            indexed[key] = item
    return indexed


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


def sync_once(client: HomeAssistantClient, options: dict[str, Any], state: dict[str, Any]) -> int:
    """Run one synchronization pass. Returns number of write operations."""
    list_a = options["list_a"]
    list_b = options["list_b"]
    items_a = index_items(client.get_items(list_a))
    items_b = index_items(client.get_items(list_b))
    stored_items = state.setdefault("items", {})
    keys = set(items_a) | set(items_b) | set(stored_items)
    writes = 0

    for key in sorted(keys):
        item_a = items_a.get(key)
        item_b = items_b.get(key)
        item_state = stored_items.setdefault(key, {})

        if item_a is None and item_b is None:
            stored_items.pop(key, None)
            continue

        if item_a is None:
            if item_b and item_b.status == STATUS_NEEDS_ACTION:
                LOGGER.info("Creating '%s' in %s", item_b.summary, list_a)
                client.add_item(list_a, item_b)
                writes += 1
            remember(item_state, item_a, item_b)
            continue

        if item_b is None:
            if item_a.status == STATUS_NEEDS_ACTION:
                LOGGER.info("Creating '%s' in %s", item_a.summary, list_b)
                client.add_item(list_b, item_a)
                writes += 1
            remember(item_state, item_a, item_b)
            continue

        target_status = resolve_status(item_a, item_b, item_state)
        if target_status and options["sync_completed"]:
            if item_a.status != target_status:
                LOGGER.info("Setting '%s' in %s to %s", item_a.summary, list_a, target_status)
                client.update_status(list_a, item_a.uid, target_status)
                item_a.status = target_status
                writes += 1
            if item_b.status != target_status:
                LOGGER.info("Setting '%s' in %s to %s", item_b.summary, list_b, target_status)
                client.update_status(list_b, item_b.uid, target_status)
                item_b.status = target_status
                writes += 1

        remember(item_state, item_a, item_b)

    state["updated_at"] = time.time()
    save_state(state)

    if options["remove_completed"]:
        LOGGER.info("Removing completed items from both lists")
        client.remove_completed_items(list_a)
        client.remove_completed_items(list_b)
        writes += 2

    return writes


def main() -> int:
    """Run add-on loop."""
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    options = load_options()
    setup_logging(options["log_level"])
    state = load_state()
    client = HomeAssistantClient()

    LOGGER.info(
        "Starting sync between %s and %s every %ss",
        options["list_a"],
        options["list_b"],
        options["interval_seconds"],
    )

    while not STOP_REQUESTED:
        try:
            writes = sync_once(client, options, state)
            LOGGER.debug("Sync pass completed with %s write operation(s)", writes)
        except Exception:
            LOGGER.exception("Sync pass failed")

        sleep_until = time.monotonic() + options["interval_seconds"]
        while not STOP_REQUESTED and time.monotonic() < sleep_until:
            time.sleep(min(1, sleep_until - time.monotonic()))

    LOGGER.info("Stopping Alexa Sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
