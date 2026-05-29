"""Entry point for `makosync` — GUI by default, --headless for CLI.

Three modes (``--mode``):
  * ``dolphin`` (default) — watch a CTS Dolphin folder, push unofficial times.
  * ``manager`` — read the Hy-Tek Meet Manager ``.mdb`` and push official results.
  * ``mm-import`` — pull relayed Dolphin ``.do3`` files (renamed with event/heat)
    into the folder Meet Manager imports from; toast on each new heat.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from .watcher import Watcher, WatcherConfig


def _safe_print(msg: str) -> None:
    """Print, tolerating a missing stdout (the windowed .exe has none)."""
    try:
        print(msg, flush=True)
    except Exception:
        pass


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="makosync",
        description="Push live swim results to makosmeets, from CTS Dolphin files or a Meet Manager .mdb.",
    )
    ap.add_argument("--headless", action="store_true", help="No GUI — run from the CLI.")
    ap.add_argument("--mode", default="dolphin", choices=["dolphin", "manager", "mm-import"],
                    help="'dolphin' (folder watch), 'manager' (Hy-Tek Meet Manager .mdb), "
                         "or 'mm-import' (pull relayed Dolphin .do3 into MM's import folder).")
    ap.add_argument("--url", help="Base URL of the ingest server, e.g. http://localhost:8080")
    ap.add_argument("--token", default="", help="Optional bearer token; omitted if blank.")
    ap.add_argument("--once", action="store_true",
                    help="Run one pass/cycle and exit (handy for smoke tests).")

    g_dolphin = ap.add_argument_group("dolphin mode")
    g_dolphin.add_argument("--folder", help="Dolphin output folder (required for --mode dolphin).")
    g_dolphin.add_argument("--include-csv", action="store_true", help="Also watch .csv files.")
    g_dolphin.add_argument("--no-raw", action="store_true",
                           help="Don't archive raw files (JSON heat only).")
    g_dolphin.add_argument("--replay-existing", action="store_true",
                           help="Also send files that already exist when the watcher starts.")
    g_dolphin.add_argument("--tier", default="unofficial", choices=["unofficial", "official"],
                           help="Tier label sent with each Dolphin heat (default: unofficial).")

    g_mm = ap.add_argument_group("manager mode")
    g_mm.add_argument("--mdb-path", help="Path to the live Meet Manager .mdb (required for --mode manager).")
    g_mm.add_argument("--poll-interval", type=float, default=12.0,
                      help="Seconds between MDB re-reads in Manager mode (default: 12).")

    g_imp = ap.add_argument_group("mm-import mode")
    g_imp.add_argument("--import-dir",
                       help="Folder Meet Manager imports Dolphin times from (required for --mode mm-import).")
    g_imp.add_argument("--import-poll", type=float, default=2.0,
                       help="Seconds between server polls in mm-import mode (default: 2).")
    g_imp.add_argument("--no-notify", action="store_true",
                       help="Disable the Windows toast on each pulled heat (mm-import).")
    return ap.parse_args(argv)


def run_dolphin_headless(args: argparse.Namespace) -> int:
    missing = [f for f in ("folder", "url") if not getattr(args, f)]
    if missing:
        print(f"--mode dolphin --headless requires: {', '.join('--' + m for m in missing)}", file=sys.stderr)
        return 2

    cfg = WatcherConfig(
        folder=Path(args.folder),
        base_url=args.url,
        token=args.token,
        include_csv=bool(args.include_csv),
        upload_raw=not bool(args.no_raw),
        replay_existing=bool(args.replay_existing),
        tier=args.tier,
    )

    if not cfg.folder.exists():
        print(f"folder does not exist: {cfg.folder}", file=sys.stderr)
        return 1

    watcher = Watcher(cfg, on_event=_safe_print)

    if args.once:
        # Single-pass mode for smoke testing: scan, handle, drain raw queue, exit.
        watcher._stop.set()  # so the raw worker exits after the queue drains
        # Pre-mark NOTHING as seen (replay-existing-style for the one-shot scan).
        for p in watcher._scan():
            if not watcher._is_stable(p):
                # second tick to clear size-stable check
                time.sleep(0.6)
                if not watcher._is_stable(p):
                    print(f"unstable, skipping: {p.name}", flush=True)
                    continue
            watcher._handle(p)
        # Drain raw queue inline.
        while not watcher._raw_q.empty():
            job = watcher._raw_q.get()
            res = watcher.client.send_file(job.path, job.heat)
            print(f"raw {job.path.name}: {res.status} ok={res.ok}", flush=True)
        return 0

    return _run_until_stopped(watcher)


def run_mm_headless(args: argparse.Namespace) -> int:
    from .mm_watcher import MmWatcher, MmWatcherConfig

    missing = [f for f in ("mdb_path", "url") if not getattr(args, f)]
    if missing:
        flags = ", ".join("--" + m.replace("_", "-") for m in missing)
        print(f"--mode manager --headless requires: {flags}", file=sys.stderr)
        return 2

    mdb = Path(args.mdb_path)
    if not mdb.exists():
        print(f"MDB does not exist: {mdb}", file=sys.stderr)
        return 1

    cfg = MmWatcherConfig(
        mdb_path=mdb,
        base_url=args.url,
        token=args.token,
        poll_interval=args.poll_interval,
    )
    watcher = MmWatcher(cfg, on_event=_safe_print)

    if args.once:
        # One read+push cycle, inline, then exit.
        watcher._cycle()
        return 0

    return _run_until_stopped(watcher)


def run_mm_import_headless(args: argparse.Namespace) -> int:
    from .mm_import import MmImportConfig, MmImportWatcher

    missing = [f for f in ("import_dir", "url") if not getattr(args, f)]
    if missing:
        flags = ", ".join("--" + m.replace("_", "-") for m in missing)
        print(f"--mode mm-import --headless requires: {flags}", file=sys.stderr)
        return 2

    cfg = MmImportConfig(
        base_url=args.url,
        import_dir=Path(args.import_dir),
        token=args.token,
        poll_interval=args.import_poll,
        notify=not bool(args.no_notify),
    )
    watcher = MmImportWatcher(cfg, on_event=_safe_print)

    if args.once:
        watcher._cycle()  # one fetch+download pass, then exit (smoke test)
        return 0

    return _run_until_stopped(watcher)


def _run_until_stopped(watcher) -> int:
    """Run a watcher long-running; Ctrl-C / SIGTERM stops it cleanly."""
    def _shutdown(*_):
        watcher.stop()

    signal.signal(signal.SIGINT, _shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown)

    watcher.start()
    try:
        while watcher.is_running():
            time.sleep(0.5)
    except KeyboardInterrupt:
        watcher.stop()
    return 0


def run_headless(args: argparse.Namespace) -> int:
    if args.mode == "manager":
        return run_mm_headless(args)
    if args.mode == "mm-import":
        return run_mm_import_headless(args)
    return run_dolphin_headless(args)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.headless:
        return run_headless(args)
    # GUI mode — import here so headless mode doesn't need tkinter installed.
    from .gui import run_gui
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
