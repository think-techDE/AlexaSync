"""Alexa client implementations for the Alexa Sync add-on."""

from __future__ import annotations

import json
import logging
import pickle
import re
import time
import zlib
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from settings import (
    ALEXA_ACCOUNTS_DIR,
    ALEXA_COOKIES_PATH,
    DEFAULT_ACCOUNT_ID,
    HA_CONFIG_PATHS,
    STATUS_NEEDS_ACTION,
)
from ha_client import TodoItem

LOGGER = logging.getLogger("alexa_sync")

HTTP_USER_AGENT = (
    "AppleWebKit PitanguiBridge/2.2.595606.0-"
    "[HARDWARE=iPhone14_7][SOFTWARE=17.4.1][DEVICE=iPhone]"
)
SHOPPING_LIST_ITEM_STATUS_ACTIVE = "ACTIVE"
SHOPPING_LIST_ITEM_STATUS_COMPLETE = "COMPLETE"

CLICK_VISIBLE_SHOPPING_LIST_ITEM_SCRIPT = r"""
const wanted = arguments[0];
const normalize = (value) => {
  let text = String(value || "").trim().toLocaleLowerCase("de-DE");
  const replacements = {
    "\u00e4": "ae",
    "\u00f6": "oe",
    "\u00fc": "ue",
    "\u00df": "ss",
    "\u00c3\u00a4": "ae",
    "\u00c3\u00b6": "oe",
    "\u00c3\u00bc": "ue",
    "\u00c3\u009f": "ss",
    "\u00e3\u00a4": "ae",
    "\u00e3\u00b6": "oe",
    "\u00e3\u00bc": "ue",
    "\u00e3\u009f": "ss",
  };
  for (const [source, target] of Object.entries(replacements)) {
    text = text.split(source).join(target);
  }
  text = text.replace(/[^\p{L}\p{N}_]+/gu, " ");
  return text.replace(/\s+/g, " ").trim();
};

const list = document.querySelector(".virtual-list");
if (!list) {
  return {status: "missing-list"};
}

const rows = Array.from(list.querySelectorAll(".inner"));
let lastText = null;
for (const row of rows) {
  const title = row.querySelector(".item-title");
  const titleText = title ? title.innerText.trim() : "";
  if (titleText) {
    lastText = titleText;
  }
  if (normalize(titleText) !== wanted) {
    continue;
  }

  const button = row.querySelector(".item-actions-2 button");
  if (!button) {
    return {status: "missing-button", text: titleText, lastText};
  }
  button.scrollIntoView({block: "center"});
  button.click();
  return {status: "clicked", text: titleText, lastText};
}

if (rows.length) {
  rows[rows.length - 1].scrollIntoView({block: "end"});
}
return {status: "not-found", lastText, rowCount: rows.length};
"""

RESET_SHOPPING_LIST_SCROLL_SCRIPT = r"""
const list = document.querySelector(".virtual-list");
const scrollers = [];
for (let node = list; node; node = node.parentElement) {
  if (node.scrollHeight > node.clientHeight) {
    node.scrollTop = 0;
    scrollers.push(node.tagName);
  }
}
if (document.scrollingElement) {
  document.scrollingElement.scrollTop = 0;
}
window.scrollTo(0, 0);
return {status: list ? "reset" : "missing-list", scrollers};
"""


def selenium_remove_retry_exceptions() -> tuple[type[BaseException], ...]:
    """Return Selenium exceptions that indicate transient DOM changes."""
    try:
        from selenium.common.exceptions import (
            ElementClickInterceptedException,
            JavascriptException,
            NoSuchElementException,
            StaleElementReferenceException,
        )
    except ModuleNotFoundError:
        return ()
    return (
        StaleElementReferenceException,
        NoSuchElementException,
        ElementClickInterceptedException,
        JavascriptException,
    )


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


def load_cookie_list(path: Path) -> list[dict[str, Any]]:
    """Load stored Amazon cookies from JSON."""
    if not path.exists():
        raise RuntimeError("Amazon-Session fehlt. Bitte Cookies in der Weboberflaeche importieren.")
    try:
        with path.open("r", encoding="utf-8") as cookie_file:
            cookies = json.load(cookie_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Amazon-Cookie-Datei konnte nicht gelesen werden.") from exc
    if not isinstance(cookies, list):
        raise RuntimeError("Cookie-Datei muss eine JSON-Liste enthalten.")
    return [cookie for cookie in cookies if isinstance(cookie, dict)]


def cookie_matches_host(cookie: dict[str, Any], host: str) -> bool:
    """Return whether a stored cookie should be sent to host."""
    domain = str(cookie.get("domain") or "").strip().lower()
    if not domain:
        return True
    host = host.strip().lower()
    if domain.startswith("."):
        domain = domain[1:]
        return host == domain or host.endswith(f".{domain}")
    return host == domain


def cookie_header_from_cookie_list(cookies: list[dict[str, Any]], host: str | None = None) -> str:
    """Return a Cookie header from stored Selenium-style cookies."""
    now = int(time.time())
    values: list[str] = []
    for cookie in cookies:
        if host is not None and not cookie_matches_host(cookie, host):
            continue
        name = str(cookie.get("name") or "").strip()
        value = cookie.get("value")
        expiry = cookie.get("expiry")
        if not name or value is None:
            continue
        if any(character in name for character in "\r\n;="):
            continue
        if isinstance(expiry, (int, float)) and expiry <= now:
            continue
        cookie_value = str(value).replace("\r", "").replace("\n", "")
        values.append(f"{name}={cookie_value}")
    return "; ".join(values)


def csrf_from_cookie_list(cookies: list[dict[str, Any]]) -> str:
    """Return the csrf cookie value if present."""
    for cookie in cookies:
        if str(cookie.get("name") or "").casefold() == "csrf":
            return str(cookie.get("value") or "")
    return ""


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
    existing_accounts_by_id = {
        sanitize_account_id(account.get("id")): dict(account)
        for account in existing_accounts
        if isinstance(account, dict)
    }
    used_ids: set[str] = set()
    next_accounts: list[dict[str, Any]] = []
    imported: list[dict[str, Any]] = []
    errors: list[str] = []

    for session_file in session_files:
        label = alexa_media_session_label(session_file)
        base_id = sanitize_account_id(label)
        account_id = unique_account_id(base_id, used_ids)
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
        account = existing_accounts_by_id.get(account_id) or {}
        account.update(
            {
                "id": account_id,
                "name": account.get("name") or label,
                "amazon_domain": account.get("amazon_domain") or infer_amazon_domain(cookies, settings["amazon_domain"]),
                "enabled": True,
            }
        )
        next_accounts.append(account)
        imported.append({"account_id": account_id, "name": account["name"], "source": session_file.name})

    if not imported:
        details = f" Details: {'; '.join(errors[:3])}" if errors else ""
        raise RuntimeError(f"Keine nutzbare Alexa-Media-Player-Session gefunden.{details}")

    settings["amazon_accounts"] = normalize_amazon_accounts(next_accounts, settings["amazon_domain"])
    normalized = normalize_settings(settings)
    write_json_file(SETTINGS_PATH, normalized)
    return {"imported": imported, "errors": errors, "settings": normalized}


# ---------------------------------------------------------------------------
# HTTP Alexa client
# ---------------------------------------------------------------------------

class HttpAlexaClient:
    """Lightweight Alexa shopping list client using Amazon's internal HTTP endpoints."""

    def __init__(self, amazon_domain: str, cookie_path: Path | None = None) -> None:
        """Initialize client."""
        self.amazon_domain = normalize_amazon_domain(amazon_domain)
        self.cookie_path = cookie_path or ALEXA_COOKIES_PATH
        self.cookies: list[dict[str, Any]] = []
        self.cookie_header = ""
        self.csrf = ""
        self.shopping_list_id: str | None = None
        self.item_versions: dict[str, int] = {}
        self.last_auth_error: str | None = None

    def __enter__(self) -> "HttpAlexaClient":
        """Load session cookies."""
        self.cookies = load_cookie_list(self.cookie_path)
        self.cookie_header = cookie_header_from_cookie_list(self.cookies)
        self.csrf = csrf_from_cookie_list(self.cookies)
        if not self.cookie_header:
            raise RuntimeError("Amazon-Session enthaelt keine nutzbaren Cookies.")
        if not self.csrf:
            raise RuntimeError("Amazon-Session enthaelt kein CSRF-Cookie.")
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        """Clear per-run caches."""
        self.shopping_list_id = None
        self.item_versions = {}

    def _alexa_api_base(self) -> str:
        """Return Alexa web API base URL."""
        return f"https://alexa.{self.amazon_domain}"

    def _shopping_api_base(self) -> str:
        """Return Amazon shopping-list API base URL."""
        return f"https://www.{self.amazon_domain}/alexashoppinglists/api/v2"

    def _headers(self) -> dict[str, str]:
        """Return HTTP headers for Amazon internal API calls."""
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
            "Content-Type": "application/json",
            "Cookie": self.cookie_header,
            "Origin": self._alexa_api_base(),
            "Referer": f"{self._alexa_api_base()}/spa/index.html",
            "User-Agent": HTTP_USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
        }
        if self.csrf:
            headers["csrf"] = self.csrf
        return headers

    def _request_json(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call an Amazon internal endpoint and return a JSON object."""
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        http_request = request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with request.urlopen(http_request, timeout=20) as response:
                body = self._decode_response_body(response.read(), response.headers.get("Content-Encoding"))
        except error.HTTPError as exc:
            body = self._decode_response_body(exc.read(), exc.headers.get("Content-Encoding"))
            raise RuntimeError(f"Alexa-HTTP-Anfrage fehlgeschlagen ({exc.code}): {body[:200]}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Alexa-HTTP-Anfrage fehlgeschlagen: {exc}") from exc
        if not body.strip():
            return {}
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Alexa-HTTP-Antwort war kein JSON: {body[:200]}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Alexa-HTTP-Antwort war kein JSON-Objekt.")
        return parsed

    def _decode_response_body(self, body: bytes, encoding: str | None) -> str:
        """Decode a possibly compressed Amazon response body for JSON parsing/logging."""
        normalized = str(encoding or "").lower()
        try:
            if normalized == "gzip":
                body = zlib.decompress(body, zlib.MAX_WBITS | 16)
            elif normalized == "deflate":
                body = zlib.decompress(body)
        except zlib.error:
            LOGGER.debug("Could not decompress Alexa HTTP response", exc_info=True)
        return body.decode("utf-8", errors="replace")

    def _quote(self, value: str) -> str:
        """Quote a path value for Amazon internal endpoints."""
        return parse.quote(value, safe="")

    def _list_candidates(self) -> list[dict[str, Any]]:
        """Return available Alexa list metadata."""
        response = self._request_json(
            "POST",
            f"{self._shopping_api_base()}/lists/fetch",
            {
                "listAttributesToAggregate": [
                    {"type": "totalActiveItemsCount"},
                ],
                "listOwnershipType": None,
            },
        )
        raw_lists = response.get("listInfoList") or response.get("lists") or []
        return raw_lists if isinstance(raw_lists, list) else []

    def _list_id_from_candidate(self, candidate: dict[str, Any]) -> str:
        """Extract a list id from an Alexa list metadata item."""
        for key in ("listId", "id"):
            value = candidate.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    def _shopping_score(self, candidate: dict[str, Any]) -> int:
        """Score whether a metadata item is the Alexa shopping list."""
        text = " ".join(
            str(candidate.get(key) or "")
            for key in ("listType", "type", "listName", "name", "listId", "id")
        ).casefold()
        score = 0
        if "shopping" in text or "einkauf" in text:
            score += 10
        if "shopping_item" in text or "-shopping_item" in text:
            score += 10
        if "task" in text or "todo" in text or "to-do" in text:
            score -= 10
        return score

    def _list_candidate_summary(self, candidate: dict[str, Any]) -> str:
        """Return a compact log-safe list metadata summary."""
        fields = []
        for key in ("listType", "type", "listName", "name", "listId", "id"):
            value = str(candidate.get(key) or "").strip()
            if value:
                fields.append(f"{key}={value[:60]}")
        return ", ".join(fields) or "unknown"

    def _ensure_shopping_list_id(self) -> str:
        """Return and cache the Alexa shopping list id."""
        if self.shopping_list_id:
            return self.shopping_list_id
        candidates = [
            candidate
            for candidate in self._list_candidates()
            if isinstance(candidate, dict) and self._list_id_from_candidate(candidate)
        ]
        if not candidates:
            raise RuntimeError("Alexa-HTTP konnte keine Listenmetadaten lesen.")
        best = max(candidates, key=self._shopping_score)
        if self._shopping_score(best) <= 0:
            summaries = "; ".join(self._list_candidate_summary(candidate) for candidate in candidates[:5])
            raise RuntimeError(f"Alexa-HTTP konnte die Einkaufsliste nicht eindeutig finden. Listen: {summaries}")
        self.shopping_list_id = self._list_id_from_candidate(best)
        return self.shopping_list_id

    def is_authenticated(self) -> bool:
        """Return if stored cookies can access the Alexa shopping list endpoints."""
        try:
            self._ensure_shopping_list_id()
        except Exception as exc:
            self.last_auth_error = str(exc)
            LOGGER.debug("Alexa HTTP auth/list check failed: %s", exc, exc_info=True)
            return False
        self.last_auth_error = None
        return True

    def _extract_items(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract list items from known Alexa response shapes."""
        raw_items = response.get("listItemInfoList") or response.get("items") or []
        raw_items = response.get("itemInfoList") or raw_items
        return raw_items if isinstance(raw_items, list) else []

    def get_items(self) -> list[TodoItem]:
        """Return active Alexa shopping list items via HTTP."""
        list_id = self._ensure_shopping_list_id()
        url = f"{self._shopping_api_base()}/lists/{self._quote(list_id)}/items/fetch?limit=100"
        response = self._request_json(
            "POST",
            url,
            {
                "itemAttributesToProject": [
                    "quantity",
                    "note",
                ]
            },
        )
        items: list[TodoItem] = []
        self.item_versions = {}
        for raw_item in self._extract_items(response):
            if not isinstance(raw_item, dict):
                continue
            summary = str(raw_item.get("itemName") or raw_item.get("value") or "").strip()
            uid = str(raw_item.get("itemId") or raw_item.get("id") or "").strip()
            status = str(raw_item.get("itemStatus") or raw_item.get("status") or "").upper()
            version = raw_item.get("version")
            if not summary or not uid or status == SHOPPING_LIST_ITEM_STATUS_COMPLETE:
                continue
            if isinstance(version, int):
                self.item_versions[uid] = version
            items.append(TodoItem(uid=uid, summary=summary, status=STATUS_NEEDS_ACTION))
        return items

    def add_item(self, item: TodoItem) -> None:
        """Add an item to Alexa via HTTP."""
        list_id = self._ensure_shopping_list_id()
        url = f"{self._shopping_api_base()}/lists/{self._quote(list_id)}/items"
        payload = {
            "items": [
                {
                    "itemName": item.summary,
                    "itemType": "KEYWORD",
                }
            ]
        }
        self._request_json("POST", url, payload)

    def remove_item(self, item: TodoItem) -> bool:
        """Mark one Alexa item as complete via HTTP."""
        return self.remove_items([item]) > 0

    def remove_items(self, items: list[TodoItem]) -> int:
        """Mark multiple Alexa items as complete via HTTP."""
        if not items:
            return 0
        if any(item.uid not in self.item_versions for item in items):
            self.get_items()
        list_id = self._ensure_shopping_list_id()
        removed = 0
        for item in items:
            item_id = item.uid
            version = self.item_versions.get(item_id)
            if version is None:
                LOGGER.debug("Alexa HTTP item '%s' has no cached version", item.summary)
                continue
            query = parse.urlencode({"version": version})
            url = (
                f"{self._shopping_api_base()}/lists/{self._quote(list_id)}"
                f"/items/{self._quote(item_id)}?{query}"
            )
            payload = {
                "itemAttributesToUpdate": [
                    {"type": "itemName", "value": item.summary},
                    {"type": "itemStatus", "value": SHOPPING_LIST_ITEM_STATUS_COMPLETE}
                ],
                "itemAttributesToRemove": [],
            }
            self._request_json("PUT", url, payload)
            removed += 1
        return removed


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
        self._cookies_loaded = False
        self._list_loaded = False

    def __enter__(self) -> "InternalAlexaClient":
        """Start browser."""
        self.driver = self._create_driver()
        self._cookies_loaded = False
        self._list_loaded = False
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        """Stop browser."""
        if self.driver is not None:
            self.driver.quit()
            self.driver = None
        self._cookies_loaded = False
        self._list_loaded = False

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
        if self._cookies_loaded:
            return

        self.driver.get(f"https://www.{self.amazon_domain}")
        cookies = load_cookie_list(self.cookie_path)

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
        self._cookies_loaded = True
        self._list_loaded = False

    def _shopping_list_url(self) -> str:
        """Return the Alexa shopping list URL for the configured marketplace."""
        return f"https://www.{self.amazon_domain}/alexaquantum/sp/alexaShoppingList?ref=nav_asl"

    def is_authenticated(self) -> bool:
        """Return if imported cookies still authenticate with Amazon."""
        if self.driver is None:
            raise RuntimeError("Browser is not running")
        self._load_cookies()
        self.driver.get(self._shopping_list_url())
        self._list_loaded = False
        time.sleep(3)
        current_url = str(self.driver.current_url).lower()
        page = self.driver.page_source.lower()
        authenticated = "ap/signin" not in current_url and "virtual-list" in page
        self._list_loaded = authenticated
        return authenticated

    def _list_is_open(self) -> bool:
        """Return whether the current browser page already contains the shopping list."""
        if self.driver is None:
            raise RuntimeError("Browser is not running")
        try:
            return bool(self.driver.execute_script("return Boolean(document.querySelector('.virtual-list'));"))
        except Exception:
            return False

    def _open_list(self) -> None:
        """Open Alexa shopping list page."""
        if self.driver is None:
            raise RuntimeError("Browser is not running")
        if self._list_loaded and self._list_is_open():
            return
        self._load_cookies()
        if self._list_is_open():
            self._list_loaded = True
            return

        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as ec
        from selenium.webdriver.support.ui import WebDriverWait

        self.driver.get(self._shopping_list_url())
        self._list_loaded = False
        WebDriverWait(self.driver, 30).until(ec.presence_of_element_located((By.CLASS_NAME, "virtual-list")))
        time.sleep(3)
        self._list_loaded = True

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
        return self.remove_items([item]) > 0

    def remove_items(self, items: list[TodoItem]) -> int:
        """Remove multiple items from Alexa while keeping the list page open."""
        if not items:
            return 0
        if self.driver is None:
            raise RuntimeError("Browser is not running")

        self._open_list()
        removed_count = 0
        retry_exceptions = selenium_remove_retry_exceptions()
        for item in items:
            last_error: Exception | None = None
            for attempt in range(1, 4):
                try:
                    if self._remove_item_from_open_list(item):
                        removed_count += 1
                    break
                except retry_exceptions as exc:
                    last_error = exc
                    LOGGER.debug("Retrying Alexa remove for '%s' after DOM change", item.summary, exc_info=True)
                    time.sleep(attempt)
                    self._open_list()
            else:
                LOGGER.warning("Could not remove '%s' from Alexa after retries: %s", item.summary, last_error)
        return removed_count

    def _remove_item_once(self, item: TodoItem) -> bool:
        """Remove an item once, returning whether a row was clicked."""
        if self.driver is None:
            raise RuntimeError("Browser is not running")
        self._open_list()
        return self._remove_item_from_open_list(item)

    def _remove_item_from_open_list(self, item: TodoItem) -> bool:
        """Remove an item from the currently open Alexa shopping list."""
        from ha_client import normalize_summary

        if self.driver is None:
            raise RuntimeError("Browser is not running")
        wanted = normalize_summary(item.summary)
        last_text = None
        stable_rounds = 0
        self.driver.execute_script(RESET_SHOPPING_LIST_SCROLL_SCRIPT)
        time.sleep(0.2)

        while stable_rounds < 2:
            result = self.driver.execute_script(CLICK_VISIBLE_SHOPPING_LIST_ITEM_SCRIPT, wanted)
            result = result if isinstance(result, dict) else {}
            status = result.get("status")
            if status == "clicked":
                time.sleep(1)
                return True
            if status == "missing-list":
                from selenium.common.exceptions import NoSuchElementException

                raise NoSuchElementException("virtual-list")
            if status == "missing-button":
                LOGGER.debug("Alexa row for '%s' has no remove button", item.summary)
                return False

            current_last = result.get("lastText")
            if current_last == last_text:
                stable_rounds += 1
            else:
                stable_rounds = 0
            last_text = current_last

            time.sleep(1)
        return False
