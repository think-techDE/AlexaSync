"""Alexa client implementations for the Alexa Sync add-on."""

from __future__ import annotations

import json
import logging
import pickle
import re
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from settings import (
    ALEXA_ACCOUNTS_DIR,
    ALEXA_COOKIES_PATH,
    DEFAULT_ACCOUNT_ID,
    HA_CONFIG_PATHS,
    STATUS_NEEDS_ACTION,
)
from ha_client import TodoItem

LOGGER = logging.getLogger("alexa_sync")


# ---------------------------------------------------------------------------
# Account ID helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Cookie persistence
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Amazon domain helpers
# ---------------------------------------------------------------------------

def normalize_amazon_domain(value: Any) -> str:
    """Normalize Amazon marketplace domain input."""
    domain = str(value or "amazon.de").strip().lower()
    domain = re.sub(r"^https?://", "", domain)
    domain = domain.split("/", 1)[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain or "amazon.de"


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


# ---------------------------------------------------------------------------
# Cookie extraction from Alexa Media Player pickle files
# ---------------------------------------------------------------------------

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
        name = name[len("alexa_media."):]
    elif name.startswith("alexa_media_"):
        name = name[len("alexa_media_"):]
    for suffix in (".pickle", ".pkl"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.replace("_", " ").strip() or path.stem


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
    for candidate in sorted(
        raw_alexa_media_session_files(),
        key=lambda path: sort_session_file(path, active_identities),
    ):
        session_keys = alexa_media_session_identity_keys(candidate)
        identity_key = preferred_identity_key(session_keys, candidate.stem)
        if identity_key in deduped_identities:
            continue
        deduped_identities.add(identity_key)
        filtered.append(candidate)
    return filtered


def allow_modern_cookie_attributes() -> None:
    """Allow unpickling AMP cookies with browser attributes missing in older Python."""
    from http.cookies import Morsel

    extra_attributes = {
        "partitioned": "Partitioned",
        "priority": "Priority",
        "sameparty": "SameParty",
    }
    for key, label in extra_attributes.items():
        Morsel._reserved.setdefault(key, label)
    if isinstance(getattr(Morsel, "_flags", None), set):
        Morsel._flags.update({"partitioned", "sameparty"})


def load_alexa_media_cookie_pickle(path: Path) -> Any:
    """Load an Alexa Media Player cookie pickle with cookie attribute compatibility."""
    allow_modern_cookie_attributes()
    with path.open("rb") as cookie_file:
        return pickle.load(cookie_file)


def is_cookie_dict(value: Any) -> bool:
    """Return whether a value already looks like a browser cookie dict."""
    return isinstance(value, dict) and "name" in value and "value" in value


def is_cookie_object(value: Any) -> bool:
    """Return whether a value looks like an http.cookiejar.Cookie."""
    return all(hasattr(value, attr) for attr in ("name", "value", "domain", "path"))


def is_morsel(value: Any) -> bool:
    """Return whether a value looks like http.cookies.Morsel."""
    return hasattr(value, "key") and hasattr(value, "value") and hasattr(value, "__getitem__")


def is_scalar_cookie_value(value: Any) -> bool:
    """Return whether a value can be stored as one cookie value."""
    return isinstance(value, (str, int, float, bool))


def is_plain_cookie_mapping(value: dict[Any, Any]) -> bool:
    """Return whether a dict is a simple cookie-name to cookie-value mapping."""
    if not value:
        return False
    metadata_keys = {
        "domain",
        "expires",
        "expiry",
        "httponly",
        "max-age",
        "maxage",
        "path",
        "samesite",
        "secure",
    }
    keys = {str(key).lower() for key in value}
    if keys and keys <= metadata_keys:
        return False
    return all(isinstance(key, str) and is_scalar_cookie_value(child) for key, child in value.items())


def safe_morsel_get(value: Any, key: str) -> str:
    """Read a Morsel attribute without coupling to its concrete type."""
    try:
        result = value[key]
    except Exception:
        return ""
    return str(result or "")


def cookie_flag(value: Any) -> bool:
    """Normalize cookie boolean flags from bools and cookie attributes."""
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"", "0", "false", "none", "no"}:
        return False
    return True


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


def parse_cookie_expiry(value: Any, max_age: Any = None) -> int | None:
    """Parse cookie expiry values accepted by Selenium."""
    if max_age not in (None, ""):
        try:
            return int(time.time()) + int(max_age)
        except (TypeError, ValueError):
            pass

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
        max_age: Any = None,
        same_site: Any = "",
    ) -> None:
        cookie_name = str(name or "").strip()
        if not cookie_name or value is None:
            return

        domain = normalize_cookie_domain(domain_hint, normalized_amazon_domain)
        if not domain:
            return
        path = str(path_hint or "/")
        expiry = parse_cookie_expiry(expires, max_age)
        if expiry is not None and expiry <= int(time.time()):
            return

        cookie: dict[str, Any] = {
            "name": cookie_name,
            "value": str(value),
            "domain": domain,
            "path": path,
            "secure": cookie_flag(secure),
            "httpOnly": cookie_flag(http_only),
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
                value.get("maxAge", value.get("max-age")),
                value.get("sameSite", value.get("samesite", "")),
            )
            return

        if is_cookie_object(value):
            rest = getattr(value, "_rest", {}) or {}
            http_only = "HttpOnly" in rest or "httponly" in rest
            add_cookie(
                getattr(value, "name", ""),
                getattr(value, "value", ""),
                getattr(value, "domain", "") or domain_hint,
                getattr(value, "path", "") or path_hint,
                getattr(value, "secure", False),
                http_only,
                getattr(value, "expires", None),
                None,
                rest.get("SameSite", rest.get("samesite", "")),
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
                safe_morsel_get(value, "expires"),
                safe_morsel_get(value, "max-age"),
                safe_morsel_get(value, "samesite"),
            )
            return

        if isinstance(value, dict):
            if is_plain_cookie_mapping(value):
                for key, child in value.items():
                    add_cookie(key, child, domain_hint, path_hint)
                return

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
            raw_cookie_data = load_alexa_media_cookie_pickle(session_file)
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


def import_selected_alexa_media_sessions(settings: dict[str, Any], source_names: list[str] | None) -> dict[str, Any]:
    """Import selected Alexa Media Player sessions and create account entries."""
    from settings import SETTINGS_PATH, normalize_settings, write_json_file

    selected_names = {str(name) for name in source_names or [] if str(name).strip()}
    session_files = find_alexa_media_session_files()
    if selected_names:
        session_files = [path for path in session_files if path.name in selected_names]
    else:
        active_identities = active_alexa_media_identities()
        active_sessions = [
            path
            for path in session_files
            if session_matches_active_identity(path, active_identities)
        ]
        session_files = active_sessions or session_files

    if not session_files:
        raise RuntimeError("Keine importierbare Alexa-Media-Player-Session gefunden.")

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
            raw_cookie_data = load_alexa_media_cookie_pickle(session_file)
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


# ---------------------------------------------------------------------------
# InternalAlexaClient
# ---------------------------------------------------------------------------

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
        from ha_client import normalize_summary

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
