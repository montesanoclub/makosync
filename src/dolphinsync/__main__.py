"""Entry point for `dolphinsync` — GUI by default, --headless for CLI."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from .watcher import Watcher, WatcherConfig


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="dolphinsync",
        description="Watch a CTS Dolphin folder, push parsed times + raw files to an ingest endpoint.",
    )
    ap.add_argument("--headless", action="store_true", help="No GUI — run from the CLI.")
    ap.add_argument("--folder", help="Dolphin output folder (required for --headless).")
    ap.add_argument("--url", help="Base URL of the ingest server, e.g. http://localhost:8080")
    ap.add_argument("--token", default="", help="Optional bearer token; omitted if blank.")
    ap.add_argument("--include-csv", action="store_true", help="Also watch .csv files.")
    ap.add_argument("--no-raw", action="store_true",
                    help="Don't archive raw files (JSON heat only).")
    ap.add_argument("--replay-existing", action="store_true",
                    help="Also send files that already exist when the watcher starts.")
    ap.add_argument("--tier", default="unofficial", choices=["unofficial", "official"],
                    help="Tier label sent with each heat (default: unofficial).")
    ap.add_argument("--once", action="store_true",
                    help="Scan one pass and exit (handy for smoke tests).")
    return ap.parse_args(argv)


def run_headless(args: argparse.Namespace) -> int:
    missing = [f for f in ("folder", "url") if not getattr(args, f)]
    if missing:
        print(f"--headless requires: {', '.join('--' + m for m in missing)}", file=sys.stderr)
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

    watcher = Watcher(cfg, on_event=lambda m: print(m, flush=True))

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

    # Long-running mode. Ctrl-C / SIGTERM stops cleanly.
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
