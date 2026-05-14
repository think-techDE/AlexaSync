"""Alexa Sync Home Assistant add-on."""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
import pickle
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
ALEXA_ACCOUNTS_DIR = Path("/data/alexa_accounts")
HA_CONFIG_PATHS = (Path("/homeassistant"), Path("/config"))
WEB_PORT = 8099

STATUS_NEEDS_ACTION = "needs_action"
STATUS_COMPLETED = "completed"
DEFAULT_ACCOUNT_ID = "default"

DEFAULT_SETTINGS = {
    "mode": "internal_alexa",
    "amazon_domain": "amazon.de",
    "amazon_accounts": [],
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
                LOGGER.exception("Sync pass failed")
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
        return {"saved": True, "cookie_count": len(cookies), "account_id": self.setup_account["id"]}


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
    logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)


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


def save_cookie_list(cookies: list[dict[str, Any]], path: Path | None = None) -> None:
    """Persist Amazon cookies with owner-only permissions where supported."""
    target_path = path or ALEXA_COOKIES_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as cookie_file:
        json.dump(cookies, cookie_file, ensure_ascii=True, indent=2)
    tmp_path.replace(target_path)
    try:
        target_path.chmod(0o600)
    except OSError:
        LOGGER.debug("Could not chmod cookie file", exc_info=True)


def sanitize_account_id(value: Any) -> str:
    """Return a stable filesystem-safe Amazon account id."""
    account_id = str(value or "").strip().casefold()
    account_id = re.sub(r"[^a-z0-9_@.-]+", "_", account_id)
    account_id = re.sub(r"_+", "_", account_id).strip("._-")
    return account_id or DEFAULT_ACCOUNT_ID


def unique_account_id(base_id: str, used_ids: set[str]) -> str:
    """Return an unused account id based on base_id."""
    candidate = sanitize_account_id(base_id)
    if candidate not in used_ids:
        used_ids.add(candidate)
        return candidate
    suffix = 2
    while f"{candidate}_{suffix}" in used_ids:
        suffix += 1
    unique_id = f"{candidate}_{suffix}"
    used_ids.add(unique_id)
    return unique_id


def account_cookie_path(account_id: str) -> Path:
    """Return cookie storage path for an Amazon account."""
    safe_id = sanitize_account_id(account_id)
    if safe_id == DEFAULT_ACCOUNT_ID:
        return ALEXA_COOKIES_PATH
    return ALEXA_ACCOUNTS_DIR / f"{safe_id}.json"


def read_cookie_count(path: Path) -> int:
    """Return number of stored cookies or zero if unreadable."""
    if not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8") as cookie_file:
            cookies = json.load(cookie_file)
    except (OSError, json.JSONDecodeError):
        return 0
    return len(cookies) if isinstance(cookies, list) else 0


def identity_keys_from_text(value: Any) -> set[str]:
    """Return normalized identity keys for Alexa Media Player account labels."""
    text = str(value or "").strip().casefold()
    if not text:
        return set()
    text = re.sub(r"\s+", " ", text)
    keys = {text}
    if " - " in text:
        keys.add(text.split(" - ", 1)[0].strip())
    for email in re.findall(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", text):
        keys.add(email)
    return {key for key in keys if key}


def alexa_media_session_identity_keys(path: Path) -> set[str]:
    """Return identity keys for an Alexa Media Player session file."""
    return identity_keys_from_text(alexa_media_session_label(path))


def preferred_identity_key(keys: set[str], fallback: str) -> str:
    """Pick a stable identity key, preferring email addresses."""
    emails = sorted(key for key in keys if "@" in key)
    if emails:
        return emails[0]
    if keys:
        return sorted(keys)[0]
    return fallback.casefold()


def alexa_media_config_entries() -> list[dict[str, Any]]:
    """Return Alexa Media Player config entries from Home Assistant storage."""
    config_entries: list[dict[str, Any]] = []
    seen_entries: set[str] = set()
    for base_path in HA_CONFIG_PATHS:
        entries_path = base_path / ".storage" / "core.config_entries"
        if not entries_path.exists():
            continue
        try:
            with entries_path.open("r", encoding="utf-8") as entries_file:
                data = json.load(entries_file)
        except (OSError, json.JSONDecodeError):
            LOGGER.debug("Could not read %s", entries_path, exc_info=True)
            continue
        entries = data.get("data", {}).get("entries", [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("domain") != "alexa_media":
                continue
            entry_id = str(entry.get("entry_id") or entry.get("title") or "")
            if entry_id in seen_entries:
                continue
            seen_entries.add(entry_id)

            identities = identity_keys_from_text(entry.get("title"))
            entry_data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
            for key in ("email", "username", "account", "account_email"):
                identities.update(identity_keys_from_text(entry_data.get(key)))
            config_entries.append(
                {
                    "entry_id": entry_id,
                    "title": str(entry.get("title") or "Alexa Media Player"),
                    "disabled": bool(entry.get("disabled_by")),
                    "identities": sorted(identities),
                }
            )
    return config_entries


def active_alexa_media_identities() -> set[str]:
    """Return identities from active Alexa Media Player config entries."""
    identities: set[str] = set()
    for entry in alexa_media_config_entries():
        if not entry.get("disabled"):
            identities.update(entry.get("identities", []))
    return identities


def raw_alexa_media_session_files() -> list[Path]:
    """Find all Alexa Media Player cookie jars in the mounted HA config directory."""
    found: list[Path] = []
    seen: set[Path] = set()
    for base_path in HA_CONFIG_PATHS:
        storage_path = base_path / ".storage"
        if not storage_path.is_dir():
            continue
        for pattern in ("alexa_media*.pickle", "alexa_media*.pkl"):
            try:
                candidates = storage_path.glob(pattern)
                for candidate in candidates:
                    if candidate.is_file() and candidate not in seen:
                        found.append(candidate)
                        seen.add(candidate)
            except OSError:
                LOGGER.debug("Could not scan %s", storage_path, exc_info=True)
    return sorted(found, key=lambda path: safe_mtime(path), reverse=True)


def session_matches_active_identity(path: Path, active_identities: set[str]) -> bool:
    """Return whether a session file belongs to an active AMP config entry."""
    return bool(alexa_media_session_identity_keys(path) & active_identities)


def sort_session_file(path: Path, active_identities: set[str]) -> tuple[int, float]:
    """Sort active sessions before unmatched sessions, newest first."""
    return (0 if session_matches_active_identity(path, active_identities) else 1, -safe_mtime(path))


def find_alexa_media_session_files() -> list[Path]:
    """Return deduplicated Alexa Media Player cookie jars."""
    active_identities = active_alexa_media_identities()
    filtered: list[Path] = []
    deduped_identities: set[str] = set()
    for candidate in sorted(raw_alexa_media_session_files(), key=lambda path: sort_session_file(path, active_identities)):
        session_keys = alexa_media_session_identity_keys(candidate)
        identity_key = preferred_identity_key(session_keys, candidate.stem)
        if identity_key in deduped_identities:
            continue
        deduped_identities.add(identity_key)
        filtered.append(candidate)
    return filtered


def safe_mtime(path: Path) -> float:
    """Return file mtime or zero if it cannot be read."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0


def alexa_media_session_label(path: Path) -> str:
    """Derive a readable account label from an Alexa Media Player pickle name."""
    name = path.name
    if name.startswith("alexa_media."):
        name = name[len("alexa_media.") :]
    elif name.startswith("alexa_media_"):
        name = name[len("alexa_media_") :]
    for suffix in (".pickle", ".pkl"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.replace("_", " ").strip() or path.stem


def import_alexa_media_session(
    amazon_domain: str,
    *,
    account_id: str = DEFAULT_ACCOUNT_ID,
    source_name: str | None = None,
) -> dict[str, Any]:
    """Import cookies from Alexa Media Player's local cookie pickle."""
    if not source_name:
        raise RuntimeError("Bitte eine Alexa-Media-Player-Session auswaehlen.")
    session_files = find_alexa_media_session_files()
    session_files = [path for path in session_files if path.name == source_name]
    if not session_files:
        raise RuntimeError("Ausgewaehlte Alexa-Media-Player-Session nicht gefunden.")

    errors: list[str] = []
    cookie_path = account_cookie_path(account_id)
    for session_file in session_files:
        try:
            # Alexa Media Player stores an aiohttp cookie jar pickle in HA's
            # local .storage directory. The add-on mounts this directory read-only.
            with session_file.open("rb") as cookie_file:
                raw_cookie_data = pickle.load(cookie_file)
            cookies = extract_alexa_media_cookies(raw_cookie_data, amazon_domain)
        except Exception as exc:
            LOGGER.warning("Could not import Alexa Media Player session from %s", session_file.name)
            errors.append(f"{session_file.name}: {exc}")
            continue

        if not cookies:
            errors.append(f"{session_file.name}: keine Amazon-Cookies gefunden")
            continue

        save_cookie_list(cookies, cookie_path)
        LOGGER.info("Imported %s cookies from Alexa Media Player session %s", len(cookies), session_file.name)
        return {"saved": True, "cookie_count": len(cookies), "source": session_file.name}

    details = f" Details: {'; '.join(errors[:3])}" if errors else ""
    raise RuntimeError(f"Keine nutzbare Alexa-Media-Player-Session gefunden.{details}")


def import_selected_alexa_media_sessions(settings: dict[str, Any], source_names: list[str]) -> dict[str, Any]:
    """Import selected Alexa Media Player sessions and create account entries."""
    selected_names = {str(name) for name in source_names if str(name).strip()}
    if not selected_names:
        raise RuntimeError("Bitte mindestens eine Alexa-Media-Player-Session auswaehlen.")

    session_files = [path for path in find_alexa_media_session_files() if path.name in selected_names]
    if not session_files:
        raise RuntimeError("Keine der ausgewaehlten Alexa-Media-Player-Sessions wurde gefunden.")

    existing_accounts = list(settings.get("amazon_accounts") or [])
    if (
        len(existing_accounts) == 1
        and existing_accounts[0].get("id") == DEFAULT_ACCOUNT_ID
        and not account_cookie_path(DEFAULT_ACCOUNT_ID).exists()
    ):
        existing_accounts = []
    used_ids = {sanitize_account_id(account.get("id")) for account in existing_accounts if isinstance(account, dict)}
    accounts_by_id = {
        sanitize_account_id(account.get("id")): dict(account)
        for account in existing_accounts
        if isinstance(account, dict)
    }
    imported: list[dict[str, Any]] = []
    errors: list[str] = []

    for session_file in session_files:
        label = alexa_media_session_label(session_file)
        base_id = sanitize_account_id(label)
        account_id = base_id if base_id in accounts_by_id else unique_account_id(base_id, used_ids)
        try:
            with session_file.open("rb") as cookie_file:
                raw_cookie_data = pickle.load(cookie_file)
            cookies = extract_alexa_media_cookies(raw_cookie_data, settings["amazon_domain"])
        except Exception as exc:
            LOGGER.warning("Could not import Alexa Media Player session from %s", session_file.name)
            errors.append(f"{session_file.name}: {exc}")
            continue

        if not cookies:
            errors.append(f"{session_file.name}: keine Amazon-Cookies gefunden")
            continue

        save_cookie_list(cookies, account_cookie_path(account_id))
        account = accounts_by_id.get(account_id) or {}
        account.update(
            {
                "id": account_id,
                "name": account.get("name") or label,
                "amazon_domain": account.get("amazon_domain") or infer_amazon_domain(cookies, settings["amazon_domain"]),
                "enabled": True,
            }
        )
        accounts_by_id[account_id] = account
        imported.append({"account_id": account_id, "name": account["name"], "source": session_file.name})

    if not imported:
        details = f" Details: {'; '.join(errors[:3])}" if errors else ""
        raise RuntimeError(f"Keine nutzbare Alexa-Media-Player-Session gefunden.{details}")

    settings["amazon_accounts"] = normalize_amazon_accounts(list(accounts_by_id.values()), settings["amazon_domain"])
    normalized = normalize_settings(settings)
    write_json_file(SETTINGS_PATH, normalized)
    return {"imported": imported, "errors": errors, "settings": normalized}


def infer_amazon_domain(cookies: list[dict[str, Any]], fallback: str) -> str:
    """Infer marketplace domain from imported cookies."""
    candidates: list[str] = []
    for cookie in cookies:
        domain = str(cookie.get("domain") or "").strip().lower().lstrip(".")
        if domain.startswith("www."):
            domain = domain[4:]
        if domain.startswith("alexa."):
            domain = domain[6:]
        if domain.startswith("amazon."):
            candidates.append(domain)
    if not candidates:
        return normalize_amazon_domain(fallback)
    return sorted(candidates, key=len)[0]


def extract_alexa_media_cookies(raw_cookie_data: Any, amazon_domain: str) -> list[dict[str, Any]]:
    """Convert Alexa Media Player/aiohttp cookie storage to Selenium cookies."""
    cookies_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    normalized_amazon_domain = normalize_amazon_domain(amazon_domain)

    def add_cookie(
        name: Any,
        value: Any,
        domain_hint: Any = "",
        path_hint: Any = "/",
        secure: Any = False,
        http_only: Any = False,
        expires: Any = None,
        same_site: Any = "",
    ) -> None:
        cookie_name = str(name or "").strip()
        if not cookie_name or value is None:
            return

        domain = normalize_cookie_domain(domain_hint, normalized_amazon_domain)
        if not domain:
            return
        path = str(path_hint or "/")
        expiry = parse_cookie_expiry(expires)
        if expiry is not None and expiry <= int(time.time()):
            return

        cookie: dict[str, Any] = {
            "name": cookie_name,
            "value": str(value),
            "domain": domain,
            "path": path,
            "secure": bool(secure),
            "httpOnly": bool(http_only),
        }
        if expiry is not None:
            cookie["expiry"] = expiry

        normalized_same_site = normalize_same_site(same_site)
        if normalized_same_site:
            cookie["sameSite"] = normalized_same_site

        cookies_by_key[(domain, path, cookie_name)] = cookie

    def collect(value: Any, domain_hint: Any = "", path_hint: Any = "/") -> None:
        if value is None:
            return

        if is_cookie_dict(value):
            add_cookie(
                value.get("name"),
                value.get("value"),
                value.get("domain") or domain_hint,
                value.get("path") or path_hint,
                value.get("secure", False),
                value.get("httpOnly", value.get("httponly", False)),
                value.get("expiry", value.get("expires")),
                value.get("sameSite", value.get("samesite", "")),
            )
            return

        if is_morsel(value):
            add_cookie(
                getattr(value, "key", ""),
                getattr(value, "value", ""),
                safe_morsel_get(value, "domain") or domain_hint,
                safe_morsel_get(value, "path") or path_hint,
                safe_morsel_get(value, "secure"),
                safe_morsel_get(value, "httponly"),
                safe_morsel_get(value, "expires") or safe_morsel_get(value, "max-age"),
                safe_morsel_get(value, "samesite"),
            )
            return

        if isinstance(value, dict):
            for key, child in value.items():
                next_domain, next_path = cookie_hints_from_key(key, domain_hint, path_hint)
                collect(child, next_domain, next_path)
            return

        if isinstance(value, (list, tuple, set)):
            for child in value:
                collect(child, domain_hint, path_hint)
            return

        if hasattr(value, "__iter__") and not isinstance(value, (str, bytes)):
            try:
                for child in value:
                    collect(child, domain_hint, path_hint)
            except TypeError:
                return

    collect(raw_cookie_data)
    return list(cookies_by_key.values())


def is_cookie_dict(value: Any) -> bool:
    """Return whether a value already looks like a browser cookie dict."""
    return isinstance(value, dict) and "name" in value and "value" in value


def is_morsel(value: Any) -> bool:
    """Return whether a value looks like http.cookies.Morsel."""
    return hasattr(value, "key") and hasattr(value, "value") and hasattr(value, "__getitem__")


def safe_morsel_get(value: Any, key: str) -> str:
    """Read a Morsel attribute without coupling to its concrete type."""
    try:
        result = value[key]
    except Exception:
        return ""
    return str(result or "")


def cookie_hints_from_key(key: Any, domain_hint: Any, path_hint: Any) -> tuple[Any, Any]:
    """Derive domain/path hints from aiohttp CookieJar storage keys."""
    if isinstance(key, tuple) and len(key) >= 2:
        return key[0] or domain_hint, key[1] or path_hint
    if isinstance(key, str) and "amazon." in key.lower():
        return key, path_hint
    return domain_hint, path_hint


def normalize_cookie_domain(domain_hint: Any, amazon_domain: str) -> str:
    """Normalize and restrict cookies to Amazon domains."""
    domain = str(domain_hint or "").strip().lower()
    domain = re.sub(r"^https?://", "", domain)
    domain = domain.split("/", 1)[0]
    if not domain:
        domain = f".{amazon_domain}"
    if "amazon." not in domain:
        return ""
    return domain


def parse_cookie_expiry(value: Any) -> int | None:
    """Parse cookie expiry values accepted by Selenium."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text.startswith("-"):
        return None
    try:
        return int(parsedate_to_datetime(text).timestamp())
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def normalize_same_site(value: Any) -> str | None:
    """Normalize SameSite to Selenium's accepted values."""
    text = str(value or "").strip().lower()
    if text in {"strict", "lax", "none"}:
        return text.capitalize()
    return None


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


def normalize_amazon_accounts(raw_accounts: Any, legacy_domain: str) -> list[dict[str, Any]]:
    """Normalize configured Amazon accounts."""
    if not isinstance(raw_accounts, list) or not raw_accounts:
        raw_accounts = [
            {
                "id": DEFAULT_ACCOUNT_ID,
                "name": "Amazon Konto 1",
                "amazon_domain": legacy_domain,
                "enabled": True,
            }
        ]

    accounts: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, raw_account in enumerate(raw_accounts, start=1):
        raw = raw_account if isinstance(raw_account, dict) else {}
        base_id = raw.get("id") or raw.get("name") or f"amazon_konto_{index}"
        account_id = unique_account_id(str(base_id), used_ids)
        name = str(raw.get("name") or f"Amazon Konto {index}").strip()
        domain = normalize_amazon_domain(raw.get("amazon_domain") or raw.get("domain") or legacy_domain)
        accounts.append(
            {
                "id": account_id,
                "name": name or f"Amazon Konto {index}",
                "amazon_domain": domain,
                "enabled": bool(raw.get("enabled", True)),
            }
        )
    return accounts


def enabled_amazon_accounts(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Return enabled Amazon accounts from settings."""
    return [account for account in settings.get("amazon_accounts", []) if account.get("enabled", True)]


def load_options() -> dict[str, Any]:
    """Load add-on options as initial defaults."""
    return read_json_file(OPTIONS_PATH, DEFAULT_SETTINGS)


def normalize_settings(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize user settings."""
    settings = dict(DEFAULT_SETTINGS)
    settings.update(raw)
    settings["mode"] = str(settings.get("mode", "internal_alexa")).strip()
    settings["amazon_domain"] = normalize_amazon_domain(settings.get("amazon_domain", "amazon.de"))
    settings["amazon_accounts"] = normalize_amazon_accounts(
        settings.get("amazon_accounts"),
        settings["amazon_domain"],
    )
    if settings["amazon_accounts"]:
        settings["amazon_domain"] = settings["amazon_accounts"][0]["amazon_domain"]
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
        if not enabled_amazon_accounts(settings):
            raise ValueError("Bitte mindestens ein Amazon-Konto aktivieren.")
        for account in enabled_amazon_accounts(settings):
            if not account["amazon_domain"]:
                raise ValueError(f"Bitte Amazon-Domain fuer {account['name']} eintragen.")
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
    return get_configuration_error(settings) is None


def get_configuration_error(settings: dict[str, Any]) -> str | None:
    """Return a user-facing configuration error, if any."""
    if settings["mode"] == "internal_alexa":
        accounts = enabled_amazon_accounts(settings)
        if not accounts:
            return "Bitte mindestens ein Amazon-Konto aktivieren."
        if not settings["ha_list"]:
            return "Bitte Bring-/Ziel-Liste auswaehlen."
        missing_sessions = [
            account["name"]
            for account in accounts
            if not account_cookie_path(account["id"]).exists()
        ]
        if missing_sessions:
            return "Amazon-Session fehlt fuer: " + ", ".join(missing_sessions)
        return None
    if settings["mode"] == "alexa_server":
        if not settings["alexa_server_host"] or not settings["ha_list"]:
            return "Bitte Alexa-Server und Home-Assistant-Liste auswaehlen."
        return None
    if not settings["list_a"] or not settings["list_b"]:
        return "Bitte beide Listen auswaehlen."
    if settings["list_a"] == settings["list_b"]:
        return "Bitte zwei unterschiedliche Listen auswaehlen."
    return None


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

    def remove_item(self, item: TodoItem) -> bool:
        """Remove an item from Alexa."""
        self.command("remove_item", {"item": item.summary})
        return True


class InternalAlexaClient:
    """Selenium-backed Alexa shopping list client."""

    def __init__(self, amazon_domain: str, cookie_path: Path | None = None) -> None:
        """Initialize client."""
        self.amazon_domain = amazon_domain
        self.cookie_path = cookie_path or ALEXA_COOKIES_PATH
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
        if not self.cookie_path.exists():
            raise RuntimeError("Amazon-Session fehlt. Bitte Cookies in der Weboberflaeche importieren.")

        self.driver.get(f"https://www.{self.amazon_domain}")
        with self.cookie_path.open("r", encoding="utf-8") as cookie_file:
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

    def remove_item(self, item: TodoItem) -> bool:
        """Remove an item from Alexa active list."""
        from selenium.common.exceptions import (
            ElementClickInterceptedException,
            NoSuchElementException,
            StaleElementReferenceException,
        )

        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                return self._remove_item_once(item)
            except (StaleElementReferenceException, NoSuchElementException, ElementClickInterceptedException) as exc:
                last_error = exc
                LOGGER.debug("Retrying Alexa remove for '%s' after DOM change", item.summary, exc_info=True)
                time.sleep(attempt)
        LOGGER.warning("Could not remove '%s' from Alexa after retries: %s", item.summary, last_error)
        return False

    def _remove_item_once(self, item: TodoItem) -> bool:
        """Remove an item once, returning whether a row was clicked."""
        from selenium.webdriver.common.by import By
        from selenium.common.exceptions import ElementClickInterceptedException, NoSuchElementException

        if self.driver is None:
            raise RuntimeError("Browser is not running")
        self._open_list()
        last_text = None
        stable_rounds = 0

        while stable_rounds < 2:
            list_container = self.driver.find_element(By.CLASS_NAME, "virtual-list")
            rows = list_container.find_elements(By.CLASS_NAME, "inner")
            for row in rows:
                try:
                    title = row.find_element(By.CLASS_NAME, "item-title").get_attribute("innerText").strip()
                except NoSuchElementException:
                    continue
                if normalize_summary(title) == normalize_summary(item.summary):
                    button = row.find_element(By.CLASS_NAME, "item-actions-2").find_element(By.TAG_NAME, "button")
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
                    time.sleep(0.2)
                    try:
                        button.click()
                    except ElementClickInterceptedException:
                        self.driver.execute_script("arguments[0].click();", button)
                    time.sleep(1)
                    return True

            current_last = rows[-1].text if rows else None
            if current_last == last_text:
                stable_rounds += 1
            else:
                stable_rounds = 0
            last_text = current_last

            if rows:
                self.driver.execute_script("arguments[0].scrollIntoView();", rows[-1])
            time.sleep(1)
        return False


def resolve_account_from_payload(settings: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve an Amazon account from API payload or existing settings."""
    raw_account = payload.get("account")
    if isinstance(raw_account, dict):
        return normalize_amazon_accounts([raw_account], settings["amazon_domain"])[0]

    account_id = sanitize_account_id(payload.get("account_id") or DEFAULT_ACCOUNT_ID)
    for account in settings.get("amazon_accounts", []):
        if account["id"] == account_id:
            return account
    raise ValueError("Amazon-Konto nicht gefunden. Bitte speichern und erneut versuchen.")


def amazon_account_statuses(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Return cookie/session status for configured Amazon accounts."""
    statuses: list[dict[str, Any]] = []
    for account in settings.get("amazon_accounts", []):
        cookie_path = account_cookie_path(account["id"])
        cookie_count = read_cookie_count(cookie_path)
        statuses.append(
            {
                "id": account["id"],
                "cookie_present": cookie_path.exists() and cookie_count > 0,
                "cookie_count": cookie_count,
            }
        )
    return statuses


def alexa_media_sessions_payload() -> list[dict[str, Any]]:
    """Return detected Alexa Media Player sessions for the UI."""
    active_identities = active_alexa_media_identities()
    sessions: list[dict[str, Any]] = []
    session_identity_keys: set[str] = set()
    for path in find_alexa_media_session_files():
        identity_keys = alexa_media_session_identity_keys(path)
        identity_key = preferred_identity_key(identity_keys, path.stem)
        is_active = session_matches_active_identity(path, active_identities)
        session_identity_keys.add(identity_key)
        sessions.append(
            {
                "name": path.name,
                "label": alexa_media_session_label(path),
                "mtime": safe_mtime(path),
                "active": is_active,
                "importable": True,
                "status": "Aktiv in Alexa Media Player" if is_active else "Nicht eindeutig aktiv; Import ist moeglich",
            }
        )

    for entry in alexa_media_config_entries():
        if entry.get("disabled"):
            continue
        identity_key = preferred_identity_key(set(entry.get("identities", [])), str(entry.get("title", "")))
        if identity_key in session_identity_keys:
            continue
        sessions.append(
            {
                "name": f"missing:{entry.get('entry_id')}",
                "label": str(entry.get("title") or "Alexa Media Player"),
                "mtime": 0,
                "active": True,
                "importable": False,
                "status": "Aktiv in Alexa Media Player, aber keine Cookie-Datei gefunden",
            }
        )
    return sessions


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
                settings = load_settings()
                account = resolve_account_from_payload(settings, payload)
                cookies = payload.get("cookies")
                if isinstance(cookies, str):
                    cookies = json.loads(cookies)
                if not isinstance(cookies, list):
                    raise ValueError("Cookies muessen als JSON-Liste uebergeben werden.")
                save_cookie_list(cookies, account_cookie_path(account["id"]))
                self.send_json({"ok": True})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/alexa/status":
            try:
                payload = self.read_json()
                settings = load_settings()
                account = resolve_account_from_payload(settings, payload)
                self.send_json(self.get_alexa_status_payload(account))
            except Exception as exc:
                self.send_json({"ok": False, "authenticated": False, "message": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/alexa/import_amp":
            try:
                payload = self.read_json()
                settings = load_settings()
                account = resolve_account_from_payload(settings, payload)
                result = import_alexa_media_session(
                    account["amazon_domain"],
                    account_id=account["id"],
                    source_name=payload.get("source") or None,
                )
                self.send_json({"ok": True, "result": result})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/alexa/import_amp_selected":
            try:
                payload = self.read_json()
                sources = payload.get("sources")
                if not isinstance(sources, list):
                    raise ValueError("Bitte Alexa-Media-Player-Sessions auswaehlen.")
                settings = load_settings()
                result = import_selected_alexa_media_sessions(settings, sources)
                self.send_json({"ok": True, "result": result})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/setup/start":
            try:
                payload = self.read_json()
                settings = load_settings()
                account = resolve_account_from_payload(settings, payload)
                screenshot = self.runtime.start_setup_browser(account)
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
        settings = load_settings()
        account_statuses = amazon_account_statuses(settings)
        alexa_media_sessions = alexa_media_sessions_payload()
        try:
            entities = self.runtime.client.list_todo_entities()
            entity_error = None
        except Exception as exc:
            LOGGER.exception("Failed to list todo entities")
            entities = []
            entity_error = str(exc)

        return {
            "settings": settings,
            "todo_entities": entities,
            "suggested_ha_list": suggest_todo_entity(entities),
            "status": self.runtime.last_result,
            "alexa_cookies_present": any(status["cookie_present"] for status in account_statuses),
            "amazon_accounts_status": account_statuses,
            "alexa_media_session_available": any(session.get("importable") for session in alexa_media_sessions),
            "alexa_media_sessions": alexa_media_sessions,
            "entity_error": entity_error,
        }

    def get_alexa_status_payload(self, account: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return internal Alexa authentication status."""
        settings = load_settings()
        accounts = enabled_amazon_accounts(settings)
        if account is None and not accounts:
            return {"ok": True, "authenticated": False, "message": "Kein Amazon-Konto aktiv."}
        target_account = account or accounts[0]
        cookie_path = account_cookie_path(target_account["id"])
        if not cookie_path.exists():
            return {"ok": True, "authenticated": False, "message": "Keine Cookies importiert."}
        try:
            with InternalAlexaClient(target_account["amazon_domain"], cookie_path) as alexa:
                authenticated = alexa.is_authenticated()
            return {
                "ok": True,
                "authenticated": authenticated,
                "message": (
                    f"Amazon-Session fuer {target_account['name']} ist gueltig."
                    if authenticated
                    else f"Amazon-Session fuer {target_account['name']} ist nicht gueltig."
                ),
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
    normalized = summary.strip().casefold()
    normalized = (
        normalized.replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )
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
    """Synchronize one or more built-in Alexa Selenium clients with one HA list."""
    writes = 0
    errors: list[str] = []
    account_settings = dict(settings)
    account_settings["remove_completed"] = False

    for account in enabled_amazon_accounts(settings):
        account_state = get_internal_account_state(state, account["id"])
        cookie_path = account_cookie_path(account["id"])
        try:
            with InternalAlexaClient(account["amazon_domain"], cookie_path) as alexa:
                if not alexa.is_authenticated():
                    raise RuntimeError("Amazon-Session ist nicht authentifiziert.")
                writes += sync_alexa_items_with_ha(
                    alexa,
                    client,
                    settings["ha_list"],
                    account_settings,
                    account_state,
                    alexa_label=f"Alexa-Liste {account['name']}",
                    save_after=False,
                )
        except Exception as exc:
            LOGGER.exception("Sync failed for Amazon account %s", account["name"])
            errors.append(f"{account['name']}: {exc}")

    state["updated_at"] = time.time()
    save_state(state)

    if errors:
        raise RuntimeError("Sync teilweise fehlgeschlagen: " + "; ".join(errors))

    if settings["remove_completed"]:
        LOGGER.info("Removing completed items from %s", settings["ha_list"])
        client.remove_completed_items(settings["ha_list"])
        writes += 1

    return writes


def get_internal_account_state(state: dict[str, Any], account_id: str) -> dict[str, Any]:
    """Return per-Amazon-account sync state, migrating the legacy state if needed."""
    account_states = state.setdefault("amazon_accounts", {})
    safe_id = sanitize_account_id(account_id)
    if safe_id not in account_states:
        account_states[safe_id] = {"items": {}}
        if safe_id == DEFAULT_ACCOUNT_ID and isinstance(state.get("items"), dict):
            account_states[safe_id]["items"] = state["items"]
    return account_states[safe_id]


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
    save_after: bool = True,
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
            item_state.pop("ha_only_baseline", None)
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
            removed = alexa.remove_item(alexa_item)
            if removed is not False:
                writes += 1

        remember(item_state, alexa_item, ha_item)

    state["updated_at"] = time.time()
    if save_after:
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
    .account-list {
      display: grid;
      gap: 12px;
      margin-top: 14px;
    }
    .account-card {
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
    }
    .account-grid {
      display: grid;
      grid-template-columns: minmax(160px, 1fr) minmax(140px, 1fr);
      gap: 12px;
      align-items: end;
    }
    .account-status {
      color: var(--muted);
      font-size: 13px;
      margin-top: 10px;
    }
    .account-card .actions {
      margin-top: 12px;
    }
    .session-list {
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }
    .session-row {
      display: flex;
      gap: 10px;
      align-items: flex-start;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 9px 10px;
      font-weight: 500;
    }
    .session-row.disabled {
      opacity: 0.72;
    }
    .session-main {
      min-width: 0;
      overflow-wrap: anywhere;
    }
    .session-title {
      display: block;
    }
    .session-status {
      color: var(--muted);
      display: block;
      font-size: 12px;
      margin-top: 2px;
    }
    .badge {
      border: 1px solid var(--border);
      border-radius: 999px;
      display: inline-block;
      font-size: 11px;
      font-weight: 650;
      margin-left: 8px;
      padding: 1px 7px;
      vertical-align: middle;
    }
    .badge.ok { color: var(--ok); }
    .badge.warn { color: var(--danger); }
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
      .grid, .account-grid, dl { grid-template-columns: 1fr; }
      main { margin: 20px auto; }
    }
  </style>
</head>
<body>
  <main>
    <h1>Alexa Sync</h1>
    <p>Alexa per Sprache befuellen, in Bring abhaken. Waehle deine Bring-Liste und verbinde ein oder mehrere Amazon-Konten.</p>

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
          <label for="amazon-domain">Standard-Domain fuer neue Konten</label>
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
        <label>Amazon-Konten</label>
        <div class="actions">
          <button id="import-amp-selected" type="button">Ausgewaehlte aus Alexa Media Player uebernehmen</button>
          <button id="add-account" type="button">Amazon-Konto hinzufuegen</button>
          <button id="setup-save" type="button">Session uebernehmen</button>
          <button id="setup-stop" type="button">Browser schliessen</button>
        </div>
        <p id="amp-status" class="setup-hint"></p>
        <div id="amp-session-list" class="session-list"></div>
        <p class="setup-hint">Jedes aktivierte Amazon-Konto wird mit derselben Bring-/Ziel-Liste synchronisiert. Neue Bring-Eintraege werden in alle aktiven Alexa-Listen geschrieben; erledigte Eintraege werden aus allen aktiven Alexa-Listen entfernt.</p>
        <div id="account-list" class="account-list"></div>
        <div id="setup-browser" class="setup-browser">
          <img id="setup-screenshot" alt="Amazon Login Browser">
        </div>
      </div>
      <div class="internal-alexa-field">
        <label for="cookies">Fallback: Amazon-Session-Cookies als JSON</label>
        <select id="cookie-account"></select>
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
    const accountList = document.getElementById("account-list");
    const cookieAccount = document.getElementById("cookie-account");
    const ampSessionList = document.getElementById("amp-session-list");
    const setupBrowser = document.getElementById("setup-browser");
    const setupScreenshot = document.getElementById("setup-screenshot");
    const ampStatus = document.getElementById("amp-status");
    const modeInputs = [...document.querySelectorAll('input[name="mode"]')];
    let accountStatuses = [];
    let alexaMediaSessions = [];
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

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }[char]));
    }

    function newAccountId() {
      return `konto_${Date.now()}_${Math.random().toString(16).slice(2, 6)}`;
    }

    function statusForAccount(accountId) {
      return accountStatuses.find((status) => status.id === accountId) || {};
    }

    function accountFromCard(card) {
      return {
        id: card.dataset.accountId || newAccountId(),
        name: card.querySelector(".account-name").value.trim() || "Amazon Konto",
        amazon_domain: card.querySelector(".account-domain").value.trim() || amazonDomain.value || "amazon.de",
        enabled: card.querySelector(".account-enabled").checked
      };
    }

    function collectAccounts() {
      return [...accountList.querySelectorAll(".account-card")].map(accountFromCard);
    }

    function renderCookieAccountSelect(accounts) {
      cookieAccount.replaceChildren();
      for (const account of accounts) {
        cookieAccount.appendChild(option(account.id, account.name));
      }
    }

    function renderAlexaMediaSessions() {
      ampSessionList.replaceChildren();
      for (const session of alexaMediaSessions) {
        const label = document.createElement("label");
        label.className = "session-row";
        if (!session.importable) label.classList.add("disabled");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = session.name;
        checkbox.disabled = !session.importable;
        const content = document.createElement("span");
        content.className = "session-main";
        const title = document.createElement("span");
        title.className = "session-title";
        title.textContent = session.label || session.name;
        const badge = document.createElement("span");
        badge.className = `badge ${session.active ? "ok" : "warn"}`;
        badge.textContent = session.importable
          ? (session.active ? "aktiv" : "ungeprueft")
          : "ohne Datei";
        const status = document.createElement("span");
        status.className = "session-status";
        status.textContent = session.status || "";
        title.appendChild(badge);
        content.append(title, status);
        label.append(checkbox, content);
        ampSessionList.appendChild(label);
      }
    }

    function selectedAlexaMediaSessions() {
      return [...ampSessionList.querySelectorAll('input[type="checkbox"]:checked')].map((input) => input.value);
    }

    function renderAccounts(accounts) {
      const normalized = accounts && accounts.length ? accounts : [{
        id: "default",
        name: "Amazon Konto 1",
        amazon_domain: amazonDomain.value || "amazon.de",
        enabled: true
      }];
      accountList.replaceChildren();
      for (const account of normalized) {
        const status = statusForAccount(account.id);
        const statusText = status.cookie_present
          ? `Session gespeichert (${status.cookie_count || 0} Cookies)`
          : "Noch keine Session gespeichert";
        const card = document.createElement("div");
        card.className = "account-card";
        card.dataset.accountId = account.id || newAccountId();
        const sessionOptions = alexaMediaSessions.filter((session) => session.importable).map((session) => (
          `<option value="${escapeHtml(session.name)}">${escapeHtml(session.label || session.name)}</option>`
        )).join("");
        card.innerHTML = `
          <div class="account-grid">
            <label>Kontoname
              <input class="account-name" type="text" value="${escapeHtml(account.name)}">
            </label>
            <label>Amazon-Domain
              <input class="account-domain" type="text" value="${escapeHtml(account.amazon_domain || amazonDomain.value || "amazon.de")}">
            </label>
            <label>Alexa-Media-Player-Session
              <select class="account-session">
                <option value="">Bitte auswaehlen</option>
                ${sessionOptions}
              </select>
            </label>
            <label class="check">
              <input class="account-enabled" type="checkbox" ${account.enabled === false ? "" : "checked"}>
              Konto aktiv
            </label>
          </div>
          <div class="account-status">${escapeHtml(statusText)}</div>
          <div class="actions">
            <button type="button" data-action="import">Session aus Alexa Media Player uebernehmen</button>
            <button type="button" data-action="login">Amazon-Anmeldung oeffnen</button>
            <button type="button" data-action="check">Session pruefen</button>
            <button type="button" data-action="remove">Entfernen</button>
          </div>
        `;
        accountList.appendChild(card);
      }
      renderCookieAccountSelect(normalized);
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
      accountStatuses = data.amazon_accounts_status || [];
      alexaMediaSessions = data.alexa_media_sessions || [];
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
      const importableSessions = alexaMediaSessions.filter((session) => session.importable);
      const missingSessions = alexaMediaSessions.filter((session) => !session.importable);
      ampStatus.textContent = importableSessions.length
        ? `${importableSessions.length} importierbare Alexa-Media-Player-Session(s) gefunden.`
        : "Keine importierbare Alexa-Media-Player-Session gefunden. Der Login-Browser bleibt als Fallback verfuegbar.";
      if (missingSessions.length) {
        ampStatus.textContent += ` ${missingSessions.length} aktive(r) Alexa-Media-Player-Account(s) ohne Cookie-Datei.`;
      }
      renderAlexaMediaSessions();
      renderAccounts(settings.amazon_accounts || []);
      applyModeVisibility();
      renderStatus(data.status);
      if (data.entity_error) setMessage(data.entity_error, "error");
    }

    document.getElementById("config-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const payload = {
        mode: selectedMode(),
        amazon_domain: amazonDomain.value,
        amazon_accounts: collectAccounts(),
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
        body: JSON.stringify({account_id: cookieAccount.value, cookies: cookies.value})
      });
      const data = await res.json();
      setMessage(data.ok ? "Cookies importiert." : data.error, data.ok ? "ok" : "error");
    });

    document.getElementById("import-amp-selected").addEventListener("click", async () => {
      const sources = selectedAlexaMediaSessions();
      if (!sources.length) {
        setMessage("Bitte mindestens eine Alexa-Media-Player-Session auswaehlen.", "error");
        return;
      }
      setMessage("Uebernehme ausgewaehlte Sessions aus Alexa Media Player...");
      const res = await fetch("api/alexa/import_amp_selected", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({sources})
      });
      const data = await res.json();
      if (!data.ok) {
        setMessage(data.error || "Uebernahme fehlgeschlagen", "error");
        return;
      }
      setMessage(`${data.result.imported.length} ausgewaehlte Session(s) uebernommen.`, "ok");
      await loadConfig();
    });

    document.getElementById("add-account").addEventListener("click", () => {
      const accounts = collectAccounts();
      accounts.push({
        id: newAccountId(),
        name: `Amazon Konto ${accounts.length + 1}`,
        amazon_domain: amazonDomain.value || "amazon.de",
        enabled: true
      });
      renderAccounts(accounts);
    });

    accountList.addEventListener("click", async (event) => {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const card = button.closest(".account-card");
      const account = accountFromCard(card);

      if (button.dataset.action === "remove") {
        card.remove();
        renderCookieAccountSelect(collectAccounts());
        return;
      }

      if (button.dataset.action === "import") {
        const source = card.querySelector(".account-session").value;
        if (!source) {
          setMessage("Bitte fuer dieses Konto eine Alexa-Media-Player-Session auswaehlen.", "error");
          return;
        }
        setMessage(`Uebernehme Session fuer ${account.name}...`);
        const res = await fetch("api/alexa/import_amp", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({account, source})
        });
        const data = await res.json();
        if (!data.ok) {
          setMessage(data.error || "Uebernahme fehlgeschlagen", "error");
          return;
        }
        setMessage(`Session fuer ${account.name} uebernommen (${data.result.cookie_count} Cookies).`, "ok");
        await loadConfig();
        return;
      }

      if (button.dataset.action === "login") {
        setMessage(`Oeffne Amazon-Anmeldung fuer ${account.name}...`);
        const data = await setupAction("api/setup/start", {account});
        if (data) setMessage(`Amazon-Anmeldung fuer ${account.name} geoeffnet.`, "ok");
        return;
      }

      if (button.dataset.action === "check") {
        setMessage(`Pruefe Amazon-Session fuer ${account.name}...`);
        const res = await fetch("api/alexa/status", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({account})
        });
        const data = await res.json();
        setMessage(data.message, data.authenticated ? "ok" : "error");
      }
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

    document.getElementById("setup-save").addEventListener("click", async () => {
      const data = await setupAction("api/setup/save");
      if (data) {
        setMessage(`Session gespeichert (${data.result.cookie_count} Cookies).`, "ok");
        await loadConfig();
      }
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

    modeInputs.forEach((input) => input.addEventListener("change", applyModeVisibility));

    loadConfig().catch((err) => setMessage(err.message, "error"));
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())
