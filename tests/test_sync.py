from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "alexa_sync"))

from ha_client import TodoItem  # noqa: E402
from settings import STATUS_COMPLETED, STATUS_NEEDS_ACTION  # noqa: E402
from sync import sync_alexa_items_with_ha, sync_internal_alexa_once  # noqa: E402


class FakeAlexa:
    def __init__(self, items: list[TodoItem]) -> None:
        self.items = items
        self.removed_batches: list[list[str]] = []
        self.remove_item_calls: list[str] = []

    def get_items(self) -> list[TodoItem]:
        return self.items

    def add_item(self, item: TodoItem) -> None:
        raise AssertionError(f"Unexpected Alexa add: {item.summary}")

    def remove_item(self, item: TodoItem) -> bool:
        self.remove_item_calls.append(item.summary)
        return True

    def remove_items(self, items: list[TodoItem]) -> int:
        self.removed_batches.append([item.summary for item in items])
        return len(items)


class FakeHomeAssistant:
    def __init__(self, items: list[TodoItem]) -> None:
        self.items = items

    def get_items(self, _entity_id: str) -> list[TodoItem]:
        return self.items

    def add_item(self, _entity_id: str, item: TodoItem) -> None:
        raise AssertionError(f"Unexpected HA add: {item.summary}")

    def update_status(self, _entity_id: str, item_uid: str, status: str) -> None:
        raise AssertionError(f"Unexpected HA status update: {item_uid} -> {status}")


class SyncCompletedRemovalTests(unittest.TestCase):
    def test_completed_alexa_items_are_removed_in_one_batch(self) -> None:
        alexa = FakeAlexa(
            [
                TodoItem(uid="a", summary="A", status=STATUS_NEEDS_ACTION),
                TodoItem(uid="b", summary="B", status=STATUS_NEEDS_ACTION),
            ]
        )
        ha = FakeHomeAssistant(
            [
                TodoItem(uid="ha-a", summary="A", status=STATUS_COMPLETED),
                TodoItem(uid="ha-b", summary="B", status=STATUS_COMPLETED),
            ]
        )
        settings = {"sync_completed": True, "remove_completed": False}

        writes = sync_alexa_items_with_ha(
            alexa,
            ha,
            "todo.einkauf",
            settings,
            {},
            alexa_label="Alexa",
            save_after=False,
        )

        self.assertEqual(writes, 2)
        self.assertEqual(alexa.removed_batches, [["A", "B"]])
        self.assertEqual(alexa.remove_item_calls, [])


class ContextAlexa(FakeAlexa):
    opened = 0

    def __init__(self, _domain: str, _cookie_path: object) -> None:
        super().__init__([])

    def __enter__(self) -> "ContextAlexa":
        type(self).opened += 1
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None

    def is_authenticated(self) -> bool:
        return True


class FailingChromiumAlexa:
    def __init__(self, _domain: str, _cookie_path: object) -> None:
        raise AssertionError("Chromium should not be constructed when HTTP sync succeeds")


class SyncClientSelectionTests(unittest.TestCase):
    def test_sync_uses_http_client_without_starting_chromium(self) -> None:
        ContextAlexa.opened = 0
        settings = {
            "amazon_accounts": [
                {
                    "id": "test@example.com",
                    "name": "Test",
                    "amazon_domain": "amazon.de",
                    "enabled": True,
                }
            ],
            "ha_list": "todo.einkauf",
            "remove_completed": False,
            "sync_completed": True,
        }
        state: dict[str, object] = {}

        with (
            patch("sync.HttpAlexaClient", ContextAlexa),
            patch("sync.InternalAlexaClient", FailingChromiumAlexa),
            patch("sync.save_state"),
        ):
            writes = sync_internal_alexa_once(FakeHomeAssistant([]), settings, state)

        self.assertEqual(writes, 0)
        self.assertEqual(ContextAlexa.opened, 1)


if __name__ == "__main__":
    unittest.main()
