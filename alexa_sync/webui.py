"""Web UI and HTTP handler for the Alexa Sync add-on."""

from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from settings import WEB_PORT, load_settings, normalize_settings, persist_amazon_account, save_settings
from alexa_client import (
    InternalAlexaClient,
    account_cookie_path,
    active_alexa_media_identities,
    alexa_media_config_entries,
    alexa_media_session_identity_keys,
    alexa_media_session_label,
    enabled_amazon_accounts,
    find_alexa_media_session_files,
    import_alexa_media_session,
    import_selected_alexa_media_sessions,
    normalize_amazon_accounts,
    preferred_identity_key,
    read_cookie_count,
    safe_mtime,
    sanitize_account_id,
    save_cookie_list,
    session_matches_active_identity,
)
from sync import suggest_todo_entity

LOGGER = logging.getLogger("alexa_sync")

_INDEX_HTML: str | None = None


def _load_index_html() -> str:
    """Load index.html from disk (next to this module)."""
    global _INDEX_HTML
    if _INDEX_HTML is None:
        html_path = Path(__file__).parent / "index.html"
        _INDEX_HTML = html_path.read_text(encoding="utf-8")
    return _INDEX_HTML


def resolve_account_from_payload(settings: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve an Amazon account from API payload or existing settings."""
    from settings import DEFAULT_ACCOUNT_ID
    raw_account = payload.get("account")
    if isinstance(raw_account, dict):
        return normalize_amazon_accounts([raw_account], settings["amazon_domain"])[0]

    account_id = sanitize_account_id(payload.get("account_id") or DEFAULT_ACCOUNT_ID)
    for account in settings.get("amazon_accounts", []):
        if account["id"] == account_id:
            return account
    raise ValueError("Amazon-Konto nicht gefunden. Bitte speichern und erneut versuchen.")


def load_settings_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Load settings, applying optional unsaved UI values without validating sessions yet."""
    settings = load_settings()
    draft_settings = payload.get("settings")
    if isinstance(draft_settings, dict):
        merged_settings = dict(settings)
        merged_settings.update(draft_settings)
        settings = normalize_settings(merged_settings)
    return settings


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


class WebHandler(BaseHTTPRequestHandler):
    """Ingress web UI and JSON API."""

    # Set by run_web_server via closure / class variable
    runtime: Any  # RuntimeState — typed as Any to avoid circular import

    def log_message(self, format_text: str, *args: Any) -> None:
        """Route HTTP access logs through the add-on logger."""
        LOGGER.debug(format_text, *args)

    def do_GET(self) -> None:
        """Serve UI and API."""
        if self.path in {"/", "/index.html"}:
            self.send_html(_load_index_html())
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
                settings = load_settings_from_payload(payload)
                account = resolve_account_from_payload(settings, payload)
                cookies = payload.get("cookies")
                if isinstance(cookies, str):
                    cookies = json.loads(cookies)
                if not isinstance(cookies, list):
                    raise ValueError("Cookies muessen als JSON-Liste uebergeben werden.")
                save_cookie_list(cookies, account_cookie_path(account["id"]))
                settings = persist_amazon_account(settings, account)
                self.send_json({"ok": True, "settings": settings})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/alexa/status":
            try:
                payload = self.read_json()
                settings = load_settings_from_payload(payload)
                account = resolve_account_from_payload(settings, payload)
                self.send_json(self.get_alexa_status_payload(account))
            except Exception as exc:
                self.send_json({"ok": False, "authenticated": False, "message": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/alexa/import_amp":
            try:
                payload = self.read_json()
                settings = load_settings_from_payload(payload)
                account = resolve_account_from_payload(settings, payload)
                result = import_alexa_media_session(
                    account["amazon_domain"],
                    account_id=account["id"],
                    source_name=payload.get("source") or None,
                )
                settings = persist_amazon_account(settings, account)
                self.send_json({"ok": True, "result": result, "settings": settings})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/alexa/import_amp_selected":
            try:
                payload = self.read_json()
                sources = payload.get("sources")
                if sources is not None and not isinstance(sources, list):
                    raise ValueError("Alexa-Media-Player-Sessions muessen als Liste uebergeben werden.")
                settings = load_settings_from_payload(payload)
                result = import_selected_alexa_media_sessions(settings, sources)
                self.send_json({"ok": True, "result": result, "settings": result.get("settings")})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/setup/start":
            try:
                payload = self.read_json()
                settings = load_settings_from_payload(payload)
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
                payload = self.read_json()
                result = self.runtime.save_setup_cookies()
                settings = load_settings_from_payload(payload)
                if isinstance(result.get("account"), dict):
                    settings = persist_amazon_account(settings, result["account"])
                self.send_json({"ok": True, "result": result, "settings": settings})
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


def run_web_server(runtime: Any) -> ThreadingHTTPServer:
    """Start the ingress web server in a background thread."""
    WebHandler.runtime = runtime
    server = ThreadingHTTPServer(("", WEB_PORT), WebHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    LOGGER.info("Configuration UI listening on port %s", WEB_PORT)
    return server
