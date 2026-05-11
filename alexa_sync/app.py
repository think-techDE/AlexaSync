"""Alexa Sync Home Assistant add-on."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time
from typing import Any
from urllib import error, request
from websockets.sync.client import connect

API_BASE = "http://supervisor/core/api"
OPTIONS_PATH = Path("/data/options.json")
SETTINGS_PATH = Path("/data/settings.json")
STATE_PATH = Path("/data/sync_state.json")
ALEXA_COOKIES_PATH = Path("/data/alexa_cookies.json")
WEB_PORT = 8099

STATUS_NEEDS_ACTION = "needs_action"
STATUS_COMPLETED = "completed"

DEFAULT_SETTINGS = {
    "mode": "internal_alexa",
    "amazon_domain": "amazon.de",
    "list_a": "",
    "list_b": "",
    "alexa_server_host": "",
    "alexa_server_port": 4000,
    "ha_list": "",
    "interval_seconds": 120,
    "sync_completed": True,
    "remove_completed": False,
    "log_level": "info",
}

LOGGER = logging.getLogger("alexa_sync")
STOP_REQUESTED = False


@dataclass
class TodoItem:
    """Normalized Home Assistant to-do item."""

    uid: str
    summary: str
    status: str
    description: str | None = None


class RuntimeState:
    """Shared runtime state for sync loop and web UI."""

    def __init__(self, client: "HomeAssistantClient") -> None:
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

    def sync(self) -> dict[str, Any]:
        """Run one synchronized pass with locking."""
        with self.lock:
            settings = load_settings()
            if not is_configured(settings):
                self.last_result = {
                    "configured": False,
                    "last_sync": None,
                    "last_writes": 0,
                    "last_error": "Bitte zuerst den Sync-Modus konfigurieren.",
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
                LOGGER.exception("Sync pass failed")
                self.last_result = {
                    "configured": True,
                    "last_sync": time.time(),
                    "last_writes": 0,
                    "last_error": str(exc),
                }
            return self.last_result

    def start_setup_browser(self, amazon_domain: str) -> dict[str, Any]:
        """Start an interactive Amazon login browser."""
        with self.lock:
            self.close_setup_browser()
            browser = InternalAlexaClient(amazon_domain)
            browser.__enter__()
            if browser.driver is None:
                raise RuntimeError("Browser konnte nicht gestartet werden.")
            browser.open_setup_page()
            self.setup_browser = browser
            return self.get_setup_screenshot()

    def close_setup_browser(self) -> None:
        """Close interactive setup browser if present."""
        if self.setup_browser is not None:
            self.setup_browser.__exit__(None, None, None)
            self.setup_browser = None

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
        cookies = self.setup_browser.driver.get_cookies()
        if not cookies:
            raise RuntimeError("Keine Cookies im Setup-Browser gefunden.")
        save_cookie_list(cookies)
        return {"saved": True, "cookie_count": len(cookies)}


def handle_stop(_signum: int, _frame: Any) -> None:
    """Request clean shutdown."""
    global STOP_REQUESTED
    STOP_REQUESTED = True


def setup_logging(level: str) -> None:
    """Configure logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def read_json_file(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    """Read a JSON file or return fallback."""
    if not path.exists():
        return dict(fallback)
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("Ignoring invalid JSON file %s", path)
        return dict(fallback)
    if not isinstance(data, dict):
        return dict(fallback)
    return data


def write_json_file(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically."""
    tmp_path = path.with_suffix(".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tmp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=True, indent=2, sort_keys=True)
    tmp_path.replace(path)


def save_cookie_list(cookies: list[dict[str, Any]]) -> None:
    """Persist Amazon cookies with owner-only permissions where supported."""
    ALEXA_COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = ALEXA_COOKIES_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as cookie_file:
        json.dump(cookies, cookie_file, ensure_ascii=True, indent=2)
    tmp_path.replace(ALEXA_COOKIES_PATH)
    try:
        ALEXA_COOKIES_PATH.chmod(0o600)
    except OSError:
        LOGGER.debug("Could not chmod cookie file", exc_info=True)


def parse_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Parse bounded integer settings."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def normalize_amazon_domain(value: Any) -> str:
    """Normalize Amazon marketplace domain input."""
    domain = str(value or "amazon.de").strip().lower()
    domain = re.sub(r"^https?://", "", domain)
    domain = domain.split("/", 1)[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain or "amazon.de"


def load_options() -> dict[str, Any]:
    """Load add-on options as initial defaults."""
    return read_json_file(OPTIONS_PATH, DEFAULT_SETTINGS)


def normalize_settings(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize user settings."""
    settings = dict(DEFAULT_SETTINGS)
    settings.update(raw)
    settings["mode"] = str(settings.get("mode", "internal_alexa")).strip()
    settings["amazon_domain"] = normalize_amazon_domain(settings.get("amazon_domain", "amazon.de"))
    settings["list_a"] = str(settings.get("list_a", "")).strip()
    settings["list_b"] = str(settings.get("list_b", "")).strip()
    settings["alexa_server_host"] = str(settings.get("alexa_server_host", "")).strip()
    settings["alexa_server_port"] = parse_int(settings.get("alexa_server_port"), 4000, 1, 65535)
    settings["ha_list"] = str(settings.get("ha_list", "")).strip()
    settings["interval_seconds"] = parse_int(settings.get("interval_seconds"), 120, 30, 3600)
    settings["sync_completed"] = bool(settings.get("sync_completed", True))
    settings["remove_completed"] = bool(settings.get("remove_completed", False))
    settings["log_level"] = str(settings.get("log_level", "info")).lower()
    return settings


def load_settings() -> dict[str, Any]:
    """Load effective settings."""
    if not SETTINGS_PATH.exists():
        return normalize_settings(load_options())
    return normalize_settings(read_json_file(SETTINGS_PATH, DEFAULT_SETTINGS))


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Persist settings from the web UI."""
    normalized = normalize_settings(settings)
    validate_settings(normalized)
    write_json_file(SETTINGS_PATH, normalized)
    return normalized


def validate_settings(settings: dict[str, Any]) -> None:
    """Validate settings."""
    if settings["mode"] == "internal_alexa":
        if not settings["amazon_domain"]:
            raise ValueError("Bitte Amazon-Domain eintragen.")
        if not settings["ha_list"]:
            raise ValueError("Bitte eine Home-Assistant-Liste auswaehlen.")
        return

    if settings["mode"] == "alexa_server":
        if bool(settings["alexa_server_host"]) != bool(settings["ha_list"]):
            raise ValueError("Bitte Alexa-Server und Home-Assistant-Liste auswaehlen.")
        if settings["alexa_server_port"] <= 0:
            raise ValueError("Bitte einen gueltigen Alexa-Server-Port angeben.")
        return

    if bool(settings["list_a"]) != bool(settings["list_b"]):
        raise ValueError("Bitte beide Listen auswaehlen.")
    if settings["list_a"] and settings["list_a"] == settings["list_b"]:
        raise ValueError("Bitte zwei unterschiedliche Listen auswaehlen.")


def is_configured(settings: dict[str, Any]) -> bool:
    """Return if sync lists are configured."""
    if settings["mode"] == "internal_alexa":
        return bool(settings["amazon_domain"] and settings["ha_list"])
    if settings["mode"] == "alexa_server":
        return bool(settings["alexa_server_host"] and settings["ha_list"])
    return bool(settings["list_a"] and settings["list_b"] and settings["list_a"] != settings["list_b"])


def load_state() -> dict[str, Any]:
    """Load persistent sync state."""
    return read_json_file(STATE_PATH, {"items": {}})


def save_state(state: dict[str, Any]) -> None:
    """Persist sync state."""
    write_json_file(STATE_PATH, state)


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


class AlexaServerClient:
    """Client for madmachinations Alexa Shopping List server."""

    def __init__(self, host: str, port: int) -> None:
        """Initialize client."""
        self.url = f"ws://{host}:{port}"

    def command(self, command: str, args: dict[str, Any] | None = None) -> Any:
        """Run one Alexa server command."""
        payload = {"command": command, "args": args or {}}
        with connect(self.url, open_timeout=30, close_timeout=10) as websocket:
            websocket.send(json.dumps(payload))
            response = json.loads(websocket.recv())

        if response.get("error"):
            raise RuntimeError(f"Alexa server command {command} failed: {response['error']}")
        return response.get("result")

    def get_items(self) -> list[TodoItem]:
        """Return active Alexa shopping list items."""
        result = self.command("get_list")
        if not isinstance(result, list):
            return []
        return [
            TodoItem(uid=str(item), summary=str(item), status=STATUS_NEEDS_ACTION)
            for item in result
            if str(item).strip()
        ]

    def add_item(self, item: TodoItem) -> None:
        """Add an item to Alexa."""
        self.command("add_item", {"item": item.summary})

    def remove_item(self, item: TodoItem) -> None:
        """Remove an item from Alexa."""
        self.command("remove_item", {"item": item.summary})


class InternalAlexaClient:
    """Selenium-backed Alexa shopping list client."""

    def __init__(self, amazon_domain: str) -> None:
        """Initialize client."""
        self.amazon_domain = amazon_domain
        self.driver = None

    def __enter__(self) -> "InternalAlexaClient":
        """Start browser."""
        self.driver = self._create_driver()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        """Stop browser."""
        if self.driver is not None:
            self.driver.quit()
            self.driver = None

    def _create_driver(self) -> Any:
        """Create a Chromium webdriver."""
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1366,768")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        return webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=options)

    def _load_cookies(self) -> None:
        """Load persisted Amazon cookies into Chromium."""
        if self.driver is None:
            raise RuntimeError("Browser is not running")
        if not ALEXA_COOKIES_PATH.exists():
            raise RuntimeError("Amazon-Session fehlt. Bitte Cookies in der Weboberflaeche importieren.")

        self.driver.get(f"https://www.{self.amazon_domain}")
        with ALEXA_COOKIES_PATH.open("r", encoding="utf-8") as cookie_file:
            cookies = json.load(cookie_file)
        if not isinstance(cookies, list):
            raise RuntimeError("Cookie-Datei muss eine JSON-Liste enthalten.")

        for cookie in cookies:
            if not isinstance(cookie, dict) or "name" not in cookie or "value" not in cookie:
                continue
            safe_cookie = {
                key: value
                for key, value in cookie.items()
                if key in {"name", "value", "domain", "path", "expiry", "secure", "httpOnly", "sameSite"}
            }
            try:
                self.driver.add_cookie(safe_cookie)
            except Exception:
                LOGGER.debug("Ignoring incompatible Amazon cookie %s", cookie.get("name"), exc_info=True)
        self.driver.refresh()
        time.sleep(2)

    def _shopping_list_url(self) -> str:
        """Return the Alexa shopping list URL for the configured marketplace."""
        return f"https://www.{self.amazon_domain}/alexaquantum/sp/alexaShoppingList?ref=nav_asl"

    def _account_url(self) -> str:
        """Return a robust account URL that redirects to Amazon sign-in if needed."""
        return f"https://www.{self.amazon_domain}/gp/css/homepage.html?ref_=nav_AccountFlyout_ya"

    def _home_url(self) -> str:
        """Return Amazon marketplace home URL."""
        return f"https://www.{self.amazon_domain}/"

    def open_setup_page(self) -> None:
        """Open a login-capable Amazon page for interactive setup."""
        if self.driver is None:
            raise RuntimeError("Browser is not running")
        self.driver.get(self._account_url())
        time.sleep(3)
        page = self.driver.page_source.lower()
        current_url = str(self.driver.current_url).lower()
        if "suchst du etwas" in page or "web-adresse" in page or "/errors/" in current_url:
            LOGGER.info("Amazon account URL did not load, falling back to marketplace home")
            self.driver.get(self._home_url())
            time.sleep(3)

    def is_authenticated(self) -> bool:
        """Return if imported cookies still authenticate with Amazon."""
        if self.driver is None:
            raise RuntimeError("Browser is not running")
        self._load_cookies()
        self.driver.get(self._shopping_list_url())
        time.sleep(3)
        current_url = str(self.driver.current_url).lower()
        page = self.driver.page_source.lower()
        return "ap/signin" not in current_url and "virtual-list" in page

    def _open_list(self) -> None:
        """Open Alexa shopping list page."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as ec
        from selenium.webdriver.support.ui import WebDriverWait

        if self.driver is None:
            raise RuntimeError("Browser is not running")
        self._load_cookies()
        self.driver.get(self._shopping_list_url())
        WebDriverWait(self.driver, 30).until(ec.presence_of_element_located((By.CLASS_NAME, "virtual-list")))
        time.sleep(3)

    def get_items(self) -> list[TodoItem]:
        """Return active Alexa shopping list items."""
        from selenium.webdriver.common.by import By

        if self.driver is None:
            raise RuntimeError("Browser is not running")
        self._open_list()
        list_container = self.driver.find_element(By.CLASS_NAME, "virtual-list")
        found: list[str] = []
        last_text = None
        stable_rounds = 0

        while stable_rounds < 2:
            elements = list_container.find_elements(By.CLASS_NAME, "item-title")
            before_count = len(found)
            for element in elements:
                text = element.get_attribute("innerText").strip()
                if text and text not in found:
                    found.append(text)

            current_last = elements[-1].get_attribute("innerText") if elements else None
            if len(found) == before_count and current_last == last_text:
                stable_rounds += 1
            else:
                stable_rounds = 0
            last_text = current_last

            if elements:
                self.driver.execute_script("arguments[0].scrollIntoView();", elements[-1])
            time.sleep(1)

        return [TodoItem(uid=item, summary=item, status=STATUS_NEEDS_ACTION) for item in found]

    def add_item(self, item: TodoItem) -> None:
        """Add an item to Alexa."""
        from selenium.webdriver.common.by import By

        if self.driver is None:
            raise RuntimeError("Browser is not running")
        self._open_list()
        header = self.driver.find_element(By.CLASS_NAME, "list-header")
        header.find_element(By.CLASS_NAME, "add-symbol").click()
        textfield = header.find_element(By.CLASS_NAME, "input-box").find_element(By.TAG_NAME, "input")
        textfield.send_keys(item.summary)
        header.find_element(By.CLASS_NAME, "add-to-list").find_element(By.TAG_NAME, "button").click()
        time.sleep(1)

    def remove_item(self, item: TodoItem) -> None:
        """Remove an item from Alexa active list."""
        from selenium.webdriver.common.by import By

        if self.driver is None:
            raise RuntimeError("Browser is not running")
        self._open_list()
        list_container = self.driver.find_element(By.CLASS_NAME, "virtual-list")
        last_text = None
        stable_rounds = 0

        while stable_rounds < 2:
            rows = list_container.find_elements(By.CLASS_NAME, "inner")
            for row in rows:
                title = row.find_element(By.CLASS_NAME, "item-title").get_attribute("innerText").strip()
                if normalize_summary(title) == normalize_summary(item.summary):
                    row.find_element(By.CLASS_NAME, "item-actions-2").find_element(By.TAG_NAME, "button").click()
                    time.sleep(1)
                    return

            current_last = rows[-1].text if rows else None
            if current_last == last_text:
                stable_rounds += 1
            else:
                stable_rounds = 0
            last_text = current_last

            if rows:
                self.driver.execute_script("arguments[0].scrollIntoView();", rows[-1])
            time.sleep(1)


class ConfigHandler(BaseHTTPRequestHandler):
    """Ingress web UI and JSON API."""

    runtime: RuntimeState

    def log_message(self, format_text: str, *args: Any) -> None:
        """Route HTTP access logs through the add-on logger."""
        LOGGER.debug(format_text, *args)

    def do_GET(self) -> None:
        """Serve UI and API."""
        if self.path in {"/", "/index.html"}:
            self.send_html(INDEX_HTML)
            return
        if self.path == "/api/config":
            self.send_json(self.get_config_payload())
            return
        if self.path == "/api/alexa/status":
            self.send_json(self.get_alexa_status_payload())
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        """Handle API writes."""
        if self.path == "/api/config":
            try:
                payload = self.read_json()
                settings = save_settings(payload)
                self.send_json({"ok": True, "settings": settings})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/sync":
            result = self.runtime.sync()
            self.send_json({"ok": result.get("last_error") is None, "result": result})
            return
        if self.path == "/api/alexa/cookies":
            try:
                payload = self.read_json()
                cookies = payload.get("cookies")
                if isinstance(cookies, str):
                    cookies = json.loads(cookies)
                if not isinstance(cookies, list):
                    raise ValueError("Cookies muessen als JSON-Liste uebergeben werden.")
                save_cookie_list(cookies)
                self.send_json({"ok": True})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/setup/start":
            try:
                settings = load_settings()
                screenshot = self.runtime.start_setup_browser(settings["amazon_domain"])
                self.send_json({"ok": True, "screenshot": screenshot})
            except Exception as exc:
                LOGGER.exception("Setup browser start failed")
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/setup/click":
            try:
                payload = self.read_json()
                screenshot = self.runtime.click_setup_browser(int(payload["x"]), int(payload["y"]))
                self.send_json({"ok": True, "screenshot": screenshot})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/setup/type":
            try:
                payload = self.read_json()
                screenshot = self.runtime.type_setup_browser(str(payload.get("text", "")))
                self.send_json({"ok": True, "screenshot": screenshot})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/setup/key":
            try:
                payload = self.read_json()
                screenshot = self.runtime.key_setup_browser(str(payload.get("key", "")))
                self.send_json({"ok": True, "screenshot": screenshot})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/setup/save":
            try:
                result = self.runtime.save_setup_cookies()
                self.send_json({"ok": True, "result": result})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/setup/stop":
            try:
                self.runtime.close_setup_browser()
                self.send_json({"ok": True})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def get_config_payload(self) -> dict[str, Any]:
        """Return UI configuration payload."""
        try:
            entities = self.runtime.client.list_todo_entities()
            entity_error = None
        except Exception as exc:
            LOGGER.exception("Failed to list todo entities")
            entities = []
            entity_error = str(exc)

        return {
            "settings": load_settings(),
            "todo_entities": entities,
            "suggested_ha_list": suggest_todo_entity(entities),
            "status": self.runtime.last_result,
            "alexa_cookies_present": ALEXA_COOKIES_PATH.exists(),
            "entity_error": entity_error,
        }

    def get_alexa_status_payload(self) -> dict[str, Any]:
        """Return internal Alexa authentication status."""
        settings = load_settings()
        if not ALEXA_COOKIES_PATH.exists():
            return {"ok": True, "authenticated": False, "message": "Keine Cookies importiert."}
        try:
            with InternalAlexaClient(settings["amazon_domain"]) as alexa:
                authenticated = alexa.is_authenticated()
            return {
                "ok": True,
                "authenticated": authenticated,
                "message": "Amazon-Session ist gueltig." if authenticated else "Amazon-Session ist nicht gueltig.",
            }
        except Exception as exc:
            LOGGER.exception("Alexa authentication check failed")
            return {"ok": False, "authenticated": False, "message": str(exc)}

    def read_json(self) -> dict[str, Any]:
        """Read request JSON."""
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("Invalid JSON payload")
        return data

    def send_html(self, html: str) -> None:
        """Send HTML response."""
        data = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        """Send JSON response."""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


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


def suggest_todo_entity(entities: list[dict[str, str]]) -> str:
    """Suggest the most likely shopping list entity."""
    candidates = ("bring", "einkauf", "shopping")
    for entity in entities:
        haystack = f"{entity.get('name', '')} {entity.get('entity_id', '')}".casefold()
        if any(candidate in haystack for candidate in candidates):
            return entity["entity_id"]
    return entities[0]["entity_id"] if entities else ""


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


def sync_once(client: HomeAssistantClient, settings: dict[str, Any], state: dict[str, Any]) -> int:
    """Run one synchronization pass. Returns number of write operations."""
    if settings["mode"] == "internal_alexa":
        return sync_internal_alexa_once(client, settings, state)

    if settings["mode"] == "alexa_server":
        return sync_alexa_server_once(client, settings, state)

    list_a = settings["list_a"]
    list_b = settings["list_b"]
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
        if target_status and settings["sync_completed"]:
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

    if settings["remove_completed"]:
        LOGGER.info("Removing completed items from both lists")
        client.remove_completed_items(list_a)
        client.remove_completed_items(list_b)
        writes += 2

    return writes


def sync_internal_alexa_once(
    client: HomeAssistantClient, settings: dict[str, Any], state: dict[str, Any]
) -> int:
    """Synchronize built-in Alexa Selenium client with one Home Assistant to-do list."""
    with InternalAlexaClient(settings["amazon_domain"]) as alexa:
        if not alexa.is_authenticated():
            raise RuntimeError("Amazon-Session ist nicht authentifiziert. Bitte Cookies neu importieren.")
        return sync_alexa_items_with_ha(
            alexa,
            client,
            settings["ha_list"],
            settings,
            state,
            alexa_label="interne Alexa-Liste",
        )


def sync_alexa_server_once(
    client: HomeAssistantClient, settings: dict[str, Any], state: dict[str, Any]
) -> int:
    """Synchronize Alexa server active items with one Home Assistant to-do list."""
    alexa = AlexaServerClient(settings["alexa_server_host"], settings["alexa_server_port"])
    return sync_alexa_items_with_ha(
        alexa,
        client,
        settings["ha_list"],
        settings,
        state,
        alexa_label="Alexa-Server",
    )


def sync_alexa_items_with_ha(
    alexa: Any,
    client: HomeAssistantClient,
    ha_entity: str,
    settings: dict[str, Any],
    state: dict[str, Any],
    *,
    alexa_label: str,
) -> int:
    """Synchronize active Alexa items with one Home Assistant to-do list."""
    alexa_items = index_items(alexa.get_items())
    ha_items = index_items(client.get_items(ha_entity))
    stored_items = state.setdefault("items", {})
    keys = set(alexa_items) | set(ha_items) | set(stored_items)
    writes = 0

    for key in sorted(keys):
        alexa_item = alexa_items.get(key)
        ha_item = ha_items.get(key)
        item_state = stored_items.setdefault(key, {})

        if alexa_item is None and ha_item is None:
            stored_items.pop(key, None)
            continue

        if alexa_item is None and ha_item is not None:
            if (
                item_state.get("a_uid")
                and item_state.get("b_status") == STATUS_NEEDS_ACTION
                and ha_item.status == STATUS_NEEDS_ACTION
            ):
                LOGGER.info(
                    "Marking '%s' in %s as completed because it disappeared from %s",
                    ha_item.summary,
                    ha_entity,
                    alexa_label,
                )
                client.update_status(ha_entity, ha_item.uid, STATUS_COMPLETED)
                ha_item.status = STATUS_COMPLETED
                writes += 1
                remember(item_state, alexa_item, ha_item)
                continue
            if ha_item.status == STATUS_NEEDS_ACTION:
                LOGGER.info("Creating '%s' in %s", ha_item.summary, alexa_label)
                alexa.add_item(ha_item)
                writes += 1
            remember(item_state, alexa_item, ha_item)
            continue

        if ha_item is None and alexa_item is not None:
            LOGGER.info("Creating '%s' in %s", alexa_item.summary, ha_entity)
            client.add_item(ha_entity, alexa_item)
            writes += 1
            remember(item_state, alexa_item, ha_item)
            continue

        if (
            alexa_item is not None
            and ha_item is not None
            and settings["sync_completed"]
            and ha_item.status == STATUS_COMPLETED
        ):
            LOGGER.info("Removing completed '%s' from Alexa", ha_item.summary)
            alexa.remove_item(alexa_item)
            writes += 1

        remember(item_state, alexa_item, ha_item)

    state["updated_at"] = time.time()
    save_state(state)

    if settings["remove_completed"]:
        LOGGER.info("Removing completed items from %s", ha_entity)
        client.remove_completed_items(ha_entity)
        writes += 1

    return writes


def run_web_server(runtime: RuntimeState) -> ThreadingHTTPServer:
    """Start the ingress web server in a background thread."""
    ConfigHandler.runtime = runtime
    server = ThreadingHTTPServer(("", WEB_PORT), ConfigHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    LOGGER.info("Configuration UI listening on port %s", WEB_PORT)
    return server


def main() -> int:
    """Run add-on loop."""
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    settings = load_settings()
    setup_logging(settings["log_level"])
    client = HomeAssistantClient()
    runtime = RuntimeState(client)
    server = run_web_server(runtime)

    LOGGER.info("Alexa Sync started")

    while not STOP_REQUESTED:
        settings = load_settings()
        if is_configured(settings):
            LOGGER.debug("Running sync in %s mode", settings["mode"])
            runtime.sync()
        else:
            LOGGER.info("Waiting for configuration in the add-on web UI")

        sleep_until = time.monotonic() + settings["interval_seconds"]
        while not STOP_REQUESTED and time.monotonic() < sleep_until:
            time.sleep(min(1, sleep_until - time.monotonic()))

    runtime.close_setup_browser()
    server.shutdown()
    LOGGER.info("Stopping Alexa Sync")
    return 0


INDEX_HTML = r"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Alexa Sync</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #5d6875;
      --border: #d8dde3;
      --accent: #0b6bcb;
      --accent-contrast: #ffffff;
      --danger: #b42318;
      --ok: #067647;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #111418;
        --panel: #1b2027;
        --text: #eef2f6;
        --muted: #aeb8c4;
        --border: #303946;
        --accent: #5aa7ff;
        --accent-contrast: #07111f;
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 15px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main {
      width: min(860px, calc(100% - 32px));
      margin: 32px auto;
    }
    h1 {
      margin: 0 0 8px;
      font-size: 28px;
      font-weight: 650;
    }
    p {
      margin: 0 0 24px;
      color: var(--muted);
    }
    .mode-row {
      display: grid;
      gap: 10px;
      margin-bottom: 18px;
    }
    details {
      margin: 14px 0 18px;
    }
    summary {
      color: var(--accent);
      cursor: pointer;
      font-weight: 650;
      width: fit-content;
    }
    .radio {
      display: flex;
      gap: 10px;
      align-items: flex-start;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      font-weight: 600;
    }
    .radio span {
      display: block;
      color: var(--muted);
      font-size: 13px;
      font-weight: 400;
      margin-top: 2px;
    }
    form, .status {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 20px;
      margin-bottom: 16px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }
    label {
      display: block;
      font-weight: 600;
      margin-bottom: 6px;
    }
    select, input[type="number"], input[type="text"], input[type="password"], textarea {
      width: 100%;
      min-height: 42px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--panel);
      color: var(--text);
      padding: 8px 10px;
      font: inherit;
    }
    textarea {
      min-height: 120px;
      resize: vertical;
    }
    .setup-browser {
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-top: 14px;
      overflow: hidden;
      background: #000;
      display: none;
    }
    .setup-browser img {
      display: block;
      width: 100%;
      height: auto;
      cursor: crosshair;
      user-select: none;
    }
    .setup-hint {
      color: var(--muted);
      margin: 10px 0 0;
      font-size: 13px;
    }
    .checks {
      display: grid;
      gap: 10px;
      margin-top: 16px;
    }
    .check {
      display: flex;
      gap: 10px;
      align-items: center;
      font-weight: 500;
    }
    .actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 20px;
    }
    button {
      min-height: 42px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--panel);
      color: var(--text);
      padding: 8px 14px;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
    }
    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: var(--accent-contrast);
    }
    .message {
      min-height: 22px;
      margin-top: 14px;
      color: var(--muted);
    }
    .message.error { color: var(--danger); }
    .message.ok { color: var(--ok); }
    dl {
      display: grid;
      grid-template-columns: 180px 1fr;
      gap: 8px 12px;
      margin: 0;
    }
    dt { color: var(--muted); }
    dd { margin: 0; }
    @media (max-width: 700px) {
      .grid, dl { grid-template-columns: 1fr; }
      main { margin: 20px auto; }
    }
  </style>
</head>
<body>
  <main>
    <h1>Alexa Sync</h1>
    <p>Alexa per Sprache befuellen, in Bring abhaken. Waehle nur deine Bring-Liste und speichere eine Amazon-Session.</p>

    <form id="config-form">
      <div class="mode-row">
        <label class="radio">
          <input type="radio" name="mode" value="internal_alexa">
          <span><strong>Alexa direkt mit Bring synchronisieren</strong><br>Empfohlen. Ein Add-on, keine zweite Server-Komponente.</span>
        </label>
      </div>
      <details>
        <summary>Erweiterte Modi</summary>
        <div class="mode-row">
          <label class="radio">
            <input type="radio" name="mode" value="ha_todo_pair">
            <span><strong>Home Assistant Liste - Home Assistant Liste</strong><br>Fuer Bring, lokale Listen oder andere vorhandene `todo.*`-Entities.</span>
          </label>
          <label class="radio">
            <input type="radio" name="mode" value="alexa_server">
            <span><strong>Externer Alexa Shopping List Server - Home Assistant Liste</strong><br>Kompatibilitaetsmodus fuer bestehende Installationen.</span>
          </label>
        </div>
      </details>
      <div class="grid">
        <div class="internal-alexa-field">
          <label for="amazon-domain">Amazon-Domain</label>
          <input id="amazon-domain" name="amazon_domain" type="text" placeholder="amazon.de">
        </div>
        <div class="internal-alexa-field">
          <label for="internal-ha-list">Bring-/Ziel-Liste</label>
          <select id="internal-ha-list" name="internal_ha_list"></select>
        </div>
        <div class="ha-pair-field">
          <label for="list-a">Liste A</label>
          <select id="list-a" name="list_a"></select>
        </div>
        <div class="ha-pair-field">
          <label for="list-b">Liste B</label>
          <select id="list-b" name="list_b"></select>
        </div>
        <div class="alexa-field">
          <label for="alexa-host">Alexa-Server Host/IP</label>
          <input id="alexa-host" name="alexa_server_host" type="text" placeholder="192.168.1.10">
        </div>
        <div class="alexa-field">
          <label for="alexa-port">Alexa-Server Port</label>
          <input id="alexa-port" name="alexa_server_port" type="number" min="1" max="65535" step="1">
        </div>
        <div class="alexa-field">
          <label for="ha-list">Home-Assistant-Liste</label>
          <select id="ha-list" name="ha_list"></select>
        </div>
        <div>
          <label for="interval">Sync-Intervall in Sekunden</label>
          <input id="interval" name="interval_seconds" type="number" min="10" max="3600" step="5">
        </div>
      </div>
      <div class="internal-alexa-field">
        <label>Amazon-Session</label>
        <div class="actions">
          <button id="setup-start" type="button">Amazon-Anmeldung oeffnen</button>
          <button id="setup-save" type="button">Session uebernehmen</button>
          <button id="setup-stop" type="button">Browser schliessen</button>
          <button id="check-alexa" type="button">Amazon-Session pruefen</button>
        </div>
        <p class="setup-hint">Nach dem Oeffnen in die Browseransicht klicken und normal anmelden. Wenn Amazon die Startseite zeigt, oben Konto/Anmelden waehlen. Enter, Tab und Backspace werden uebertragen. Danach Session uebernehmen.</p>
        <div id="setup-browser" class="setup-browser">
          <img id="setup-screenshot" alt="Amazon Login Browser">
        </div>
      </div>
      <div class="internal-alexa-field">
        <label for="cookies">Fallback: Amazon-Session-Cookies als JSON</label>
        <textarea id="cookies" placeholder='[{"name":"session-id","value":"...","domain":".amazon.de"}]'></textarea>
        <div class="actions">
          <button id="save-cookies" type="button">Cookies importieren</button>
        </div>
      </div>
      <div class="checks">
        <label class="check"><input id="sync-completed" name="sync_completed" type="checkbox"> Abgehakte Eintraege synchronisieren</label>
        <label class="check"><input id="remove-completed" name="remove_completed" type="checkbox"> Abgehakte Eintraege nach dem Sync aus beiden Listen entfernen</label>
      </div>
      <div class="actions">
        <button class="primary" type="submit">Speichern</button>
        <button id="sync-now" type="button">Jetzt synchronisieren</button>
      </div>
      <div id="message" class="message"></div>
    </form>

    <section class="status">
      <dl>
        <dt>Status</dt><dd id="status-configured">-</dd>
        <dt>Letzter Sync</dt><dd id="status-last-sync">-</dd>
        <dt>Letzte Schreibvorgaenge</dt><dd id="status-writes">-</dd>
        <dt>Letzter Fehler</dt><dd id="status-error">-</dd>
      </dl>
    </section>
  </main>

  <script>
    const listA = document.getElementById("list-a");
    const listB = document.getElementById("list-b");
    const haList = document.getElementById("ha-list");
    const internalHaList = document.getElementById("internal-ha-list");
    const amazonDomain = document.getElementById("amazon-domain");
    const alexaHost = document.getElementById("alexa-host");
    const alexaPort = document.getElementById("alexa-port");
    const interval = document.getElementById("interval");
    const syncCompleted = document.getElementById("sync-completed");
    const removeCompleted = document.getElementById("remove-completed");
    const message = document.getElementById("message");
    const cookies = document.getElementById("cookies");
    const setupBrowser = document.getElementById("setup-browser");
    const setupScreenshot = document.getElementById("setup-screenshot");
    const modeInputs = [...document.querySelectorAll('input[name="mode"]')];
    let setupFocused = false;
    let setupSize = {width: 1366, height: 768};

    function setMessage(text, type = "") {
      message.textContent = text;
      message.className = `message ${type}`.trim();
    }

    function option(value, label) {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = label;
      return opt;
    }

    function fillSelect(select, entities, selected) {
      select.replaceChildren(option("", "Bitte auswaehlen"));
      for (const entity of entities) {
        select.appendChild(option(entity.entity_id, `${entity.name} (${entity.entity_id})`));
      }
      select.value = selected || "";
    }

    function renderStatus(status) {
      document.getElementById("status-configured").textContent = status.configured ? "konfiguriert" : "nicht konfiguriert";
      document.getElementById("status-last-sync").textContent = status.last_sync ? new Date(status.last_sync * 1000).toLocaleString() : "-";
      document.getElementById("status-writes").textContent = status.last_writes ?? "-";
      document.getElementById("status-error").textContent = status.last_error || "-";
    }

    function selectedMode() {
      return document.querySelector('input[name="mode"]:checked')?.value || "internal_alexa";
    }

    function applyModeVisibility() {
      const mode = selectedMode();
      document.querySelectorAll(".ha-pair-field").forEach((el) => {
        el.style.display = mode === "ha_todo_pair" ? "" : "none";
      });
      document.querySelectorAll(".alexa-field").forEach((el) => {
        el.style.display = mode === "alexa_server" ? "" : "none";
      });
      document.querySelectorAll(".internal-alexa-field").forEach((el) => {
        el.style.display = mode === "internal_alexa" ? "" : "none";
      });
    }

    async function loadConfig() {
      const res = await fetch("api/config");
      const data = await res.json();
      const settings = data.settings;
      const mode = settings.mode || "internal_alexa";
      const suggestedList = data.suggested_ha_list || "";
      document.querySelector(`input[name="mode"][value="${mode}"]`).checked = true;
      fillSelect(listA, data.todo_entities, settings.list_a);
      fillSelect(listB, data.todo_entities, settings.list_b);
      fillSelect(haList, data.todo_entities, settings.ha_list);
      fillSelect(internalHaList, data.todo_entities, settings.ha_list || suggestedList);
      amazonDomain.value = settings.amazon_domain || "amazon.de";
      alexaHost.value = settings.alexa_server_host || "";
      alexaPort.value = settings.alexa_server_port || 4000;
      interval.value = settings.interval_seconds;
      syncCompleted.checked = settings.sync_completed;
      removeCompleted.checked = settings.remove_completed;
      applyModeVisibility();
      renderStatus(data.status);
      if (data.entity_error) setMessage(data.entity_error, "error");
    }

    document.getElementById("config-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const payload = {
        mode: selectedMode(),
        amazon_domain: amazonDomain.value,
        list_a: listA.value,
        list_b: listB.value,
        alexa_server_host: alexaHost.value,
        alexa_server_port: Number(alexaPort.value),
        ha_list: selectedMode() === "internal_alexa" ? internalHaList.value : haList.value,
        interval_seconds: Number(interval.value),
        sync_completed: syncCompleted.checked,
        remove_completed: removeCompleted.checked
      };
      const res = await fetch("api/config", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!data.ok) {
        setMessage(data.error || "Speichern fehlgeschlagen", "error");
        return;
      }
      setMessage("Gespeichert.", "ok");
      await loadConfig();
    });

    document.getElementById("sync-now").addEventListener("click", async () => {
      setMessage("Synchronisiere...");
      const res = await fetch("api/sync", {method: "POST"});
      const data = await res.json();
      renderStatus(data.result);
      setMessage(data.ok ? "Sync abgeschlossen." : data.result.last_error, data.ok ? "ok" : "error");
    });

    document.getElementById("save-cookies").addEventListener("click", async () => {
      const res = await fetch("api/alexa/cookies", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({cookies: cookies.value})
      });
      const data = await res.json();
      setMessage(data.ok ? "Cookies importiert." : data.error, data.ok ? "ok" : "error");
    });

    function renderSetupScreenshot(payload) {
      setupSize = {width: payload.width || 1366, height: payload.height || 768};
      setupScreenshot.src = `data:image/png;base64,${payload.image}`;
      setupBrowser.style.display = "block";
    }

    async function setupAction(path, body = {}) {
      const res = await fetch(path, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body)
      });
      const data = await res.json();
      if (!data.ok) {
        setMessage(data.error || "Aktion fehlgeschlagen", "error");
        return null;
      }
      if (data.screenshot) renderSetupScreenshot(data.screenshot);
      return data;
    }

    document.getElementById("setup-start").addEventListener("click", async () => {
      setMessage("Oeffne Amazon-Anmeldung...");
      const data = await setupAction("api/setup/start");
      if (data) setMessage("Amazon-Anmeldung geoeffnet.", "ok");
    });

    document.getElementById("setup-save").addEventListener("click", async () => {
      const data = await setupAction("api/setup/save");
      if (data) setMessage(`Session gespeichert (${data.result.cookie_count} Cookies).`, "ok");
    });

    document.getElementById("setup-stop").addEventListener("click", async () => {
      const data = await setupAction("api/setup/stop");
      if (data) {
        setupBrowser.style.display = "none";
        setMessage("Browser geschlossen.", "ok");
      }
    });

    setupScreenshot.addEventListener("click", async (event) => {
      setupFocused = true;
      const rect = setupScreenshot.getBoundingClientRect();
      const x = Math.round((event.clientX - rect.left) * setupSize.width / rect.width);
      const y = Math.round((event.clientY - rect.top) * setupSize.height / rect.height);
      await setupAction("api/setup/click", {x, y});
    });

    document.addEventListener("keydown", async (event) => {
      if (!setupFocused || setupBrowser.style.display === "none") return;
      const tag = document.activeElement?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (event.key.length === 1) {
        event.preventDefault();
        await setupAction("api/setup/type", {text: event.key});
        return;
      }
      if (["Enter", "Tab", "Backspace", "Escape"].includes(event.key)) {
        event.preventDefault();
        await setupAction("api/setup/key", {key: event.key});
      }
    });

    document.addEventListener("paste", async (event) => {
      if (!setupFocused || setupBrowser.style.display === "none") return;
      const text = event.clipboardData?.getData("text") || "";
      if (!text) return;
      event.preventDefault();
      await setupAction("api/setup/type", {text});
    });

    document.getElementById("check-alexa").addEventListener("click", async () => {
      setMessage("Pruefe Amazon-Session...");
      const res = await fetch("api/alexa/status");
      const data = await res.json();
      setMessage(data.message, data.authenticated ? "ok" : "error");
    });

    modeInputs.forEach((input) => input.addEventListener("change", applyModeVisibility));

    loadConfig().catch((err) => setMessage(err.message, "error"));
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())
