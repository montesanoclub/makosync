"""End-to-end smoke test: real samples through a real HTTP server.

Spins up an stdlib ``http.server`` that captures /ingest/heat (JSON) and
/ingest/file (multipart) requests, runs the watcher in --once style, then
verifies the right number of each landed with the right content.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from dolphinsync.client import IngestClient
from dolphinsync.parser import parse_file
from dolphinsync.watcher import Watcher, WatcherConfig

SAMPLES = Path(__file__).parent.parent / "samples"
TOKEN = "test-token-xyz"


class _Captured:
    def __init__(self) -> None:
        self.heat_posts: list[dict[str, Any]] = []
        self.file_posts: list[tuple[str, int]] = []  # (filename, byte_count)
        self.lock = threading.Lock()


def _make_handler(captured: _Captured):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # quiet

        def _auth_ok(self) -> bool:
            return self.headers.get("Authorization") == f"Bearer {TOKEN}"

        def do_POST(self):
            if not self._auth_ok():
                self.send_response(401); self.end_headers(); return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)

            if self.path == "/api/live-results/ingest/":
                try:
                    payload = json.loads(body.decode("utf-8"))
                except Exception:
                    self.send_response(400); self.end_headers(); return
                with captured.lock:
                    captured.heat_posts.append(payload)
                self.send_response(200); self.end_headers(); return

            if self.path == "/api/live-results/ingest/file/":
                # crude multipart sniff — just record name + size
                ct = self.headers.get("Content-Type", "")
                fname = "?"
                if 'filename="' in body.decode("latin1", "replace"):
                    s = body.decode("latin1", "replace")
                    i = s.index('filename="') + len('filename="')
                    j = s.index('"', i)
                    fname = s[i:j]
                with captured.lock:
                    captured.file_posts.append((fname, len(body)))
                self.send_response(200); self.end_headers(); return

            self.send_response(404); self.end_headers()
    return H


@pytest.fixture()
def server():
    captured = _Captured()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(captured))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    port = srv.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}", captured
    finally:
        srv.shutdown()
        srv.server_close()


def test_e2e_heat_and_raw(tmp_path, server):
    base_url, captured = server

    # Copy a real .do4 sample (one with a time so timed_lanes is non-empty) to a watch folder.
    src = SAMPLES / "004-000-001A-0001.do4"
    watch = tmp_path / "watch"
    watch.mkdir()
    shutil.copy(src, watch / src.name)

    cfg = WatcherConfig(
        folder=watch,
        base_url=base_url,
        token=TOKEN,
        include_csv=False,
        upload_raw=True,
        replay_existing=True,  # send the file we just copied in
        poll_interval=0.2,
    )
    watcher = Watcher(cfg)
    watcher.start()

    # Wait up to 5s for both JSON + raw to land.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        with captured.lock:
            if captured.heat_posts and captured.file_posts:
                break
        time.sleep(0.1)
    watcher.stop()
    time.sleep(0.3)

    assert captured.heat_posts, "no JSON heat POST received"
    assert captured.file_posts, "no raw file POST received"

    h = captured.heat_posts[0]
    assert h["format"] == "do4"
    assert h["dataset"] == "004"
    assert h["race_id"] == "0001"
    assert h["lanes"] == [{"lane": 5, "time": "8.13", "timers": [8.13], "dq": False}]

    fname, size = captured.file_posts[0]
    assert fname == src.name
    assert size > src.stat().st_size  # multipart frame > raw body
