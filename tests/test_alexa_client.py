from __future__ import annotations

import sys
import time
import unittest
import pickle
import tempfile
from http.cookies import Morsel, SimpleCookie
from http.cookiejar import Cookie
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "alexa_sync"))

from alexa_client import extract_alexa_media_cookies, load_alexa_media_cookie_pickle  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
