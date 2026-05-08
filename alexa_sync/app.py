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

API_BASE = "http://supervisor/core/api"
OPTIONS_PATH = Path("/data/options.json")
SETTINGS_PATH = Path("/data/settings.json")
STATE_PATH = Path("/data/sync_state.json")
WEB_PORT = 8099

STATUS_NEEDS_ACTION = "needs_action"
STATUS_COMPLETED = "completed"

DEFAULT_SETTINGS = {
    "list_a": "",
    "list_b": "",
    "interval_seconds": 60,
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

    def sync(self) -> dict[str, Any]:
        """Run one synchronized pass with locking."""
        with self.lock:
            settings = load_settings()
            if not is_configured(settings):
                self.last_result = {
                    "configured": False,
                    "last_sync": None,
                    "last_writes": 0,
                    "last_error": "Bitte zuerst zwei unterschiedliche Listen auswaehlen.",
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
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        return dict(fallback)
    return data


def write_json_file(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically."""
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=True, indent=2, sort_keys=True)
    tmp_path.replace(path)


def load_options() -> dict[str, Any]:
    """Load add-on options as initial defaults."""
    return read_json_file(OPTIONS_PATH, DEFAULT_SETTINGS)


def normalize_settings(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize user settings."""
    settings = dict(DEFAULT_SETTINGS)
    settings.update(raw)
    settings["list_a"] = str(settings.get("list_a", "")).strip()
    settings["list_b"] = str(settings.get("list_b", "")).strip()
    settings["interval_seconds"] = max(10, min(3600, int(settings.get("interval_seconds", 60))))
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
    if bool(settings["list_a"]) != bool(settings["list_b"]):
        raise ValueError("Bitte beide Listen auswaehlen.")
    if settings["list_a"] and settings["list_a"] == settings["list_b"]:
        raise ValueError("Bitte zwei unterschiedliche Listen auswaehlen.")


def is_configured(settings: dict[str, Any]) -> bool:
    """Return if sync lists are configured."""
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
            "status": self.runtime.last_result,
            "entity_error": entity_error,
        }

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
            LOGGER.debug(
                "Running sync between %s and %s",
                settings["list_a"],
                settings["list_b"],
            )
            runtime.sync()
        else:
            LOGGER.info("Waiting for configuration in the add-on web UI")

        sleep_until = time.monotonic() + settings["interval_seconds"]
        while not STOP_REQUESTED and time.monotonic() < sleep_until:
            time.sleep(min(1, sleep_until - time.monotonic()))

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
    select, input[type="number"] {
      width: 100%;
      min-height: 42px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--panel);
      color: var(--text);
      padding: 8px 10px;
      font: inherit;
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
    <p>Waehle zwei Home-Assistant-To-do-Listen aus. Das Add-on synchronisiert neue Eintraege und optional den Erledigt-Status bidirektional.</p>

    <form id="config-form">
      <div class="grid">
        <div>
          <label for="list-a">Liste A</label>
          <select id="list-a" name="list_a"></select>
        </div>
        <div>
          <label for="list-b">Liste B</label>
          <select id="list-b" name="list_b"></select>
        </div>
        <div>
          <label for="interval">Sync-Intervall in Sekunden</label>
          <input id="interval" name="interval_seconds" type="number" min="10" max="3600" step="5">
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
    const interval = document.getElementById("interval");
    const syncCompleted = document.getElementById("sync-completed");
    const removeCompleted = document.getElementById("remove-completed");
    const message = document.getElementById("message");

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

    async function loadConfig() {
      const res = await fetch("api/config");
      const data = await res.json();
      const settings = data.settings;
      fillSelect(listA, data.todo_entities, settings.list_a);
      fillSelect(listB, data.todo_entities, settings.list_b);
      interval.value = settings.interval_seconds;
      syncCompleted.checked = settings.sync_completed;
      removeCompleted.checked = settings.remove_completed;
      renderStatus(data.status);
      if (data.entity_error) setMessage(data.entity_error, "error");
    }

    document.getElementById("config-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const payload = {
        list_a: listA.value,
        list_b: listB.value,
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

    loadConfig().catch((err) => setMessage(err.message, "error"));
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())
