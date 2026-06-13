from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "alexa_sync"))

from settings import normalize_settings  # noqa: E402


class SettingsNormalizationTests(unittest.TestCase):
    def test_legacy_ha_list_populates_ha_lists(self) -> None:
        settings = normalize_settings({"ha_list": "todo.einkauf"})

        self.assertEqual(settings["ha_list"], "todo.einkauf")
        self.assertEqual(settings["ha_lists"], ["todo.einkauf"])

    def test_ha_lists_are_deduplicated_and_drive_legacy_ha_list(self) -> None:
        settings = normalize_settings(
            {
                "ha_list": "todo.legacy",
                "ha_lists": ["todo.einkauf", "todo.einkauf", "todo.baumarkt"],
            }
        )

        self.assertEqual(settings["ha_list"], "todo.einkauf")
        self.assertEqual(settings["ha_lists"], ["todo.einkauf", "todo.baumarkt"])


if __name__ == "__main__":
    unittest.main()
