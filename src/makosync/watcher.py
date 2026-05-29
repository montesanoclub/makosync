"""Folder watcher — poll a Dolphin output folder, parse new files, ship them.

Polling (not OS file-events) because the Dolphin software often writes to a
slow/SMB share where inotify-style events are unreliable. Each file is
**immutable** once written, so once we've sent it we never look at it again.

Pipeline per new file:

  1. Detect (polling, ext filter, size-stable check).
  2. Parse (do3/do4/csv).
  3. POST JSON to ``/ingest/heat`` — fast path. On success:
  4. POST raw file to ``/ingest/file`` — slow path. Failures here don't block
     future files (heat JSON is what the live TV needs).

State (which files we've sent, counters) lives in memory — a restart will
re-send unless ``--no-replay`` is passed. That's the safe default for a
meet PC reboot mid-meet.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .client import IngestClient, IngestResult
from .parser import parse_file

logger = logging.getLogger(__name__)

EXTS_DEFAULT = (".do4", ".do3")
EXTS_WITH_CSV = (".do4", ".do3", ".csv")
POLL_INTERVAL = 2.0          # seconds between folder scans
SIZE_STABLE_GRACE = 0.5      # seconds — wait for size to stop changing before parsing
MAX_HANDLE_ATTEMPTS = 8      # give up on a file after this many transient failures


@dataclass
class WatcherStats:
    sent_heat: int = 0
    sent_file: int = 0
    errors: int = 0
    last_file: str = ""
    last_error: str = ""
    last_event_at: float = 0.0


@dataclass
class WatcherConfig:
    folder: Path
    base_url: str
    token: str = ""
    include_csv: bool = False
    replay_existing: bool = False
    upload_raw: bool = True  # also archive the raw .do to R2 (forensic copy)
    poll_interval: float = POLL_INTERVAL
    tier: str = "unofficial"


@dataclass
class _RawJob:
    path: Path
    heat: object  # ParsedHeat


class Watcher:
    """Run with ``start()`` / ``stop()``. Thread-safe; tk-friendly (no Tk imports here)."""

    def __init__(
        self,
        config: WatcherConfig,
        client: Optional[IngestClient] = None,
        on_event: Optional[Callable[[str], None]] = None,
    ):
        self.cfg = config
        self.client = client or IngestClient(config.base_url, config.token)
        self.on_event = on_event or (lambda msg: None)
        self.stats = WatcherStats()
        self._seen: set[str] = set()
        self._raw_q: "queue.Queue[_RawJob]" = queue.Queue()
        self._stop = threading.Event()
        self._main_thread: Optional[threading.Thread] = None
        self._raw_thread: Optional[threading.Thread] = None
        self._size_seen: dict[str, tuple[int, float]] = {}  # path -> (size, first_seen_at)
        self._attempts: dict[str, int] = {}  # name -> transient-failure count

    # ---- lifecycle ----------------------------------------------------

    def start(self) -> None:
        if self._main_thread and self._main_thread.is_alive():
            return
        self._stop.clear()

        # Pre-mark existing files as seen (unless replay) so we don't dump a backlog.
        if not self.cfg.replay_existing:
            for p in self._scan():
                self._seen.add(p.name)
            self._log(f"watching {self.cfg.folder} — {len(self._seen)} existing files skipped")
        else:
            self._log(f"watching {self.cfg.folder} — will replay existing files")

        self._main_thread = threading.Thread(target=self._main_loop, name="makosync-poll", daemon=True)
        self._main_thread.start()

        if self.cfg.upload_raw:
            self._raw_thread = threading.Thread(target=self._raw_loop, name="makosync-raw", daemon=True)
            self._raw_thread.start()

    def stop(self) -> None:
        self._stop.set()

    def is_running(self) -> bool:
        return self._main_thread is not None and self._main_thread.is_alive()

    # ---- main loop ----------------------------------------------------

    def _main_loop(self) -> None:
        while not self._stop.is_set():
            try:
                files = self._scan()
                present = {p.name for p in files}
                for p in files:
                    if p.name in self._seen:
                        continue
                    if not self._is_stable(p):
                        continue  # try again next tick
                    if self._handle(p):
                        self._mark_done(p.name)
                    else:
                        self._note_failure(p.name)
                self._prune_size_seen(present)
            except Exception as e:
                self.stats.errors += 1
                self.stats.last_error = str(e)
                logger.exception("watcher loop error")
                self._log(f"loop error: {e}")
            # Sleep in small chunks so stop() is responsive.
            self._stop.wait(timeout=self.cfg.poll_interval)

    def _mark_done(self, name: str) -> None:
        """File fully handled (sent, or permanently un-sendable) — never look again."""
        self._seen.add(name)
        self._size_seen.pop(name, None)
        self._attempts.pop(name, None)

    def _note_failure(self, name: str) -> None:
        """Transient failure — leave unseen so the next poll retries, up to a cap."""
        n = self._attempts.get(name, 0) + 1
        self._attempts[name] = n
        if n >= MAX_HANDLE_ATTEMPTS:
            self.stats.errors += 1
            self.stats.last_error = f"gave up on {name} after {n} attempts"
            self._log(f"giving up on {name} after {n} attempts")
            self._mark_done(name)

    def _prune_size_seen(self, present: set[str]) -> None:
        """Drop stability-tracking entries for files that are gone or already handled."""
        stale = [k for k in self._size_seen if k not in present or k in self._seen]
        for k in stale:
            self._size_seen.pop(k, None)

    def _scan(self) -> list[Path]:
        exts = EXTS_WITH_CSV if self.cfg.include_csv else EXTS_DEFAULT
        if not self.cfg.folder.exists():
            return []
        files = []
        for p in self.cfg.folder.iterdir():
            if p.is_file() and p.suffix.lower() in exts:
                files.append(p)
        files.sort()
        return files

    def _is_stable(self, p: Path) -> bool:
        """Avoid reading a half-written file. Size must be unchanged across a tick."""
        try:
            size = p.stat().st_size
        except OSError:
            return False
        prev = self._size_seen.get(p.name)
        now = time.monotonic()
        if prev is None:
            self._size_seen[p.name] = (size, now)
            return False
        prev_size, first_seen = prev
        if prev_size != size:
            self._size_seen[p.name] = (size, now)
            return False
        return (now - first_seen) >= SIZE_STABLE_GRACE

    def _handle(self, p: Path) -> bool:
        """Return True when the file is fully handled (mark seen), False to retry
        on the next poll (transient: file still locked, or server 5xx/network)."""
        heat = parse_file(p)
        if heat is None:
            # Could be a half-written/locked file (transient on Windows) or genuine
            # junk. Treat as retryable; the attempt cap stops us looping forever.
            self._log(f"could not parse {p.name} — will retry")
            return False
        if not heat.timed_lanes:
            # No real times yet — heat file written but lanes are all empty/0.
            # Don't POST a JSON heat with zero lanes (server would drop it anyway),
            # but still queue the raw file so the forensic copy lands.
            self._log(f"no lane times in {p.name} — queuing raw only")
            if self.cfg.upload_raw:
                self._raw_q.put(_RawJob(p, heat))
            return True

        res: IngestResult = self.client.send_heat(heat, tier=self.cfg.tier)
        self.stats.last_event_at = time.time()
        self.stats.last_file = p.name
        if res.ok:
            self.stats.sent_heat += 1
            lanes_summary = ", ".join(f"L{ln.lane}={ln.time}" for ln in heat.timed_lanes)
            self._log(f"sent heat {p.name} ({lanes_summary})")
            if self.cfg.upload_raw:
                self._raw_q.put(_RawJob(p, heat))
            return True

        self.stats.errors += 1
        self.stats.last_error = f"{res.status} {res.detail}"
        # 4xx is a permanent rejection (bad request) — don't retry it forever.
        # 5xx / network (status 0) is transient — retry on the next poll.
        permanent = 400 <= res.status < 500
        verb = "rejected" if permanent else "failed (will retry)"
        self._log(f"heat {verb} {p.name}: {res.status} {res.detail}")
        return permanent

    # ---- raw upload worker -------------------------------------------

    def _raw_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._raw_q.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                res = self.client.send_file(job.path, job.heat)
                if res.ok:
                    self.stats.sent_file += 1
                    self._log(f"sent raw {job.path.name}")
                else:
                    self.stats.errors += 1
                    self.stats.last_error = f"raw {res.status} {res.detail}"
                    self._log(f"raw send failed {job.path.name}: {res.status} {res.detail}")
            except Exception as e:
                self.stats.errors += 1
                self.stats.last_error = f"raw exception: {e}"
                logger.exception("raw upload error")

    # ---- helpers ------------------------------------------------------

    def _log(self, msg: str) -> None:
        logger.info(msg)
        try:
            self.on_event(msg)
        except Exception:
            logger.exception("on_event callback failed")
