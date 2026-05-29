"""Watcher retry semantics (audit fix R1).

A file must NOT be marked seen — and therefore must be retried on the next
poll — when handling fails transiently (file still locked, or server 5xx /
network). It SHOULD be marked seen on success, on a permanent 4xx rejection,
and after the transient-failure cap is reached.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from makosync.client import IngestResult
from makosync import watcher as watcher_mod
from makosync.watcher import Watcher, WatcherConfig

DO4_BODY = (
    ";1;1;All\n"
    "Lane1;0;0;0\nLane2;0;0;0\nLane3;0;0;0\nLane4;0;0;0\n"
    "Lane5;8.13;;\n"
    "Lane6;0;0;0\nLane7;0;0;0\nLane8;0;0;0\nLane9;0;0;0\nLane10;0;0;0\n"
    "1F72DF569DD6BBD7\n"
)
FILENAME = "004-000-001A-0001.do4"


class FakeClient:
    """Stand-in for IngestClient: scripted send_heat results, no network."""

    def __init__(self, results: list[IngestResult]) -> None:
        self._results = results
        self.calls = 0

    def send_heat(self, heat, tier: str = "unofficial", source: str = "dolphin") -> IngestResult:
        self.calls += 1
        idx = min(self.calls - 1, len(self._results) - 1)
        return self._results[idx]

    def send_file(self, path, heat) -> IngestResult:  # pragma: no cover - upload_raw off
        return IngestResult(ok=True, status=200)


@pytest.fixture(autouse=True)
def _fast_stability(monkeypatch):
    # Don't wait the real 0.5s size-stable grace in tests.
    monkeypatch.setattr(watcher_mod, "SIZE_STABLE_GRACE", 0.0)


def _make_watcher(tmp_path: Path, client: FakeClient) -> Watcher:
    (tmp_path / FILENAME).write_text(DO4_BODY, encoding="cp1252")
    cfg = WatcherConfig(
        folder=tmp_path,
        base_url="http://unused",
        upload_raw=False,
        replay_existing=True,
        poll_interval=0.01,
    )
    return Watcher(cfg, client=client)


def _run_until(w: Watcher, cond, timeout: float = 3.0) -> None:
    w.start()
    deadline = time.time() + timeout
    try:
        while time.time() < deadline and not cond():
            time.sleep(0.01)
    finally:
        w.stop()
    time.sleep(0.05)


def test_retries_5xx_until_success(tmp_path):
    client = FakeClient([
        IngestResult(ok=False, status=503, detail="down"),
        IngestResult(ok=False, status=503, detail="down"),
        IngestResult(ok=False, status=0, detail="conn refused"),
        IngestResult(ok=True, status=200, detail="ok"),
    ])
    w = _make_watcher(tmp_path, client)
    _run_until(w, lambda: client.calls >= 4 and FILENAME in w._seen)

    assert client.calls >= 4, "watcher should have retried across polls"
    assert FILENAME in w._seen, "file should be marked seen once it finally sent"
    assert w.stats.sent_heat == 1
    assert FILENAME not in w._size_seen  # pruned after done (M1)


def test_permanent_4xx_not_retried(tmp_path):
    client = FakeClient([IngestResult(ok=False, status=400, detail="bad request")])
    w = _make_watcher(tmp_path, client)
    _run_until(w, lambda: FILENAME in w._seen)

    assert client.calls == 1, "4xx is permanent — must not retry"
    assert FILENAME in w._seen
    assert w.stats.sent_heat == 0
    assert w.stats.errors >= 1


def test_gives_up_after_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher_mod, "MAX_HANDLE_ATTEMPTS", 3)
    client = FakeClient([IngestResult(ok=False, status=503, detail="down")])
    w = _make_watcher(tmp_path, client)
    _run_until(w, lambda: FILENAME in w._seen)

    assert client.calls == 3, "should stop after the attempt cap"
    assert FILENAME in w._seen, "give-up marks the file seen so we stop hammering"
    assert FILENAME not in w._size_seen
    assert w.stats.errors >= 1
