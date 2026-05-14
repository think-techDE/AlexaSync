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

    server.shutdown()
    logger.info("Stopping Alexa Sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
