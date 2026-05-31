"""Combined Meet Manager watcher — pull Dolphin .do3 in *and* push official results.

The Meet Manager PC does two jobs, so MakoSync's **Manager** mode runs both at
once, each on its own cadence:

  * **pull** the relayed Dolphin ``.do3`` files into MM's import folder (fast,
    ~2s) so the operator can Get-Times — :class:`~makosync.mm_import.MmImportWatcher`;
  * **push** the reconciled *official* results (places, DQs) read from the MM
    ``.mdb`` (gentler, ~12s) — :class:`~makosync.mm_watcher.MmWatcher`.

It composes the two single-purpose watchers behind the same ``start`` / ``stop``
/ ``is_running`` / ``stats`` interface the other watchers expose, so the GUI and
CLI drive it polymorphically. ``stats`` is aggregated: official pushes
(``sent_heat``), files pulled (``sent_file``), and the combined error count.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .client import IngestClient
from .mm_import import MmImportConfig, MmImportWatcher
from .mm_watcher import MmWatcher, MmWatcherConfig
from .watcher import WatcherStats

logger = logging.getLogger(__name__)


@dataclass
class ManagerWatcherConfig:
    mdb_path: Path
    base_url: str
    token: str = ""
    poll_interval: float = 12.0          # .mdb read cadence (official-results push)
    import_dir: Optional[Path] = None    # .do3 drop folder (defaults to the .mdb's parent)
    import_poll: float = 2.0             # .do3 pull cadence
    notify: bool = True
    push_official: bool = True           # run the .mdb -> official-results loop
    pull_import: bool = True             # run the .do3 import-pull loop

    def resolved_import_dir(self) -> Path:
        """Where to drop pulled .do3 — the configured folder, else the .mdb's folder
        (which is the folder Meet Manager's Get-Times picker already points at)."""
        return Path(self.import_dir) if self.import_dir else Path(self.mdb_path).parent


class ManagerWatcher:
    """Run with ``start()`` / ``stop()``. Composes the two MM watchers; tk-friendly."""

    def __init__(
        self,
        config: ManagerWatcherConfig,
        client: Optional[IngestClient] = None,
        on_event: Optional[Callable[[str], None]] = None,
        notifier: Optional[Callable[[str, str], bool]] = None,
    ):
        self.cfg = config
        self.on_event = on_event or (lambda msg: None)
        # One client shared by both loops (same base_url + token).
        client = client or IngestClient(config.base_url, config.token)
        self._mm: Optional[MmWatcher] = None
        self._imp: Optional[MmImportWatcher] = None
        if config.pull_import:
            self._imp = MmImportWatcher(
                MmImportConfig(
                    base_url=config.base_url,
                    import_dir=config.resolved_import_dir(),
                    token=config.token,
                    poll_interval=config.import_poll,
                    notify=config.notify,
                ),
                client=client,
                on_event=self.on_event,
                notifier=notifier,
            )
        if config.push_official:
            self._mm = MmWatcher(
                MmWatcherConfig(
                    mdb_path=Path(config.mdb_path),
                    base_url=config.base_url,
                    token=config.token,
                    poll_interval=config.poll_interval,
                ),
                client=client,
                on_event=self.on_event,
            )

    def _subs(self) -> list:
        """Live sub-watchers, import first (it's the latency-sensitive one)."""
        return [w for w in (self._imp, self._mm) if w is not None]

    # ---- lifecycle ----------------------------------------------------

    def start(self) -> None:
        for w in self._subs():
            w.start()

    def stop(self) -> None:
        for w in self._subs():
            w.stop()

    def is_running(self) -> bool:
        return any(w.is_running() for w in self._subs())

    def run_once(self) -> None:
        """One cycle of each sub-watcher, inline — for --once smoke tests."""
        for w in self._subs():
            w._cycle()

    # ---- aggregated stats ---------------------------------------------

    @property
    def stats(self) -> WatcherStats:
        """A fresh snapshot combining both sub-watchers' counters.

        Best-effort: the sub-watchers' threads mutate their stats without a lock,
        but each field is a GIL-atomic int/str and we only read for a 1 Hz status
        display. We snapshot each sub-watcher's coupled fields into locals together
        so the reported (last_event_at, last_file) pair stays self-consistent.
        """
        s = WatcherStats()
        if self._mm:
            s.sent_heat = self._mm.stats.sent_heat
        if self._imp:
            s.sent_file = self._imp.stats.sent_file
        last_at = 0.0
        for w in self._subs():
            st = w.stats
            errors, last_error, ev_at, last_file = st.errors, st.last_error, st.last_event_at, st.last_file
            s.errors += errors
            if last_error:
                s.last_error = last_error
            if ev_at > last_at:
                last_at = ev_at
                s.last_file = last_file
        s.last_event_at = last_at
        return s
