"""Meet Manager watcher — poll the MM ``.mdb``, push changed official heats.

The Dolphin :class:`~makosync.watcher.Watcher` is file-oriented: each Dolphin
file is immutable, so dedup is by filename. The MM ``.mdb`` is the opposite — one
mutable file the operator keeps editing all meet — so here we re-read it every
``poll_interval`` and dedup by **per-heat content hash**: a heat is POSTed only
when its lane tuples change since the last cycle.

Same informal interface as ``Watcher`` (``start`` / ``stop`` / ``is_running`` /
``stats`` / ``on_event``) so the GUI and CLI can drive either polymorphically.

Lock-safe and never blocks MM: :func:`~makosync.mdb_reader.read_mdb` reads a temp
copy; if the copy or ODBC open fails (MM mid-write), the cycle logs and retries
on the next tick.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .client import IngestClient, IngestResult
from .mdb_reader import read_mdb
from .parser import ParsedHeat
from .watcher import WatcherStats

logger = logging.getLogger(__name__)

POLL_INTERVAL = 12.0       # seconds between MDB re-reads (default; tune to MM flush latency)
MAX_SEND_ATTEMPTS = 8      # give up re-POSTing one heat after this many transient failures


@dataclass
class MmWatcherConfig:
    mdb_path: Path
    base_url: str
    token: str = ""
    poll_interval: float = POLL_INTERVAL


def heat_digest(heat: ParsedHeat) -> str:
    """Stable hash of a heat's published state: lane tuples (lane/time/place/dq)
    PLUS the event's ``scored`` flag.

    ``scored`` MUST be in the hash. Scoring an event in Meet Manager (Event_stat
    'A' -> 'S') flips ``scored`` without touching any lane time or place, so a
    digest over lanes alone would be byte-identical before and after scoring — the
    watcher would never re-POST, and the server would never learn the event is
    scored (so the official result would never reach the TV). The same applies in
    reverse if the operator un-scores to fix a mistake.
    """
    h = hashlib.sha1()
    h.update(f"scored={heat.scored}|".encode("utf-8"))
    for ln in sorted(heat.timed_lanes, key=lambda l: l.lane):
        h.update(f"{ln.lane}|{ln.time}|{ln.place}|{ln.dq}".encode("utf-8"))
    return h.hexdigest()


class MmWatcher:
    """Run with ``start()`` / ``stop()``. Thread-safe; tk-friendly (no Tk here)."""

    def __init__(
        self,
        config: MmWatcherConfig,
        client: Optional[IngestClient] = None,
        on_event: Optional[Callable[[str], None]] = None,
    ):
        self.cfg = config
        self.client = client or IngestClient(config.base_url, config.token)
        self.on_event = on_event or (lambda msg: None)
        self.stats = WatcherStats()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._hashes: dict[tuple[int, int], str] = {}  # (event, heat) -> last-sent digest
        self._fails: dict[tuple[int, int], int] = {}    # (event, heat) -> consecutive transient failures

    # ---- lifecycle ----------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._log(
            f"reading {self.cfg.mdb_path} every {self.cfg.poll_interval:g}s "
            f"(Meet Manager — official results)"
        )
        self._thread = threading.Thread(target=self._loop, name="makosync-mm", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---- main loop ----------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._cycle()
            except Exception as e:
                # copy/ODBC/read failure (MM mid-write, lock, missing driver) —
                # surface it and retry next cycle. Never crash, never block MM.
                self.stats.errors += 1
                self.stats.last_error = str(e)
                logger.exception("MM read cycle error")
                self._log(f"MM read failed (will retry): {e}")
            self._stop.wait(timeout=self.cfg.poll_interval)

    def _cycle(self) -> None:
        heats = read_mdb(self.cfg.mdb_path)
        sent = 0
        for heat in heats:
            key = (heat.event, heat.heat)
            digest = heat_digest(heat)
            if self._hashes.get(key) == digest:
                continue  # unchanged since last read
            heat.race_id = digest[:12]  # trace/idempotency tag for this content
            res: IngestResult = self.client.send_heat(heat, tier="official", source="mm")
            self.stats.last_event_at = time.time()
            self.stats.last_file = f"E{heat.event} H{heat.heat}"
            if res.ok:
                self._mark_sent(key, digest)
                self.stats.sent_heat += 1
                sent += 1
                self._log(f"official {self._summary(heat)}")
                continue
            self.stats.errors += 1
            self.stats.last_error = f"{res.status} {res.detail}"
            # 4xx = malformed/rejected: retrying the same content won't help, so
            # record the hash to skip it until the heat's content changes.
            if 400 <= res.status < 500:
                self._mark_sent(key, digest)
                self._log(f"official E{heat.event} H{heat.heat} rejected: {res.status} {res.detail}")
                continue
            # 5xx / network is transient: retry next cycle — but cap it so a
            # sustained outage can't re-POST every heat forever (mirrors the
            # Dolphin Watcher's MAX_HANDLE_ATTEMPTS give-up).
            n = self._fails.get(key, 0) + 1
            self._fails[key] = n
            if n >= MAX_SEND_ATTEMPTS:
                self._mark_sent(key, digest)  # give up until this heat's content changes
                self._log(f"giving up on E{heat.event} H{heat.heat} after {n} attempts: {res.status} {res.detail}")
            else:
                self._log(f"official E{heat.event} H{heat.heat} failed (will retry {n}/{MAX_SEND_ATTEMPTS}): {res.status} {res.detail}")
        if sent:
            self._log(f"{sent} heat(s) updated")

    def _mark_sent(self, key: tuple[int, int], digest: str) -> None:
        """Record a heat as delivered/settled — skip it until its content changes."""
        self._hashes[key] = digest
        self._fails.pop(key, None)

    # ---- helpers ------------------------------------------------------

    @staticmethod
    def _summary(heat: ParsedHeat) -> str:
        lanes = ", ".join(
            f"L{ln.lane}={ln.time}" + (f"#{ln.place}" if ln.place else "")
            for ln in sorted(heat.timed_lanes, key=lambda l: l.lane)
        )
        return f"E{heat.event} H{heat.heat}: {lanes}"

    def _log(self, msg: str) -> None:
        logger.info(msg)
        try:
            self.on_event(msg)
        except Exception:
            logger.exception("on_event callback failed")
