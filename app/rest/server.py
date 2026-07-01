"""A tiny local REST API for managing the machine_version at runtime.

The agent is otherwise outbound-only; this is the one inbound surface, intended for
local callers (a deploy/upgrade job step running *on* the machine), so it binds to
loopback by default. Built on the stdlib http.server so the agent gains no new
dependency.

Endpoints (JSON):
  GET  /machine-version  -> {"machine_version": <int>}
  PUT  /machine-version  {"machine_version": 2026070101} -> {"machine_version": <int>}
  POST /machine-version  (same as PUT)
  GET  /health           -> {"status": "ok"}
"""
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.core.machine_version import InvalidMachineVersionError, MachineVersionStore

logger = logging.getLogger("modric_agent.rest")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _make_handler(store: MachineVersionStore):
    class _Handler(BaseHTTPRequestHandler):
        # Silence the default stderr request logging; route through our logger.
        def log_message(self, fmt, *args):
            logger.debug("rest %s - %s", self.address_string(), fmt % args)

        def _send(self, code: int, payload: dict):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.rstrip("/") in ("/health", "/healthz"):
                self._send(200, {"status": "ok"})
            elif self.path.rstrip("/") == "/machine-version":
                self._send(200, {"machine_version": store.get()})
            else:
                self._send(404, {"error": "not found"})

        def _update_machine_version(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._send(400, {"error": "invalid JSON body"})
                return
            if "machine_version" not in data:
                self._send(400, {"error": "missing 'machine_version'"})
                return
            try:
                value = store.set(data["machine_version"])
            except InvalidMachineVersionError as exc:
                self._send(400, {"error": str(exc)})
                return
            self._send(200, {"machine_version": value})

        def do_PUT(self):
            if self.path.rstrip("/") == "/machine-version":
                self._update_machine_version()
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            self.do_PUT()

    return _Handler


class MachineVersionRestServer:
    """Runs the REST API on a daemon thread alongside the WebSocket client."""

    def __init__(self, store: MachineVersionStore,
                 host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self._store = store
        self._host = host
        self._port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._httpd = ThreadingHTTPServer((self._host, self._port), _make_handler(self._store))
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="machine-version-rest", daemon=True,
        )
        self._thread.start()
        logger.info("machine_version REST API listening on http://%s:%s", self._host, self._port)

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
