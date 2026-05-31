"""Tests for the combined Manager watcher — makosync.manager_watcher.

ManagerWatcher composes MmImportWatcher (pull .do3) + MmWatcher (push official
.mdb results). We monkeypatch ``mm_watcher.read_mdb`` so these never touch a real
.mdb / mdbtools — the point here is the *composition*: which loops run, and how
their stats aggregate.
"""

from __future__ import annotations

import time

import makosync.mm_watcher as mm_watcher
from makosync.client import IngestResult
from makosync.manager_watcher import ManagerWatcher, ManagerWatcherConfig

DO3 = b"0;0;1;A\n1;4.03;;\n2;2.94;;\n"


class FakeClient:
    """Stand-in for IngestClient covering both the import (pending/download) and
    the official-push (send_heat) surfaces ManagerWatcher's sub-watchers use."""

    def __init__(self, files=None, blobs=None):
        self.files = files or []
        self.blobs = blobs or {}
        self.downloads: list[str] = []
        self.sent: list = []

    def fetch_pending(self):
        return IngestResult(ok=True, status=200, detail="ok"), [dict(f) for f in self.files]

    def download_file(self, key):
        self.downloads.append(key)
        if key in self.blobs:
            return IngestResult(ok=True, status=200, detail="ok"), self.blobs[key]
        return IngestResult(ok=False, status=404, detail="nope"), b""

    def send_heat(self, heat, tier="unofficial", source="dolphin"):
        self.sent.append((heat, tier, source))
        return IngestResult(ok=True, status=200, detail="ok")


def _entry(meet="015", event=22, heat=2, race="0005"):
    return {
        "race_id": race, "meet_id": meet, "event": event, "heat": heat,
        "src_name": f"{meet}-000-00F{race}.do3",
        "out_name": f"{meet}-000-E{event:02d}_H{heat:02d}.do3",
        "key": f"dolphin-raw/2026/{meet}-000-00F{race}.do3",
    }


def _cfg(tmp_path, **kw):
    kw.setdefault("import_dir", tmp_path / "imp")
    return ManagerWatcherConfig(mdb_path=tmp_path / "meet.mdb", base_url="http://x", token="t", **kw)


def test_resolved_import_dir_defaults_to_mdb_parent(tmp_path):
    cfg = ManagerWatcherConfig(mdb_path=tmp_path / "sub" / "meet.mdb", base_url="http://x")
    assert cfg.resolved_import_dir() == tmp_path / "sub"
    cfg2 = ManagerWatcherConfig(mdb_path=tmp_path / "meet.mdb", base_url="http://x",
                                import_dir=tmp_path / "elsewhere")
    assert cfg2.resolved_import_dir() == tmp_path / "elsewhere"


def test_flags_select_which_subwatchers_exist(tmp_path):
    both = ManagerWatcher(_cfg(tmp_path), client=FakeClient())
    assert both._mm is not None and both._imp is not None
    pull_only = ManagerWatcher(_cfg(tmp_path, push_official=False), client=FakeClient())
    assert pull_only._mm is None and pull_only._imp is not None
    push_only = ManagerWatcher(_cfg(tmp_path, pull_import=False), client=FakeClient())
    assert push_only._mm is not None and push_only._imp is None


def test_pull_only_writes_file_without_reading_mdb(tmp_path):
    e = _entry()
    client = FakeClient([e], {e["key"]: DO3})
    w = ManagerWatcher(_cfg(tmp_path, push_official=False), client=client)
    w.run_once()
    assert (tmp_path / "imp" / e["out_name"]).read_bytes() == DO3
    assert w.stats.sent_file == 1
    assert client.sent == []  # never pushed official results


def test_run_once_drives_both_loops(tmp_path, monkeypatch):
    reads = {"n": 0}

    def fake_read(path, *a, **k):
        reads["n"] += 1
        return []  # no heats -> send_heat not called, but the read happened

    monkeypatch.setattr(mm_watcher, "read_mdb", fake_read)
    e = _entry()
    client = FakeClient([e], {e["key"]: DO3})
    w = ManagerWatcher(_cfg(tmp_path), client=client)
    w.run_once()
    assert reads["n"] == 1                                  # .mdb read ran
    assert (tmp_path / "imp" / e["out_name"]).exists()      # .do3 pulled
    assert w.stats.sent_file == 1


def test_stats_aggregate_counts_and_errors(tmp_path):
    w = ManagerWatcher(_cfg(tmp_path), client=FakeClient())
    w._imp.stats.sent_file = 3
    w._imp.stats.errors = 1
    w._imp.stats.last_event_at = 100.0
    w._imp.stats.last_file = "E22_H02.do3"
    w._mm.stats.sent_heat = 5
    w._mm.stats.errors = 2
    s = w.stats
    assert s.sent_file == 3
    assert s.sent_heat == 5
    assert s.errors == 3                                    # 1 + 2
    assert s.last_file == "E22_H02.do3"                     # most-recent by last_event_at


def test_start_stop_is_running(tmp_path, monkeypatch):
    monkeypatch.setattr(mm_watcher, "read_mdb", lambda *a, **k: [])
    w = ManagerWatcher(_cfg(tmp_path), client=FakeClient())
    assert not w.is_running()
    w.start()
    assert w.is_running()
    w.stop()
    for _ in range(60):                                     # threads wake on the stop event
        if not w.is_running():
            break
        time.sleep(0.05)
    assert not w.is_running()
