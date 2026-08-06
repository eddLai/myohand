"""inspire_hand.hand_server - minimal HTTP/JSON API for the RH56F1 hand.

Security: binds 127.0.0.1 ONLY (same policy as the lab's VNC-over-tunnel
practice). Other projects reach it through an SSH tunnel:

    ssh -L 8100:127.0.0.1:8100 eddlai@120.126.83.28
    curl http://127.0.0.1:8100/state

Endpoints:
    GET  /state                     telemetry, no motion
    POST /pose                      {"targets":[6 ints], "force":500, "speed":800}
    POST /gesture/<name>            open | fist | middle | point | release
Requests are serialized (one motion at a time); pose calls block until
the execute cycle finishes (~10-20 s).
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hand_api import InspireHand, HandError

BIND = "127.0.0.1"   # localhost-only by design; use SSH tunnels from outside
PORT = 8100
hand = InspireHand()
lock = threading.Lock()

GESTURES = {
    "open": hand.open_hand,
    "fist": hand.fist,
    "middle": hand.middle_finger,
    "point": hand.point,
    "release": hand.release,
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/state", "/health"):
            try:
                with lock:
                    self._send(200, hand.state())
            except HandError as e:
                self._send(503, {"ok": False, "error": str(e)})
        else:
            self._send(404, {"ok": False, "error": "GET /state only"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"ok": False, "error": "bad JSON"})
        try:
            if self.path == "/pose":
                targets = payload.get("targets")
                if not isinstance(targets, list) or len(targets) != 6:
                    return self._send(400, {"ok": False, "error": "targets must be 6 ints"})
                with lock:
                    out = hand.pose(targets,
                                    force=payload.get("force", 500),
                                    speed=payload.get("speed", 800))
                return self._send(200, out)
            if self.path.startswith("/gesture/"):
                name = self.path.rsplit("/", 1)[1]
                fn = GESTURES.get(name)
                if not fn:
                    return self._send(404, {"ok": False,
                                            "error": f"gestures: {sorted(GESTURES)}"})
                with lock:
                    return self._send(200, fn())
            self._send(404, {"ok": False, "error": "POST /pose or /gesture/<name>"})
        except HandError as e:
            self._send(503, {"ok": False, "error": str(e)})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    print(f"inspire_hand server on {BIND}:{PORT} (localhost-only, tunnel in)")
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
