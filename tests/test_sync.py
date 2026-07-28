from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "alexa_sync"))

from ha_client import TodoItem, index_items  # noqa: E402
from settings import STATUS_COMPLETED, STATUS_NEEDS_ACTION  # noqa: E402
from sync import sync_alexa_items_with_ha, sync_ha_targets, sync_internal_alexa_once  # noqa: E402


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


class RecordingAlexa(FakeAlexa):
    def __init__(self, items: list[TodoItem]) -> None:
        super().__init__(items)
        self.added: list[str] = []

    def add_item(self, item: TodoItem) -> None:
        self.added.append(item.summary)
        self.items.append(
            TodoItem(
                uid=f"alexa-{len(self.items) + 1}",
                summary=item.summary,
                status=STATUS_NEEDS_ACTION,
                description=item.description,
            )
        )


class FakeHomeAssistant:
    def __init__(self, items: list[TodoItem]) -> None:
        self.items = items

    def get_items(self, _entity_id: str) -> list[TodoItem]:
        return self.items

    def add_item(self, _entity_id: str, item: TodoItem) -> None:
        raise AssertionError(f"Unexpected HA add: {item.summary}")

    def update_status(self, _entity_id: str, item_uid: str, status: str) -> None:
        raise AssertionError(f"Unexpected HA status update: {item_uid} -> {status}")


class UpdatingHomeAssistant(FakeHomeAssistant):
    def __init__(self, items: list[TodoItem]) -> None:
        super().__init__(items)
        self.status_updates: list[tuple[str, str]] = []

    def update_status(self, _entity_id: str, item_uid: str, status: str) -> None:
        self.status_updates.append((item_uid, status))


class RecordingHomeAssistant:
    def __init__(self, items_by_entity: dict[str, list[TodoItem]]) -> None:
        self.items_by_entity = items_by_entity
        self.added: list[tuple[str, str]] = []
        self.removed_completed: list[str] = []
        self.status_updates: list[tuple[str, str, str]] = []

    def get_items(self, entity_id: str) -> list[TodoItem]:
        return self.items_by_entity.get(entity_id, [])

    def add_item(self, entity_id: str, item: TodoItem) -> None:
        self.added.append((entity_id, item.summary))
        self.items_by_entity.setdefault(entity_id, []).append(
            TodoItem(
                uid=item.summary,
                summary=item.summary,
                status=STATUS_NEEDS_ACTION,
                description=item.description,
            )
        )

    def update_status(self, entity_id: str, item_uid: str, status: str) -> None:
        self.status_updates.append((entity_id, item_uid, status))
        for item in self.items_by_entity.get(entity_id, []):
            if item.uid == item_uid:
                item.status = status
                return

    def remove_completed_items(self, entity_id: str) -> None:
        self.removed_completed.append(entity_id)


def ha_targets_state(
    items_by_entity: dict[str, list[TodoItem]],
) -> dict[str, object]:
    return {
        "ha_targets": {
            "initialized": True,
            "targets": {
                entity_id: {
                    "items": {
                        item.summary.casefold(): {
                            "uid": item.uid,
                            "status": item.status,
                            "summary": item.summary,
                        }
                        for item in items
                    }
                }
                for entity_id, items in items_by_entity.items()
            },
        }
    }


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

    def test_old_completed_ha_item_is_not_removed_from_alexa_again(self) -> None:
        alexa = FakeAlexa([TodoItem(uid="a", summary="A", status=STATUS_NEEDS_ACTION)])
        ha = FakeHomeAssistant([TodoItem(uid="ha-a", summary="A", status=STATUS_COMPLETED)])
        settings = {"sync_completed": True, "remove_completed": False}
        state = {
            "items": {
                "a": {
                    "a_uid": "a",
                    "a_status": STATUS_NEEDS_ACTION,
                    "b_uid": "ha-a",
                    "b_status": STATUS_COMPLETED,
                }
            }
        }

        writes = sync_alexa_items_with_ha(
            alexa,
            ha,
            "todo.einkauf",
            settings,
            state,
            alexa_label="Alexa",
            save_after=False,
        )

        self.assertEqual(writes, 0)
        self.assertEqual(alexa.removed_batches, [])

    def test_new_completed_ha_item_is_removed_from_alexa(self) -> None:
        alexa = FakeAlexa([TodoItem(uid="a", summary="A", status=STATUS_NEEDS_ACTION)])
        ha = FakeHomeAssistant([TodoItem(uid="ha-a", summary="A", status=STATUS_COMPLETED)])
        settings = {"sync_completed": True, "remove_completed": False}
        state = {
            "items": {
                "a": {
                    "a_uid": "a",
                    "a_status": STATUS_NEEDS_ACTION,
                    "b_uid": "ha-a",
                    "b_status": STATUS_NEEDS_ACTION,
                }
            }
        }

        writes = sync_alexa_items_with_ha(
            alexa,
            ha,
            "todo.einkauf",
            settings,
            state,
            alexa_label="Alexa",
            save_after=False,
        )

        self.assertEqual(writes, 1)
        self.assertEqual(alexa.removed_batches, [["A"]])
        self.assertNotIn("pending_alexa_remove", state["items"]["a"])

    def test_deleted_known_ha_item_is_removed_from_alexa(self) -> None:
        alexa = FakeAlexa([TodoItem(uid="a", summary="A", status=STATUS_NEEDS_ACTION)])
        ha = FakeHomeAssistant([])
        settings = {"sync_completed": True, "remove_completed": False}
        state = {
            "items": {
                "a": {
                    "a_uid": "a",
                    "a_status": STATUS_NEEDS_ACTION,
                    "b_uid": "ha-a",
                    "b_status": STATUS_NEEDS_ACTION,
                }
            }
        }

        writes = sync_alexa_items_with_ha(
            alexa,
            ha,
            "todo.enaro",
            settings,
            state,
            alexa_label="Alexa",
            save_after=False,
        )

        self.assertEqual(writes, 1)
        self.assertEqual(alexa.removed_batches, [["A"]])
        self.assertNotIn("pending_alexa_remove", state["items"]["a"])

    def test_bulk_deleted_ha_items_are_not_removed_from_alexa(self) -> None:
        alexa = FakeAlexa(
            [
                TodoItem(uid=f"a-{index}", summary=f"Item {index}", status=STATUS_NEEDS_ACTION)
                for index in range(6)
            ]
        )
        ha = FakeHomeAssistant([])
        settings = {"sync_completed": True, "remove_completed": False}
        state = {
            "items": {
                f"item {index}": {
                    "a_uid": f"a-{index}",
                    "a_status": STATUS_NEEDS_ACTION,
                    "b_uid": f"ha-{index}",
                    "b_status": STATUS_NEEDS_ACTION,
                }
                for index in range(6)
            }
        }

        writes = sync_alexa_items_with_ha(
            alexa,
            ha,
            "todo.enaro",
            settings,
            state,
            alexa_label="Alexa",
            save_after=False,
        )

        self.assertEqual(writes, 0)
        self.assertEqual(alexa.removed_batches, [])


class ItemIndexTests(unittest.TestCase):
    def test_active_duplicate_wins_over_completed_item_with_same_name(self) -> None:
        indexed = index_items(
            [
                TodoItem(uid="old", summary="Feta", status=STATUS_COMPLETED),
                TodoItem(uid="new", summary="feta", status=STATUS_NEEDS_ACTION),
            ]
        )

        self.assertEqual(indexed["feta"].uid, "new")
        self.assertEqual(indexed["feta"].status, STATUS_NEEDS_ACTION)


class SyncHomeAssistantTargetsTests(unittest.TestCase):
    def test_completed_item_in_bring_is_completed_in_enaro(self) -> None:
        previous = {
            "todo.bring": [TodoItem(uid="bring-a", summary="A", status=STATUS_NEEDS_ACTION)],
            "todo.enaro": [TodoItem(uid="enaro-a", summary="A", status=STATUS_NEEDS_ACTION)],
        }
        state = ha_targets_state(previous)
        ha = RecordingHomeAssistant(
            {
                "todo.bring": [TodoItem(uid="bring-a", summary="A", status=STATUS_COMPLETED)],
                "todo.enaro": [TodoItem(uid="enaro-a", summary="A", status=STATUS_NEEDS_ACTION)],
            }
        )

        writes = sync_ha_targets(
            ha,
            {
                "ha_lists": ["todo.bring", "todo.enaro"],
                "sync_completed": True,
            },
            state,
        )

        self.assertEqual(writes, 1)
        self.assertEqual(ha.status_updates, [("todo.enaro", "enaro-a", STATUS_COMPLETED)])
        self.assertEqual(ha.added, [])

    def test_deleted_item_in_bring_is_completed_in_enaro(self) -> None:
        previous = {
            "todo.bring": [TodoItem(uid="bring-a", summary="A", status=STATUS_NEEDS_ACTION)],
            "todo.enaro": [TodoItem(uid="enaro-a", summary="A", status=STATUS_NEEDS_ACTION)],
        }
        state = ha_targets_state(previous)
        ha = RecordingHomeAssistant(
            {
                "todo.bring": [],
                "todo.enaro": [TodoItem(uid="enaro-a", summary="A", status=STATUS_NEEDS_ACTION)],
            }
        )

        writes = sync_ha_targets(
            ha,
            {
                "ha_lists": ["todo.bring", "todo.enaro"],
                "sync_completed": True,
            },
            state,
        )

        self.assertEqual(writes, 1)
        self.assertEqual(ha.status_updates, [("todo.enaro", "enaro-a", STATUS_COMPLETED)])
        self.assertEqual(ha.added, [])

    def test_new_item_in_enaro_is_created_in_bring(self) -> None:
        state = ha_targets_state({"todo.bring": [], "todo.enaro": []})
        ha = RecordingHomeAssistant(
            {
                "todo.bring": [],
                "todo.enaro": [TodoItem(uid="enaro-a", summary="A", status=STATUS_NEEDS_ACTION)],
            }
        )

        writes = sync_ha_targets(
            ha,
            {
                "ha_lists": ["todo.bring", "todo.enaro"],
                "sync_completed": True,
            },
            state,
        )

        self.assertEqual(writes, 1)
        self.assertEqual(ha.added, [("todo.bring", "A")])
        self.assertEqual(ha.status_updates, [])

    def test_reused_name_with_new_uid_is_not_completed_by_old_missing_item(self) -> None:
        previous = {
            "todo.bring": [
                TodoItem(uid="bring-old", summary="Feta", status=STATUS_NEEDS_ACTION)
            ],
            "todo.enaro": [
                TodoItem(uid="enaro-old", summary="Feta", status=STATUS_NEEDS_ACTION)
            ],
        }
        state = ha_targets_state(previous)
        ha = RecordingHomeAssistant(
            {
                "todo.bring": [],
                "todo.enaro": [
                    TodoItem(uid="enaro-new", summary="Feta", status=STATUS_NEEDS_ACTION)
                ],
            }
        )

        writes = sync_ha_targets(
            ha,
            {
                "ha_lists": ["todo.bring", "todo.enaro"],
                "sync_completed": True,
            },
            state,
        )

        self.assertEqual(writes, 1)
        self.assertEqual(ha.added, [("todo.bring", "Feta")])
        self.assertEqual(ha.status_updates, [])

    def test_new_active_occurrence_wins_over_old_completed_target_item(self) -> None:
        previous = {
            "todo.bring": [
                TodoItem(uid="bring-old", summary="Minze", status=STATUS_COMPLETED)
            ],
            "todo.enaro": [
                TodoItem(uid="enaro-old", summary="Minze", status=STATUS_COMPLETED)
            ],
        }
        state = ha_targets_state(previous)
        ha = RecordingHomeAssistant(
            {
                "todo.bring": [
                    TodoItem(uid="bring-old", summary="Minze", status=STATUS_COMPLETED)
                ],
                "todo.enaro": [
                    TodoItem(uid="enaro-new", summary="Minze", status=STATUS_NEEDS_ACTION)
                ],
            }
        )

        writes = sync_ha_targets(
            ha,
            {
                "ha_lists": ["todo.bring", "todo.enaro"],
                "sync_completed": True,
            },
            state,
        )

        self.assertEqual(writes, 1)
        self.assertEqual(ha.added, [("todo.bring", "Minze")])
        self.assertEqual(ha.status_updates, [])

    def test_completion_wins_over_recreating_deleted_item(self) -> None:
        previous = {
            "todo.bring": [TodoItem(uid="bring-a", summary="A", status=STATUS_NEEDS_ACTION)],
            "todo.enaro": [TodoItem(uid="enaro-a", summary="A", status=STATUS_NEEDS_ACTION)],
        }
        state = ha_targets_state(previous)
        ha = RecordingHomeAssistant(
            {
                "todo.bring": [],
                "todo.enaro": [TodoItem(uid="enaro-a", summary="A", status=STATUS_NEEDS_ACTION)],
            }
        )

        writes = sync_ha_targets(
            ha,
            {
                "ha_lists": ["todo.bring", "todo.enaro"],
                "sync_completed": True,
            },
            state,
        )

        self.assertEqual(writes, 1)
        self.assertEqual(ha.status_updates, [("todo.enaro", "enaro-a", STATUS_COMPLETED)])
        self.assertEqual(ha.added, [])

    def test_bulk_missing_target_does_not_complete_other_targets(self) -> None:
        previous = {
            "todo.bring": [
                TodoItem(uid=f"bring-{index}", summary=f"Item {index}", status=STATUS_NEEDS_ACTION)
                for index in range(6)
            ],
            "todo.enaro": [
                TodoItem(uid=f"enaro-{index}", summary=f"Item {index}", status=STATUS_NEEDS_ACTION)
                for index in range(6)
            ],
        }
        state = ha_targets_state(previous)
        ha = RecordingHomeAssistant(
            {
                "todo.bring": [],
                "todo.enaro": [
                    TodoItem(uid=f"enaro-{index}", summary=f"Item {index}", status=STATUS_NEEDS_ACTION)
                    for index in range(6)
                ],
            }
        )

        writes = sync_ha_targets(
            ha,
            {
                "ha_lists": ["todo.bring", "todo.enaro"],
                "sync_completed": True,
            },
            state,
        )

        self.assertEqual(writes, 0)
        self.assertEqual(ha.status_updates, [])
        self.assertEqual(ha.added, [])


class SyncAlexaDisappearanceTests(unittest.TestCase):
    def test_missing_alexa_item_must_be_confirmed_before_completion(self) -> None:
        alexa = FakeAlexa([])
        ha = UpdatingHomeAssistant([TodoItem(uid="ha-a", summary="A", status=STATUS_NEEDS_ACTION)])
        settings = {"sync_completed": True, "remove_completed": False}
        state = {
            "items": {
                "a": {
                    "a_uid": "a",
                    "a_status": STATUS_NEEDS_ACTION,
                    "b_uid": "ha-a",
                    "b_status": STATUS_NEEDS_ACTION,
                }
            }
        }

        first_writes = sync_alexa_items_with_ha(
            alexa,
            ha,
            "todo.einkauf",
            settings,
            state,
            alexa_label="Alexa",
            save_after=False,
        )
        second_writes = sync_alexa_items_with_ha(
            alexa,
            ha,
            "todo.einkauf",
            settings,
            state,
            alexa_label="Alexa",
            save_after=False,
        )

        self.assertEqual(first_writes, 0)
        self.assertEqual(second_writes, 1)
        self.assertEqual(ha.status_updates, [("ha-a", STATUS_COMPLETED)])

    def test_reused_ha_name_with_new_uid_is_added_to_alexa_not_completed(self) -> None:
        alexa = RecordingAlexa([])
        ha = UpdatingHomeAssistant(
            [TodoItem(uid="ha-new", summary="Salatmischung", status=STATUS_NEEDS_ACTION)]
        )
        settings = {"sync_completed": True, "remove_completed": False}
        state = {
            "items": {
                "salatmischung": {
                    "a_uid": "alexa-old",
                    "a_status": STATUS_NEEDS_ACTION,
                    "b_uid": "ha-old",
                    "b_status": STATUS_NEEDS_ACTION,
                    "a_missing_count": 1,
                }
            }
        }

        writes = sync_alexa_items_with_ha(
            alexa,
            ha,
            "todo.enaro",
            settings,
            state,
            alexa_label="Alexa",
            save_after=False,
        )

        self.assertEqual(writes, 1)
        self.assertEqual(alexa.added, ["Salatmischung"])
        self.assertEqual(ha.status_updates, [])
        self.assertNotIn("a_missing_count", state["items"]["salatmischung"])

    def test_bulk_disappearances_are_not_completed(self) -> None:
        items = [
            TodoItem(uid=f"ha-{index}", summary=f"Item {index}", status=STATUS_NEEDS_ACTION)
            for index in range(6)
        ]
        alexa = FakeAlexa([])
        ha = UpdatingHomeAssistant(items)
        settings = {"sync_completed": True, "remove_completed": False}
        state = {
            "items": {
                f"item {index}": {
                    "a_uid": f"alexa-{index}",
                    "a_status": STATUS_NEEDS_ACTION,
                    "b_uid": f"ha-{index}",
                    "b_status": STATUS_NEEDS_ACTION,
                }
                for index in range(6)
            }
        }

        writes = sync_alexa_items_with_ha(
            alexa,
            ha,
            "todo.einkauf",
            settings,
            state,
            alexa_label="Alexa",
            save_after=False,
        )

        self.assertEqual(writes, 0)
        self.assertEqual(ha.status_updates, [])

    def test_multi_account_disappearance_does_not_complete_or_recreate(self) -> None:
        alexa = FakeAlexa([])
        ha = UpdatingHomeAssistant([TodoItem(uid="ha-a", summary="A", status=STATUS_NEEDS_ACTION)])
        settings = {
            "sync_completed": True,
            "remove_completed": False,
            "allow_missing_completion": False,
        }
        state = {
            "items": {
                "a": {
                    "a_uid": "a",
                    "a_status": STATUS_NEEDS_ACTION,
                    "b_uid": "ha-a",
                    "b_status": STATUS_NEEDS_ACTION,
                }
            }
        }

        writes = sync_alexa_items_with_ha(
            alexa,
            ha,
            "todo.einkauf",
            settings,
            state,
            alexa_label="Alexa",
            save_after=False,
        )

        self.assertEqual(writes, 0)
        self.assertEqual(ha.status_updates, [])


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


class ContextAlexaWithItems(ContextAlexa):
    items_template: list[TodoItem] = []

    def __init__(self, _domain: str, _cookie_path: object) -> None:
        super().__init__(_domain, _cookie_path)
        self.items = list(type(self).items_template)


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

    def test_sync_writes_alexa_items_to_all_configured_targets(self) -> None:
        ContextAlexaWithItems.opened = 0
        ContextAlexaWithItems.items_template = [
            TodoItem(uid="a-milk", summary="Milk", status=STATUS_NEEDS_ACTION)
        ]
        settings = {
            "amazon_accounts": [
                {
                    "id": "acc",
                    "name": "Test",
                    "amazon_domain": "amazon.de",
                    "enabled": True,
                }
            ],
            "ha_list": "todo.kitchen",
            "ha_lists": ["todo.kitchen", "todo.office"],
            "remove_completed": False,
            "sync_completed": True,
        }
        state: dict[str, object] = {}
        ha = RecordingHomeAssistant({"todo.kitchen": [], "todo.office": []})

        with (
            patch("sync.HttpAlexaClient", ContextAlexaWithItems),
            patch("sync.InternalAlexaClient", FailingChromiumAlexa),
            patch("sync.save_state"),
        ):
            writes = sync_internal_alexa_once(ha, settings, state)

        self.assertEqual(writes, 2)
        self.assertEqual(ha.added, [("todo.kitchen", "Milk"), ("todo.office", "Milk")])
        account_state = state["amazon_accounts"]["acc"]  # type: ignore[index]
        self.assertIn("todo.kitchen", account_state["targets"])
        self.assertIn("todo.office", account_state["targets"])

    def test_legacy_account_items_migrate_to_first_target_state(self) -> None:
        ContextAlexaWithItems.opened = 0
        ContextAlexaWithItems.items_template = []
        settings = {
            "amazon_accounts": [
                {
                    "id": "acc",
                    "name": "Test",
                    "amazon_domain": "amazon.de",
                    "enabled": True,
                }
            ],
            "ha_list": "todo.kitchen",
            "ha_lists": ["todo.kitchen", "todo.office"],
            "remove_completed": False,
            "sync_completed": True,
        }
        state: dict[str, object] = {
            "amazon_accounts": {
                "acc": {
                    "items": {
                        "milk": {
                            "a_uid": "a-milk",
                            "a_status": STATUS_NEEDS_ACTION,
                            "b_uid": "ha-milk",
                            "b_status": STATUS_NEEDS_ACTION,
                        }
                    }
                }
            }
        }
        ha = RecordingHomeAssistant(
            {
                "todo.kitchen": [
                    TodoItem(uid="ha-milk", summary="Milk", status=STATUS_NEEDS_ACTION)
                ],
                "todo.office": [],
            }
        )

        with (
            patch("sync.HttpAlexaClient", ContextAlexaWithItems),
            patch("sync.InternalAlexaClient", FailingChromiumAlexa),
            patch("sync.save_state"),
        ):
            writes = sync_internal_alexa_once(ha, settings, state)

        self.assertEqual(writes, 0)
        account_state = state["amazon_accounts"]["acc"]  # type: ignore[index]
        self.assertNotIn("items", account_state)
        self.assertIn("milk", account_state["targets"]["todo.kitchen"]["items"])


if __name__ == "__main__":
    unittest.main()
