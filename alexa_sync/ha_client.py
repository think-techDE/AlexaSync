"""Home Assistant REST API client for the Alexa Sync add-on."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any
from urllib import error, request

from settings import API_BASE, STATUS_NEEDS_ACTION, STATUS_COMPLETED

LOGGER = logging.getLogger("alexa_sync")


@dataclass
class TodoItem:
    """Normalized Home Assistant to-do item."""

    uid: str
    summary: str
    status: str
    description: str | None = None


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
    normalized = summary.strip().casefold()
    replacements = {
        "\u00e4": "ae",
        "\u00f6": "oe",
        "\u00fc": "ue",
        "\u00df": "ss",
        "\u00c3\u00a4": "ae",
        "\u00c3\u00b6": "oe",
        "\u00c3\u00bc": "ue",
        "\u00c3\u009f": "ss",
        "\u00e3\u00a4": "ae",
        "\u00e3\u00b6": "oe",
        "\u00e3\u00bc": "ue",
        "\u00e3\u009f": "ss",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    normalized = re.sub(r"[^\w]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


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


class HomeAssistantClient:
    """Small Home Assistant REST API client."""

    def __init__(self) -> None:
        """Initialize client."""
        import os
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            raise RuntimeError("SUPERVISOR_TOKEN is not available")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def get_json(self, path: str) -> Any:
        """Read JSON from the Home Assistant REST API."""
        req = request.Request(f"{API_BASE}{path}", headers=self.headers, method="GET")
        try:
            with request.urlopen(req, timeout=30) as response:
                content = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Home Assistant API GET {path} failed: HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Home Assistant API unavailable: {exc}") from exc
        return json.loads(content) if content else None

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

        return json.loads(content) if content else None

    def list_todo_entities(self) -> list[dict[str, str]]:
        """Return available Home Assistant todo entities."""
        states = self.get_json("/states")
        if not isinstance(states, list):
            return []

        entities: list[dict[str, str]] = []
        for state in states:
            entity_id = str(state.get("entity_id", ""))
            if not entity_id.startswith("todo."):
                continue
            attributes = state.get("attributes") or {}
            name = str(attributes.get("friendly_name") or entity_id)
            entities.append({"entity_id": entity_id, "name": name})
        return sorted(entities, key=lambda item: item["name"].casefold())

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
