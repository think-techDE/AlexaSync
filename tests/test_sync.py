from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "alexa_sync"))

from ha_client import TodoItem  # noqa: E402
from settings import STATUS_COMPLETED, STATUS_NEEDS_ACTION  # noqa: E402
from sync import sync_alexa_items_with_ha  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
