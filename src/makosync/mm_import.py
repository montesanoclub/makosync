"""Meet Manager IMPORT relay — pull relayed Dolphin .do3 files into MM's folder.

The mirror image of the Dolphin :class:`~makosync.watcher.Watcher`. The Dolphin
PC pushes every raw file to makosmeets; this runs on the *scoring* PC and pulls
the .do3s back down — renamed ``<original>_E<ev>_H<ht>.do3`` (event/heat
recovered server-side from the paired do4 and **suffixed** onto the original
name, so the Dolphin race number survives for MM's Get-Times-by-Race import) —
into the folder Meet Manager
imports Dolphin times from, then pops a toast so the operator knows to Get
Times. Routing through makosmeets means the two meet PCs never talk directly:
both only make outbound HTTPS, no LAN/firewall config between them.

Same ``start`` / ``stop`` / ``is_running`` / ``stats`` / ``on_event`` interface
as the other watchers so the GUI and CLI drive it polymorphically.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .client import IngestClient
from .notify import notify as toast_notify
from .watcher import WatcherStats

logger = logging.getLogger(__name__)

POLL_INTERVAL = 2.0
MAX_ATTEMPTS = 8  # per-file transient-failure cap (download/write) before giving up


@dataclass
class MmImportConfig:
    base_url: str
    import_dir: Path
    token: str = ""
    poll_interval: float = POLL_INTERVAL
    notify: bool = True


class MmImportWatcher:
    """Run with ``start()`` / ``stop()``. Thread-safe; tk-friendly (no Tk here)."""

    def __init__(
        self,
        config: MmImportConfig,
        client: Optional[IngestClient] = None,
        on_event: Optional[Callable[[str], None]] = None,
        notifier: Optional[Callable[[str, str], bool]] = None,
    ):
        self.cfg = config
        self.client = client or IngestClient(config.base_url, config.token)
        self.on_event = on_event or (lambda msg: None)
        self.notify = notifier or toast_notify
        self.stats = WatcherStats()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._seen: set[str] = set()           # out_names handled this run
        self._attempts: dict[str, int] = {}     # out_name -> transient failure count

    # ---- lifecycle ----------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        d = Path(self.cfg.import_dir)
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._log(f"could not create import folder {d}: {e}")
        # Pre-mark files already in the folder so a restart doesn't re-toast a
        # heat the operator already imported.
        existing = {p.name for p in d.glob("*.do3")} if d.is_dir() else set()
        self._seen.update(existing)
        self._log(
            f"importing to {d} every {self.cfg.poll_interval:g}s "
            f"({len(existing)} already present) — pulling Dolphin .do3 for Meet Manager"
        )
        self._thread = threading.Thread(target=self._loop, name="makosync-mmimport", daemon=True)
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
            except Exception as e:  # noqa: BLE001 — never let the loop die
                self.stats.errors += 1
                self.stats.last_error = str(e)
                logger.exception("mm-import cycle error")
                self._log(f"cycle error (will retry): {e}")
            self._stop.wait(timeout=self.cfg.poll_interval)

    def _cycle(self) -> None:
        res, files = self.client.fetch_pending()
        if not res.ok:
            self.stats.errors += 1
            self.stats.last_error = f"{res.status} {res.detail}"
            self._log(f"pending fetch failed (will retry): {res.status} {res.detail}")
            return
        for f in files:
            if self._stop.is_set():
                break
            self._handle(f)

    def _handle(self, f: dict) -> None:
        out_name = str(f.get("out_name") or "")
        key = str(f.get("key") or "")
        if not out_name or not key or out_name in self._seen:
            return
        dest = Path(self.cfg.import_dir) / out_name
        if dest.exists():
            self._seen.add(out_name)  # already imported (e.g. a prior session)
            return

        dres, data = self.client.download_file(key)
        if not dres.ok or not data:
            self._note_failure(out_name, f"download {dres.status} {dres.detail}")
            return
        try:
            # Atomic write: MM never sees a half-downloaded file mid-poll.
            # mkdir here (not just in start()) so a one-shot _cycle() or a folder
            # deleted mid-meet still lands the file.
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(dest)
        except OSError as e:
            self._note_failure(out_name, f"write: {e}")
            return

        self._seen.add(out_name)
        self._attempts.pop(out_name, None)
        self.stats.sent_file += 1
        self.stats.last_file = out_name
        self.stats.last_event_at = time.time()
        ev, ht = f.get("event"), f.get("heat")
        self._log(f"pulled E{ev} H{ht} -> {out_name} ({len(data)} bytes)")
        if self.cfg.notify:
            try:
                self.notify("MakoSync", f"Event {ev} Heat {ht} dolphin results pulled from makos meets")
            except Exception:  # noqa: BLE001
                logger.exception("toast failed")

    def _note_failure(self, name: str, why: str) -> None:
        n = self._attempts.get(name, 0) + 1
        self._attempts[name] = n
        self.stats.errors += 1
        self.stats.last_error = why
        if n >= MAX_ATTEMPTS:
            self._seen.add(name)  # give up until restart so we don't loop forever
            self._log(f"giving up on {name} after {n} attempts: {why}")
        else:
            self._log(f"{name} failed ({n}/{MAX_ATTEMPTS}), will retry: {why}")

    # ---- helpers ------------------------------------------------------

    def _log(self, msg: str) -> None:
        logger.info(msg)
        try:
            self.on_event(msg)
        except Exception:  # noqa: BLE001
            logger.exception("on_event callback failed")
