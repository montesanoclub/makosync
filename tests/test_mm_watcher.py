"""MM watcher dedup + retry-cap semantics.

A heat is POSTed only when its lane content changes; an unchanged heat is
skipped. Transient 5xx/network failures retry on later cycles but give up after
a cap (so a sustained outage can't re-POST every heat forever); a permanent 4xx
is not retried. Mirrors the Dolphin watcher's give-up contract
(`test_watcher_retry.py`) for the MM path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from makosync import mm_watcher as mm_mod
from makosync.client import IngestResult
from makosync.mm_watcher import MmWatcher, MmWatcherConfig
from makosync.parser import LaneTime, ParsedHeat


def _heat(time: str = "30.00") -> ParsedHeat:
    return ParsedHeat(
        format="mm", dataset="mm", event=5, heat=1, round="F", race_id="",
        lanes=[LaneTime(lane=4, time=time, place=1)],
    )


class FakeClient:
    """Scripted send_heat results, no network."""

    def __init__(self, results: list[IngestResult]) -> None:
        self._results = results
        self.calls = 0
        self.last_kwargs: dict = {}

    def send_heat(self, heat, tier: str = "unofficial", source: str = "dolphin") -> IngestResult:
        self.calls += 1
        self.last_kwargs = {"tier": tier, "source": source}
        return self._results[min(self.calls - 1, len(self._results) - 1)]


@pytest.fixture()
def box():
    """A mutable holder the monkeypatched read_mdb returns each cycle."""
    return {"heats": [_heat()]}


def _make(client: FakeClient, monkeypatch, box) -> MmWatcher:
    monkeypatch.setattr(mm_mod, "read_mdb", lambda *a, **k: list(box["heats"]))
    cfg = MmWatcherConfig(mdb_path=Path("unused.mdb"), base_url="http://unused")
    return MmWatcher(cfg, client=client)


def test_sends_official_mm_tier(monkeypatch, box):
    client = FakeClient([IngestResult(ok=True, status=200)])
    w = _make(client, monkeypatch, box)
    w._cycle()
    assert client.calls == 1
    assert client.last_kwargs == {"tier": "official", "source": "mm"}
    assert w.stats.sent_heat == 1


def test_unchanged_heat_not_resent(monkeypatch, box):
    client = FakeClient([IngestResult(ok=True, status=200)])
    w = _make(client, monkeypatch, box)
    w._cycle()
    w._cycle()  # identical content -> deduped
    assert client.calls == 1


def test_changed_heat_is_resent(monkeypatch, box):
    client = FakeClient([IngestResult(ok=True, status=200)])
    w = _make(client, monkeypatch, box)
    w._cycle()
    box["heats"] = [_heat("29.50")]  # a re-timed result
    w._cycle()
    assert client.calls == 2


def test_transient_5xx_retried_then_capped(monkeypatch, box):
    monkeypatch.setattr(mm_mod, "MAX_SEND_ATTEMPTS", 3)
    client = FakeClient([IngestResult(ok=False, status=503, detail="down")])
    w = _make(client, monkeypatch, box)
    for _ in range(6):
        w._cycle()
    assert client.calls == 3, "should retry across cycles then give up at the cap"
    assert w.stats.errors >= 3


def test_capped_heat_retries_again_when_content_changes(monkeypatch, box):
    monkeypatch.setattr(mm_mod, "MAX_SEND_ATTEMPTS", 2)
    # First content fails forever; once it gives up, new content should re-arm.
    client = FakeClient([
        IngestResult(ok=False, status=503),
        IngestResult(ok=False, status=503),
        IngestResult(ok=True, status=200),  # for the new content
    ])
    w = _make(client, monkeypatch, box)
    w._cycle(); w._cycle()           # 2 attempts -> capped, given up
    w._cycle()                        # same content -> skipped (no call)
    assert client.calls == 2
    box["heats"] = [_heat("28.00")]  # content changes -> re-arm
    w._cycle()
    assert client.calls == 3


def test_permanent_4xx_not_retried(monkeypatch, box):
    client = FakeClient([IngestResult(ok=False, status=400, detail="bad request")])
    w = _make(client, monkeypatch, box)
    w._cycle()
    w._cycle()
    assert client.calls == 1, "4xx is permanent — must not retry until content changes"
    assert w.stats.sent_heat == 0
