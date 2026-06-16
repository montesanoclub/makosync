"""Split-timeout + in-call retry budget (latency fix).

The live-board heat JSON POSTs on the watcher's single detection thread, so it
gets a shorter timeout (HEAT_TIMEOUT) than the off-thread raw upload and the
relay/download GETs (DEFAULT_TIMEOUT). The in-call backoff is also trimmed to a
small number of fast retries — the watcher's across-poll layer covers longer
outages.
"""

from __future__ import annotations

from urllib import error as urlerror

from makosync import client as client_mod
from makosync.client import DEFAULT_TIMEOUT, HEAT_TIMEOUT, RETRY_DELAYS, IngestClient
from makosync.parser import LaneTime, ParsedHeat


class _FakeResp:
    def __init__(self, status: int = 200, body: bytes = b"{}") -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *a) -> bool:
        return False


def _heat() -> ParsedHeat:
    return ParsedHeat(
        format="do4", dataset="004", event=1, heat=1, round="F", race_id="0001",
        lanes=[LaneTime(lane=5, time="8.13", timers=[8.13])], source_file="004-000-001A-0001.do4",
    )


def test_send_heat_uses_short_timeout(monkeypatch):
    seen: dict[str, float] = {}

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _FakeResp()

    monkeypatch.setattr(client_mod.request, "urlopen", fake_urlopen)
    res = IngestClient("https://makosmeets.com", "tok").send_heat(_heat())
    assert res.ok
    assert seen["timeout"] == HEAT_TIMEOUT == 5.0


def test_send_file_uses_default_timeout(tmp_path, monkeypatch):
    seen: dict[str, float] = {}

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _FakeResp()

    monkeypatch.setattr(client_mod.request, "urlopen", fake_urlopen)
    p = tmp_path / "004-000-001A-0001.do4"
    p.write_text(";1;1;All\nLane5;8.13;;\nABCD1234\n", encoding="cp1252")
    res = IngestClient("https://makosmeets.com", "tok").send_file(p, _heat())
    assert res.ok
    # The large raw upload keeps the generous ceiling — it's off the detection thread.
    assert seen["timeout"] == DEFAULT_TIMEOUT == 8.0


def test_download_file_uses_default_timeout(monkeypatch):
    seen: dict[str, float] = {}

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _FakeResp(body=b"rawbytes")

    monkeypatch.setattr(client_mod.request, "urlopen", fake_urlopen)
    res, data = IngestClient("https://makosmeets.com", "tok").download_file("dolphin-raw/2026/x.do3")
    assert res.ok and data == b"rawbytes"
    assert seen["timeout"] == DEFAULT_TIMEOUT


def test_in_call_retry_count_is_bounded(monkeypatch):
    """A dead link makes exactly len(RETRY_DELAYS)+1 attempts, then gives up —
    no long in-call backoff (the across-poll layer handles persistence)."""
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise urlerror.URLError("dead")

    monkeypatch.setattr(client_mod.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(client_mod.time, "sleep", lambda *_: None)  # don't actually back off
    res = IngestClient("https://makosmeets.com", "tok").send_heat(_heat())
    assert not res.ok
    assert calls["n"] == len(RETRY_DELAYS) + 1 == 3
