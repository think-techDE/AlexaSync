"""Alexa Sync Home Assistant add-on — entry point."""

from __future__ import annotations

import signal
import sys
import threading
import time
from typing import Any

from settings import (
    is_configured,
    load_settings,
    load_state,
    get_configuration_error,
    setup_logging,
)
from ha_client import HomeAssistantClient
from alexa_client import InternalAlexaClient, account_cookie_path, save_cookie_list
from sync import sync_once
from webui import run_web_server

STOP_EVENT = threading.Event()


def handle_stop(_signum: int, _frame: Any) -> None:
    """Request clean shutdown."""
    STOP_EVENT.set()


class RuntimeState:
    """Shared runtime state for sync loop and web UI."""

    def __init__(self, client: HomeAssistantClient) -> None:
        """Initialize runtime state."""
        self.client = client
        self.lock = threading.Lock()
        self.state = load_state()
        self.last_result: dict[str, Any] = {
            "configured": False,
            "last_sync": None,
            "last_writes": 0,
            "last_error": None,
        }
        self.setup_browser: InternalAlexaClient | None = None
        self.setup_account: dict[str, Any] | None = None

    def sync(self) -> dict[str, Any]:
        """Run one synchronized pass with locking."""
        with self.lock:
            settings = load_settings()
            if not is_configured(settings):
                self.last_result = {
                    "configured": False,
                    "last_sync": None,
                    "last_writes": 0,
                    "last_error": get_configuration_error(settings),
                }
                return self.last_result

            try:
                writes = sync_once(self.client, settings, self.state)
                self.last_result = {
                    "configured": True,
                    "last_sync": time.time(),
                    "last_writes": writes,
                    "last_error": None,
                }
            except Exception as exc:
                import logging
                logging.getLogger("alexa_sync").exception("Sync pass failed")
                self.last_result = {
                    "configured": True,
                    "last_sync": time.time(),
                    "last_writes": 0,
                    "last_error": str(exc),
                }
            return self.last_result

    def start_setup_browser(self, account: dict[str, Any]) -> dict[str, Any]:
        """Start an interactive Amazon login browser."""
        with self.lock:
            self.close_setup_browser()
            browser = InternalAlexaClient(
                account["amazon_domain"],
                account_cookie_path(account["id"]),
            )
            browser.__enter__()
            if browser.driver is None:
                raise RuntimeError("Browser konnte nicht gestartet werden.")
            browser.open_setup_page()
            self.setup_browser = browser
            self.setup_account = account
            return self.get_setup_screenshot()

    def close_setup_browser(self) -> None:
        """Close interactive setup browser if present."""
        if self.setup_browser is not None:
            self.setup_browser.__exit__(None, None, None)
            self.setup_browser = None
            self.setup_account = None

    def get_setup_screenshot(self) -> dict[str, Any]:
        """Return setup browser screenshot."""
        if self.setup_browser is None or self.setup_browser.driver is None:
            raise RuntimeError("Setup-Browser ist nicht gestartet.")
        driver = self.setup_browser.driver
        size = driver.get_window_size()
        return {
            "image": driver.get_screenshot_as_base64(),
            "width": int(size.get("width", 1366)),
            "height": int(size.get("height", 768)),
            "url": str(driver.current_url),
        }

    def click_setup_browser(self, x: int, y: int) -> dict[str, Any]:
        """Click in setup browser viewport."""
        if self.setup_browser is None or self.setup_browser.driver is None:
            raise RuntimeError("Setup-Browser ist nicht gestartet.")
        driver = self.setup_browser.driver
        driver.execute_cdp_cmd(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
        )
        driver.execute_cdp_cmd(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
        )
        time.sleep(0.2)
        return self.get_setup_screenshot()

    def type_setup_browser(self, text: str) -> dict[str, Any]:
        """Type text into focused setup browser element."""
        if self.setup_browser is None or self.setup_browser.driver is None:
            raise RuntimeError("Setup-Browser ist nicht gestartet.")
        self.setup_browser.driver.switch_to.active_element.send_keys(text)
        time.sleep(0.1)
        return self.get_setup_screenshot()

    def key_setup_browser(self, key: str) -> dict[str, Any]:
        """Send a special key to setup browser."""
        from selenium.webdriver.common.keys import Keys

        key_map = {
            "Backspace": Keys.BACKSPACE,
            "Enter": Keys.ENTER,
            "Escape": Keys.ESCAPE,
            "Tab": Keys.TAB,
        }
        if key not in key_map:
            raise ValueError("Nicht unterstuetzte Taste.")
        return self.type_setup_browser(key_map[key])

    def save_setup_cookies(self) -> dict[str, Any]:
        """Persist cookies from setup browser."""
        if self.setup_browser is None or self.setup_browser.driver is None:
            raise RuntimeError("Setup-Browser ist nicht gestartet.")
        if self.setup_account is None:
            raise RuntimeError("Kein Amazon-Konto fuer den Setup-Browser ausgewaehlt.")
        cookies = self.setup_browser.driver.get_cookies()
        if not cookies:
            raise RuntimeError("Keine Cookies im Setup-Browser gefunden.")
        save_cookie_list(cookies, account_cookie_path(self.setup_account["id"]))
        return {
            "saved": True,
            "cookie_count": len(cookies),
            "account_id": self.setup_account["id"],
            "account": dict(self.setup_account),
        }


def main() -> int:
    """Run add-on loop."""
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    settings = load_settings()
    setup_logging(settings["log_level"])
    client = HomeAssistantClient()
    runtime = RuntimeState(client)
    server = run_web_server(runtime)

    import logging
    logger = logging.getLogger("alexa_sync")
    logger.info("Alexa Sync started")

    while not STOP_EVENT.is_set():
        settings = load_settings()
        if is_configured(settings):
            logger.debug("Running sync in %s mode", settings["mode"])
            runtime.sync()
        else:
            logger.info("Waiting for configuration in the add-on web UI")

        sleep_until = time.monotonic() + settings["interval_seconds"]
        while not STOP_EVENT.is_set() and time.monotonic() < sleep_until:
            time.sleep(min(1, sleep_until - time.monotonic()))

    runtime.close_setup_browser()
    server.shutdown()
    logger.info("Stopping Alexa Sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
