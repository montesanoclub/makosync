"""Tiny ingest capture server for demos / local testing.

Listens for the same calls MakoSync makes and lets you exercise the full pipeline
without a real makosmeets endpoint. Accepts any (or no) bearer token.

  * ``POST /api/live-results/ingest/`` (JSON) + ``/ingest/file/`` (multipart) —
    pretty-prints each heat (official places included).
  * ``POST``/``GET /api/live-results/dolphin-events/`` — an in-memory **stub of
    the relay** (see docs/dolphin-events-relay.md): POST stores the event list
    and stamps ``updated_at``; GET returns it. Lets you bench the Manager-push →
    Dolphin-load round-trip before the real server endpoint exists.

    python build/capture_server.py            # listen on 0.0.0.0:8099
    python build/capture_server.py 9000       # custom port

Point MakoSync at  http://<this-machine-ip>:<port>  (no token needed).
"""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HEAT_PATH = "/api/live-results/ingest/"
FILE_PATH = "/api/live-results/ingest/file/"
DOLPHIN_EVENTS_PATH = "/api/live-results/dolphin-events/"

# In-memory relay mailbox (mirrors the KV the real endpoint will use).
_EVENTS_LOCK = threading.Lock()
_EVENTS_STORE: dict = {"csv": "", "name": "", "updated_at": None, "lines": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):  # silence default logging
        pass

    def _ok(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def _json(self, obj, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode("utf-8"))

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(n)
        ts = datetime.now().strftime("%H:%M:%S")
        if self.path == HEAT_PATH:
            try:
                h = json.loads(body.decode("utf-8"))
            except Exception:
                print(f"[{ts}] heat POST (unparseable, {len(body)} bytes)")
                return self._ok()
            tier = h.get("tier"); src = h.get("source")
            ev = h.get("event"); ht = h.get("heat")
            lanes = h.get("lanes") or []
            print(f"\n[{ts}] HEAT  event {ev} heat {ht}  tier={tier} source={src}  race_id={h.get('race_id','')}")
            for ln in sorted(lanes, key=lambda l: l.get("lane", 0)):
                place = f"  place {ln['place']}" if ln.get("place") is not None else ""
                dq = "  DQ" if ln.get("dq") else ""
                print(f"        lane {ln.get('lane'):>2}  {str(ln.get('time')):>9}{place}{dq}")
            return self._ok()
        if self.path == FILE_PATH:
            print(f"[{ts}] raw file POST ({len(body)} bytes)")
            return self._ok()
        if self.path == DOLPHIN_EVENTS_PATH:
            try:
                payload = json.loads(body.decode("utf-8"))
                csv_text = payload.get("csv") or ""
                name = payload.get("name") or ""
            except Exception:
                return self._json({"ok": False, "error": "invalid JSON"}, 400)
            lines = sum(1 for ln in csv_text.splitlines() if ln.strip())
            updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            with _EVENTS_LOCK:
                _EVENTS_STORE.update(csv=csv_text, name=name, lines=lines, updated_at=updated_at)
            print(f"\n[{ts}] DOLPHIN-EVENTS push: {lines} events ({name}, updated_at {updated_at})")
            for ln in csv_text.splitlines()[:6]:
                print(f"        {ln}")
            if lines > 6:
                print(f"        ... +{lines - 6} more")
            return self._json({"ok": True, "updated_at": updated_at, "lines": lines})
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        if self.path == DOLPHIN_EVENTS_PATH:
            with _EVENTS_LOCK:
                snap = dict(_EVENTS_STORE)
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] DOLPHIN-EVENTS load: {snap['lines']} events (updated_at {snap['updated_at']})")
            return self._json(snap)
        self.send_response(404)
        self.end_headers()


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"capture server listening on 0.0.0.0:{port}")
    print(f"point MakoSync at  http://<this-machine-ip>:{port}  (no token needed)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
