"""Stability gate / checksum fast-path (latency fix — no silent result loss).

A .do3/.do4 ends with a Dolphin hex checksum line written LAST, so once that
line is present the file is fully written and can be trusted on first sight —
no second confirming poll, cutting per-heat latency. A file that is still being
written lacks the checksum and must NOT be trusted on first sight: parsing a
truncated file can yield a heat with zero timed lanes, which _handle marks
permanently done — a silently dropped result. .csv has no end sentinel and so
always falls back to the size-stable gate.
"""

from __future__ import annotations

from pathlib import Path

import makosync.watcher as wm
from makosync.watcher import Watcher, WatcherConfig

# Mid-file timed lane (matches the real-world sample) so a truncated read would
# parse to zero timed lanes — the silent-loss hazard the checksum guard prevents.
COMPLETE_DO4 = (
    ";1;1;All\n"
    "Lane1;0;0;0\nLane2;0;0;0\nLane3;0;0;0\nLane4;0;0;0\n"
    "Lane5;8.13;;\n"
    "Lane6;0;0;0\nLane7;0;0;0\nLane8;0;0;0\nLane9;0;0;0\nLane10;0;0;0\n"
    "1F72DF569DD6BBD7\n"
)
PARTIAL_DO4 = (  # header + a couple of lanes, checksum not written yet
    ";1;1;All\n"
    "Lane1;0;0;0\nLane2;0;0;0\n"
)


def _watcher(folder: Path) -> Watcher:
    cfg = WatcherConfig(folder=folder, base_url="http://unused", upload_raw=False)
    return Watcher(cfg)


def test_complete_do4_trusted_on_first_sight(tmp_path):
    p = tmp_path / "004-000-001A-0001.do4"
    p.write_text(COMPLETE_DO4, encoding="cp1252")
    w = _watcher(tmp_path)
    # Checksum present -> ready immediately, no second poll.
    assert w._is_stable(p) is True


def test_partial_do4_not_trusted_until_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(wm, "SIZE_STABLE_GRACE", 0.0)
    p = tmp_path / "004-000-001A-0002.do4"
    p.write_text(PARTIAL_DO4, encoding="cp1252")
    w = _watcher(tmp_path)
    # First sight, no checksum -> held (this is the silent-loss guard).
    assert w._is_stable(p) is False
    # Dolphin finishes the file (checksum appended, size grows).
    p.write_text(COMPLETE_DO4, encoding="cp1252")
    assert w._is_stable(p) is False  # size changed this tick -> re-track, still held
    # Size now stable; with grace=0 the fallback gate clears it.
    assert w._is_stable(p) is True


def test_csv_not_trusted_on_first_sight(tmp_path):
    # .csv has no checksum sentinel, so the fast path must not apply — it falls
    # back to the (safe) two-observation size-stable gate.
    p = tmp_path / "004_Event_1_Heat_1_Race_5_x.csv"
    p.write_text("Lane,TimerA,Final\n5,8.13,8.13\n", encoding="cp1252")
    w = _watcher(tmp_path)
    assert w._is_stable(p) is False


def test_looks_complete_handles_missing_file(tmp_path):
    # A vanished/locked file must report "not complete", not raise.
    w = _watcher(tmp_path)
    assert w._looks_complete(tmp_path / "gone.do4") is False
