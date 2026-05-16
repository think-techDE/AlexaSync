from __future__ import annotations

import sys
import time
import unittest
import pickle
import json
import tempfile
from http.cookies import Morsel, SimpleCookie
from http.cookiejar import Cookie
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "alexa_sync"))

from alexa_client import (  # noqa: E402
    HttpAlexaClient,
    InternalAlexaClient,
    cookie_header_from_cookie_list,
    cookie_matches_host,
    extract_alexa_media_cookies,
    import_selected_alexa_media_sessions,
    load_alexa_media_cookie_pickle,
)
from ha_client import TodoItem  # noqa: E402


def by_name(cookies: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(cookie["name"]): cookie for cookie in cookies}


class AlexaMediaCookieExtractionTests(unittest.TestCase):
    def test_extracts_plain_alexapy_cookie_mapping(self) -> None:
        cookies = extract_alexa_media_cookies(
            {
                "session-id": "session-value",
                "at-acbde": "auth-token",
            },
            "amazon.de",
        )

        indexed = by_name(cookies)
        self.assertEqual(indexed["session-id"]["value"], "session-value")
        self.assertEqual(indexed["session-id"]["domain"], ".amazon.de")
        self.assertEqual(indexed["at-acbde"]["domain"], ".amazon.de")

    def test_extracts_nested_aiohttp_cookie_storage(self) -> None:
        simple_cookie = SimpleCookie()
        simple_cookie["session-id"] = "session-value"
        simple_cookie["session-id"]["domain"] = ".amazon.de"
        simple_cookie["session-id"]["path"] = "/"
        simple_cookie["session-id"]["httponly"] = True
        simple_cookie["session-id"]["secure"] = True

        cookies = extract_alexa_media_cookies({(".amazon.de", "/"): simple_cookie}, "amazon.de")

        indexed = by_name(cookies)
        self.assertEqual(indexed["session-id"]["value"], "session-value")
        self.assertTrue(indexed["session-id"]["httpOnly"])
        self.assertTrue(indexed["session-id"]["secure"])

    def test_extracts_http_cookiejar_cookie(self) -> None:
        raw_cookie = Cookie(
            version=0,
            name="ubid-acbde",
            value="ubid-value",
            port=None,
            port_specified=False,
            domain=".amazon.de",
            domain_specified=True,
            domain_initial_dot=True,
            path="/",
            path_specified=True,
            secure=True,
            expires=int(time.time()) + 3600,
            discard=False,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": None, "SameSite": "Lax"},
            rfc2109=False,
        )

        cookies = extract_alexa_media_cookies([raw_cookie], "amazon.de")

        indexed = by_name(cookies)
        self.assertEqual(indexed["ubid-acbde"]["value"], "ubid-value")
        self.assertTrue(indexed["ubid-acbde"]["httpOnly"])
        self.assertEqual(indexed["ubid-acbde"]["sameSite"], "Lax")

    def test_loads_cookie_pickle_with_partitioned_attribute(self) -> None:
        original_reserved = dict(Morsel._reserved)
        original_flags = set(Morsel._flags)
        try:
            Morsel._reserved["partitioned"] = "Partitioned"
            Morsel._flags.add("partitioned")
            simple_cookie = SimpleCookie()
            simple_cookie["session-id"] = "session-value"
            simple_cookie["session-id"]["domain"] = ".amazon.de"
            simple_cookie["session-id"]["partitioned"] = True
            payload = pickle.dumps(simple_cookie)

            Morsel._reserved.clear()
            Morsel._reserved.update(original_reserved)
            Morsel._flags.clear()
            Morsel._flags.update(original_flags)

            with tempfile.TemporaryDirectory() as tmp_dir:
                path = Path(tmp_dir) / "alexa_media.test@example.com.pickle"
                path.write_bytes(payload)
                loaded = load_alexa_media_cookie_pickle(path)

            cookies = extract_alexa_media_cookies(loaded, "amazon.de")
            indexed = by_name(cookies)
            self.assertEqual(indexed["session-id"]["value"], "session-value")
        finally:
            Morsel._reserved.clear()
            Morsel._reserved.update(original_reserved)
            Morsel._flags.clear()
            Morsel._flags.update(original_flags)

    def test_selected_session_import_replaces_existing_accounts(self) -> None:
        settings = {
            "amazon_domain": "amazon.de",
            "amazon_accounts": [
                {
                    "id": "marina",
                    "name": "Marina",
                    "amazon_domain": "amazon.de",
                    "enabled": True,
                },
                {
                    "id": "ben@think-tech.eu",
                    "name": "ben@think-tech.eu",
                    "amazon_domain": "amazon.de",
                    "enabled": True,
                },
            ],
            "ha_list": "todo.einkauf",
        }
        session_files = [
            Path("alexa_media.ben@think-tech.eu.pickle"),
            Path("alexa_media.mary_mausi025@web.de.pickle"),
        ]

        with (
            patch("alexa_client.find_alexa_media_session_files", return_value=session_files),
            patch("alexa_client.load_alexa_media_cookie_pickle", return_value={"session-id": "session-value"}),
            patch("alexa_client.save_cookie_list"),
            patch("settings.write_json_file"),
        ):
            result = import_selected_alexa_media_sessions(
                settings,
                [path.name for path in session_files],
            )

        account_ids = [account["id"] for account in result["settings"]["amazon_accounts"]]
        self.assertEqual(account_ids, ["ben@think-tech.eu", "mary_mausi025@web.de"])


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.headers: dict[str, str] = {}

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class HttpAlexaClientTests(unittest.TestCase):
    def test_cookie_header_skips_expired_cookies(self) -> None:
        cookies = [
            {"name": "session-id", "value": "session-value", "expiry": int(time.time()) + 60},
            {"name": "old", "value": "old-value", "expiry": int(time.time()) - 60},
        ]

        header = cookie_header_from_cookie_list(cookies)

        self.assertIn("session-id=session-value", header)
        self.assertNotIn("old=", header)

    def test_cookie_header_filters_by_request_host(self) -> None:
        cookies = [
            {"name": "session-id", "value": "root", "domain": ".amazon.de"},
            {"name": "ubid-main", "value": "www", "domain": "www.amazon.de"},
            {"name": "csrf", "value": "alexa", "domain": "alexa.amazon.de"},
        ]

        self.assertTrue(cookie_matches_host(cookies[0], "www.amazon.de"))
        self.assertFalse(cookie_matches_host(cookies[2], "www.amazon.de"))
        header = cookie_header_from_cookie_list(cookies, "www.amazon.de")

        self.assertIn("session-id=root", header)
        self.assertIn("ubid-main=www", header)
        self.assertNotIn("csrf=alexa", header)

    def test_http_client_reads_adds_and_completes_items(self) -> None:
        requests_seen: list[tuple[str, str, bytes | None]] = []

        def fake_urlopen(http_request: object, timeout: int) -> FakeHTTPResponse:
            del timeout
            method = http_request.get_method()
            url = http_request.full_url
            requests_seen.append((method, url, http_request.data))
            if method == "POST" and url.endswith("/lists/fetch"):
                return FakeHTTPResponse(
                    {
                        "listInfoList": [
                            {
                                "listId": "SHOPPING-LIST",
                                "listName": "Alexa Einkaufsliste",
                                "listType": "SHOPPING_ITEM",
                            }
                        ]
                    }
                )
            if method == "POST" and url.endswith("/lists/SHOPPING-LIST/items/fetch?limit=100"):
                return FakeHTTPResponse(
                    {
                        "itemInfoList": [
                            {
                                "itemId": "item-1",
                                "itemName": "Milch",
                                "itemStatus": "ACTIVE",
                                "version": 7,
                            },
                            {
                                "itemId": "item-2",
                                "itemName": "Alt",
                                "itemStatus": "COMPLETE",
                                "version": 2,
                            },
                        ]
                    }
                )
            return FakeHTTPResponse({})

        with tempfile.TemporaryDirectory() as tmp_dir:
            cookie_path = Path(tmp_dir) / "cookies.json"
            cookie_path.write_text(
                json.dumps(
                    [
                        {"name": "csrf", "value": "csrf-token"},
                        {"name": "session-id", "value": "session-value"},
                    ]
                ),
                encoding="utf-8",
            )

            with patch("alexa_client.request.urlopen", side_effect=fake_urlopen):
                with HttpAlexaClient("amazon.de", cookie_path) as client:
                    self.assertTrue(client.is_authenticated())
                    items = client.get_items()
                    client.add_item(TodoItem(uid="new", summary="Brot", status="needs_action"))
                    removed = client.remove_items(items)

        self.assertEqual([item.summary for item in items], ["Milch"])
        self.assertEqual(removed, 1)
        self.assertEqual([method for method, _url, _data in requests_seen], ["POST", "POST", "POST", "PUT"])
        list_fetch_payload = json.loads((requests_seen[0][2] or b"{}").decode("utf-8"))
        self.assertEqual(list_fetch_payload["listAttributesToAggregate"][0]["type"], "totalActiveItemsCount")
        item_fetch_payload = json.loads((requests_seen[1][2] or b"{}").decode("utf-8"))
        self.assertEqual(item_fetch_payload["itemAttributesToProject"], ["quantity", "note"])
        self.assertIn("version=7", requests_seen[-1][1])
        self.assertIn(b'"itemStatus"', requests_seen[-1][2] or b"")

    def test_http_client_selects_shop_list_type(self) -> None:
        client = HttpAlexaClient("amazon.de")

        self.assertGreater(
            client._shopping_score({"listType": "SHOP", "listId": "shopping-list"}),
            client._shopping_score({"listType": "TODO", "listId": "todo-list"}),
        )
        self.assertGreater(
            client._shopping_score({"listType": "SHOP", "listId": "shopping-list"}),
            client._shopping_score({"listType": "CUSTOM", "listName": "Baumarkt", "listId": "custom-list"}),
        )

    def test_http_client_extracts_list_id_from_list_shape(self) -> None:
        client = HttpAlexaClient("amazon.de")

        self.assertEqual(client._list_id_from_candidate({"id": ["list-id"]}), "list-id")


class ScriptDriver:
    def __init__(self, results: list[dict[str, object]]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute_script(self, script: str, *args: object) -> dict[str, object]:
        self.calls.append((script, args))
        if not args:
            return {"status": "reset"}
        return self.results.pop(0)


class TestableAlexaClient(InternalAlexaClient):
    def __init__(self) -> None:
        super().__init__("amazon.de")
        self.open_count = 0

    def _open_list(self) -> None:
        self.open_count += 1


class OpenListDriver:
    def __init__(self, list_open: bool = True) -> None:
        self.list_open = list_open
        self.scripts: list[str] = []
        self.urls: list[str] = []

    def execute_script(self, script: str, *args: object) -> bool:
        self.scripts.append(script)
        return self.list_open

    def get(self, url: str) -> None:
        self.urls.append(url)


class AlexaListCacheTests(unittest.TestCase):
    def test_open_list_reuses_current_loaded_list_page(self) -> None:
        client = InternalAlexaClient("amazon.de")
        driver = OpenListDriver(list_open=True)
        client.driver = driver
        client._list_loaded = True

        client._open_list()

        self.assertEqual(driver.urls, [])
        self.assertEqual(len(driver.scripts), 1)


class AlexaRemoveItemTests(unittest.TestCase):
    def test_remove_item_clicks_matching_row_via_dom_script(self) -> None:
        client = TestableAlexaClient()
        driver = ScriptDriver([{"status": "clicked", "text": "Biomuellbeutel"}])
        client.driver = driver

        with patch("alexa_client.time.sleep"):
            removed = client._remove_item_once(TodoItem(uid="1", summary="Biom\u00fcllbeutel", status="needs_action"))

        self.assertTrue(removed)
        self.assertEqual(client.open_count, 1)
        click_calls = [call for call in driver.calls if call[1]]
        self.assertEqual(click_calls[0][1][0], "biomuellbeutel")
        self.assertIn("querySelector", click_calls[0][0])

    def test_remove_item_returns_false_after_stable_not_found_scrolls(self) -> None:
        client = TestableAlexaClient()
        driver = ScriptDriver(
            [
                {"status": "not-found", "lastText": "Milch", "rowCount": 10},
                {"status": "not-found", "lastText": "Milch", "rowCount": 10},
                {"status": "not-found", "lastText": "Milch", "rowCount": 10},
            ]
        )
        client.driver = driver

        with patch("alexa_client.time.sleep"):
            removed = client._remove_item_once(TodoItem(uid="1", summary="Brot", status="needs_action"))

        self.assertFalse(removed)
        click_calls = [call for call in driver.calls if call[1]]
        self.assertEqual(len(click_calls), 3)

    def test_remove_items_opens_list_once_for_batch(self) -> None:
        client = TestableAlexaClient()
        driver = ScriptDriver(
            [
                {"status": "clicked", "text": "A"},
                {"status": "clicked", "text": "B"},
            ]
        )
        client.driver = driver

        with patch("alexa_client.time.sleep"):
            removed = client.remove_items(
                [
                    TodoItem(uid="1", summary="A", status="needs_action"),
                    TodoItem(uid="2", summary="B", status="needs_action"),
                ]
            )

        self.assertEqual(removed, 2)
        self.assertEqual(client.open_count, 1)
        click_calls = [call for call in driver.calls if call[1]]
        self.assertEqual([call[1][0] for call in click_calls], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
