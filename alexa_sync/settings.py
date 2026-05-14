"""Settings, file I/O, and logging helpers for the Alexa Sync add-on."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

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

DEFAULT_SETTINGS: dict[str, Any] = {
    "mode": "internal_alexa",
    "amazon_domain": "amazon.de",
    "amazon_accounts": [],
    "ha_list": "",
    "interval_seconds": 120,
    "sync_completed": True,
    "remove_completed": False,
    "log_level": "info",
}

LOGGER = logging.getLogger("alexa_sync")


def setup_logging(level: str) -> None:
    """Configure logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)


def parse_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Parse bounded integer settings."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


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


def load_options() -> dict[str, Any]:
    """Load add-on options as initial defaults."""
    return read_json_file(OPTIONS_PATH, DEFAULT_SETTINGS)


def normalize_amazon_domain(value: Any) -> str:
    """Normalize Amazon marketplace domain input."""
    import re
    domain = str(value or "amazon.de").strip().lower()
    domain = re.sub(r"^https?://", "", domain)
    domain = domain.split("/", 1)[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain or "amazon.de"


def normalize_amazon_accounts(raw_accounts: Any, legacy_domain: str) -> list[dict[str, Any]]:
    """Normalize configured Amazon accounts."""
    from alexa_client import unique_account_id  # avoid circular import at module level
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


def normalize_settings(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize user settings."""
    settings = dict(DEFAULT_SETTINGS)
    settings.update(raw)
    settings["mode"] = "internal_alexa"
    settings["amazon_domain"] = normalize_amazon_domain(settings.get("amazon_domain", "amazon.de"))
    settings["amazon_accounts"] = normalize_amazon_accounts(
        settings.get("amazon_accounts"),
        settings["amazon_domain"],
    )
    if settings["amazon_accounts"]:
        settings["amazon_domain"] = settings["amazon_accounts"][0]["amazon_domain"]
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


def persist_amazon_account(settings: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    """Persist one Amazon account after a successful session import."""
    from alexa_client import account_cookie_path, sanitize_account_id  # avoid circular import

    if not isinstance(account, dict):
        raise ValueError("Amazon-Konto fehlt.")

    normalized_account = normalize_amazon_accounts([account], settings["amazon_domain"])[0]
    normalized_account["enabled"] = True

    accounts = [
        dict(existing)
        for existing in settings.get("amazon_accounts", [])
        if isinstance(existing, dict)
    ]

    if (
        len(accounts) == 1
        and sanitize_account_id(accounts[0].get("id")) == DEFAULT_ACCOUNT_ID
        and normalized_account["id"] != DEFAULT_ACCOUNT_ID
        and not account_cookie_path(DEFAULT_ACCOUNT_ID).exists()
    ):
        accounts = []

    target_id = normalized_account["id"]
    for index, existing in enumerate(accounts):
        if sanitize_account_id(existing.get("id")) == target_id:
            merged = dict(existing)
            merged.update(normalized_account)
            merged["enabled"] = True
            accounts[index] = merged
            break
    else:
        accounts.append(normalized_account)

    next_settings = dict(settings)
    next_settings["amazon_domain"] = normalized_account["amazon_domain"]
    next_settings["amazon_accounts"] = accounts
    normalized = normalize_settings(next_settings)
    write_json_file(SETTINGS_PATH, normalized)
    return normalized


def enabled_amazon_accounts(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Return enabled Amazon accounts from settings."""
    return [account for account in settings.get("amazon_accounts", []) if account.get("enabled", True)]


def validate_settings(settings: dict[str, Any]) -> None:
    """Validate settings."""
    if not enabled_amazon_accounts(settings):
        raise ValueError("Bitte mindestens ein Amazon-Konto aktivieren.")
    for account in enabled_amazon_accounts(settings):
        if not account["amazon_domain"]:
            raise ValueError(f"Bitte Amazon-Domain fuer {account['name']} eintragen.")
    if not settings["ha_list"]:
        raise ValueError("Bitte eine Home-Assistant-Liste auswaehlen.")


def is_configured(settings: dict[str, Any]) -> bool:
    """Return if sync lists are configured."""
    return get_configuration_error(settings) is None


def get_configuration_error(settings: dict[str, Any]) -> str | None:
    """Return a user-facing configuration error, if any."""
    from alexa_client import account_cookie_path
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


def load_state() -> dict[str, Any]:
    """Load persistent sync state."""
    return read_json_file(STATE_PATH, {"items": {}})


def save_state(state: dict[str, Any]) -> None:
    """Persist sync state."""
    write_json_file(STATE_PATH, state)
