import json
import logging
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

logger = logging.getLogger("silent_boot.api")


class RemoteApiHandler(BaseHTTPRequestHandler):
    controller: Any = None
    api_token: Optional[str] = None

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug(f"API {self.address_string()} - {format % args}")

    def _send_json(self, status_code: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _is_authorized(self, parsed_path: urllib.parse.ParseResult) -> bool:
        if not self.api_token:
            return True

        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            if auth_header.split(" ", 1)[1].strip() == self.api_token:
                return True

        token_header = self.headers.get("X-API-Token", "").strip()
        if token_header == self.api_token:
            return True

        query_params = urllib.parse.parse_qs(parsed_path.query)
        if query_params.get("token", [None])[0] == self.api_token:
            return True

        return False

    def _handle_request(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/health":
            self._send_json(200, {"status": "ok"})
            return

        if not self._is_authorized(parsed):
            self._send_json(401, {"error": "Unauthorized"})
            return

        if path == "/api/status":
            self._send_json(200, self.controller.get_system_status())
            return

        if path == "/api/power/on":
            threading.Thread(target=self.controller.power_on_all, daemon=True).start()
            self._send_json(202, {"status": "accepted", "action": "power_on_all"})
            return

        if path == "/api/power/off":
            threading.Thread(target=self.controller.power_off_all, daemon=True).start()
            self._send_json(202, {"status": "accepted", "action": "power_off_all"})
            return

        if path.startswith("/api/device/"):
            parts = path.split("/")
            if len(parts) == 5 and parts[4] in ("on", "off"):
                dev_name = parts[3]
                turn_on = parts[4] == "on"
                found = self.controller.get_device(dev_name)
                if not found:
                    self._send_json(404, {"error": f"Device '{dev_name}' not found or disabled"})
                    return
                fn = found.power_on_device if turn_on else found.power_off_device
                threading.Thread(target=fn, daemon=True).start()
                self._send_json(202, {"status": "accepted", "device": dev_name, "action": parts[4]})
                return

        self._send_json(404, {"error": "Endpoint not found"})

    def do_GET(self) -> None:
        self._handle_request()

    def do_POST(self) -> None:
        self._handle_request()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, X-API-Token, Content-Type")
        self.end_headers()


class RemoteApiServer:
    def __init__(self, controller: Any, host: str = "0.0.0.0", port: int = 8080, api_token: Optional[str] = None):
        self.controller = controller
        self.host = host
        self.port = port
        self.api_token = api_token
        self.server: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        handler_cls = type(
            "BoundRemoteApiHandler",
            (RemoteApiHandler,),
            {"controller": self.controller, "api_token": self.api_token}
        )
        try:
            self.server = ThreadingHTTPServer((self.host, self.port), handler_cls)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            logger.info(f"Remote control API listening on http://{self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to start remote API server on {self.host}:{self.port}: {e}")

    def stop(self) -> None:
        if self.server is not None:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
