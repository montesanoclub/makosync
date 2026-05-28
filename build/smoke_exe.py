"""Smoke test: invoke the built .exe against a capture HTTP server and
assert the parsed-heat JSON POST plus the raw-file upload land. Proves the
PyInstaller bundle actually runs (no ImportError, no missing modules) and
that the headless code path reaches both makosmeets ingest endpoints.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXE = REPO / "dist" / "MakosDolphinSync.exe"
SAMPLE = REPO / "samples" / "004-000-001A-0001.do4"
# Must match dolphinsync.client.HEAT_PATH / FILE_PATH.
HEAT_PATH = "/api/live-results/ingest/"
FILE_PATH = "/api/live-results/ingest/file/"


class Captured:
    def __init__(self) -> None:
        self.heats: list[dict] = []
        self.files: list[tuple[str, int]] = []
        self.lock = threading.Lock()


def make_handler(cap: Captured):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(n)
            if self.path == HEAT_PATH:
                with cap.lock:
                    cap.heats.append(json.loads(body.decode("utf-8")))
                self.send_response(200); self.end_headers(); return
            if self.path == FILE_PATH:
                s = body.decode("latin1", "replace")
                fname = "?"
                if 'filename="' in s:
                    i = s.index('filename="') + len('filename="')
                    j = s.index('"', i)
                    fname = s[i:j]
                with cap.lock:
                    cap.files.append((fname, len(body)))
                self.send_response(200); self.end_headers(); return
            self.send_response(404); self.end_headers()
    return H


def main() -> int:
    if not EXE.exists():
        print(f"FAIL: {EXE} not built", file=sys.stderr); return 1
    if not SAMPLE.exists():
        print(f"FAIL: {SAMPLE} missing", file=sys.stderr); return 1

    cap = Captured()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cap))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    url = f"http://127.0.0.1:{port}"
    print(f"capture server on {url}")

    with tempfile.TemporaryDirectory() as td:
        watch = Path(td) / "watch"
        watch.mkdir()
        shutil.copy(SAMPLE, watch / SAMPLE.name)

        print(f"running {EXE.name} --headless --once ...")
        proc = subprocess.run(
            [
                str(EXE),
                "--headless", "--once",
                "--folder", str(watch),
                "--url", url,
                "--replay-existing",
            ],
            capture_output=True, text=True, timeout=30,
        )
        print(f"exe exit: {proc.returncode}")
        if proc.stdout:
            print("--- stdout ---"); print(proc.stdout)
        if proc.stderr:
            print("--- stderr ---"); print(proc.stderr)

    # Give the heat + raw POSTs a moment to land.
    deadline = time.time() + 3.0
    while time.time() < deadline:
        with cap.lock:
            if cap.heats and cap.files:
                break
        time.sleep(0.1)

    srv.shutdown(); srv.server_close()

    print(f"heats: {len(cap.heats)}  files: {len(cap.files)}")
    failures = []
    if proc.returncode != 0:
        failures.append(f"exe exit code {proc.returncode}")
    if not cap.heats:
        failures.append(f"no {HEAT_PATH} POST received")
    else:
        h = cap.heats[0]
        if h.get("format") != "do4":
            failures.append(f"heat format != do4: {h.get('format')!r}")
        if h.get("race_id") != "0001":
            failures.append(f"heat race_id != 0001: {h.get('race_id')!r}")
    if not cap.files:
        failures.append(f"no {FILE_PATH} POST received")
    else:
        fname, size = cap.files[0]
        if fname != SAMPLE.name:
            failures.append(f"raw filename != {SAMPLE.name}: {fname!r}")

    if failures:
        print("FAIL:")
        for f in failures: print(f"  - {f}")
        return 2
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
