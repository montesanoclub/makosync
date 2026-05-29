"""tkinter GUI for the meet-PC volunteer.

Startup shows a **mode picker** — the operator clicks **Dolphin** or **Meet
Manager**. Each mode opens its own little form:

  * Dolphin — Dolphin output folder + options; pushes unofficial times.
  * Meet Manager — the MM ``.mdb`` path + poll interval; pushes official results.

Both share the makosmeets URL + token, a Start/Stop button, a status dot, live
counters, and a scrolling log. Settings persist on Start.

tkinter ships with Windows Python — no extra install needed for the .exe.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from . import __version__
from .config import AppConfig
from .updater import check_for_update
from .watcher import Watcher, WatcherConfig

logger = logging.getLogger(__name__)

MAX_LOG_LINES = 2000  # cap the log widget so a long meet can't grow it unbounded

MODE_LABELS = {"dolphin": "Dolphin", "manager": "Manager"}
MODE_BLURB = {
    "dolphin": "Watch a CTS Dolphin folder · unofficial times",
    "manager": "Hy-Tek Meet Manager database · official results",
}


def _asset(name: str) -> Path | None:
    """Locate a bundled asset in dev and in the PyInstaller onefile bundle."""
    candidates: list[Path] = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates += [Path(base) / "makosync" / "assets" / name, Path(base) / "assets" / name]
    candidates.append(Path(__file__).resolve().parent / "assets" / name)
    for c in candidates:
        if c.exists():
            return c
    return None


class GuiApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"MakoSync v{__version__}")
        self.root.geometry("720x560")
        self.root.minsize(640, 460)

        self.cfg = AppConfig.load()
        self.mode: str | None = None
        self.watcher = None  # Watcher | MmWatcher | None
        self.log_q: "queue.Queue[str]" = queue.Queue()
        self._alive = True
        self._icon_img: tk.PhotoImage | None = None  # keep a ref so Tk doesn't GC it

        self._set_window_icon()
        self.container = ttk.Frame(self.root)
        self.container.pack(fill=tk.BOTH, expand=True)

        self._show_launcher()
        self.root.after(200, self._drain_log)
        self.root.after(1000, self._refresh_status)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- icon ---------------------------------------------------------

    def _set_window_icon(self) -> None:
        png = _asset("mako.png")
        if not png:
            return
        try:
            self._icon_img = tk.PhotoImage(file=str(png))
            self.root.iconphoto(True, self._icon_img)
        except Exception:
            logger.debug("could not set window icon", exc_info=True)

    def _logo_image(self) -> tk.PhotoImage | None:
        """A scaled-down copy of the Mako mark for the launcher header."""
        if not self._icon_img:
            return None
        try:
            w = self._icon_img.width()
            factor = max(1, round(w / 96))  # target ~96px tall
            return self._icon_img.subsample(factor, factor) if factor > 1 else self._icon_img
        except Exception:
            return None

    def _clear_container(self) -> None:
        for child in self.container.winfo_children():
            child.destroy()
        # Drop per-view widget refs so _log_line / _refresh_status know the
        # widgets are gone while we're between views (or on the launcher).
        for attr in ("log_widget", "status_canvas", "counters_label", "status_label", "events_status"):
            if hasattr(self, attr):
                delattr(self, attr)

    # ---- launcher (mode picker) --------------------------------------

    def _show_launcher(self) -> None:
        self.mode = None
        self._clear_container()
        frm = ttk.Frame(self.container, padding=24)
        frm.pack(fill=tk.BOTH, expand=True)

        logo = self._logo_image()
        if logo is not None:
            lbl = ttk.Label(frm, image=logo)
            lbl.image = logo  # type: ignore[attr-defined]  # keep alive
            lbl.pack(pady=(12, 4))
        ttk.Label(frm, text="MakoSync", font=("Segoe UI", 20, "bold")).pack()
        ttk.Label(frm, text="Choose what this computer is feeding to makosmeets:",
                  foreground="#555").pack(pady=(2, 18))

        btns = ttk.Frame(frm)
        btns.pack()
        style = ttk.Style()
        style.configure("Mode.TButton", font=("Segoe UI", 13, "bold"), padding=(24, 16))

        for col, mode in enumerate(("dolphin", "manager")):
            cell = ttk.Frame(btns)
            cell.grid(row=0, column=col, padx=12)
            ttk.Button(cell, text=MODE_LABELS[mode], style="Mode.TButton", width=16,
                       command=lambda m=mode: self._enter_mode(m)).pack()
            ttk.Label(cell, text=MODE_BLURB[mode], foreground="#777",
                      wraplength=190, justify="center").pack(pady=(6, 0))

        ttk.Label(frm, text=f"v{__version__}", foreground="#999").pack(side=tk.BOTTOM, pady=(18, 0))

    # ---- mode view ----------------------------------------------------

    def _enter_mode(self, mode: str) -> None:
        self.mode = mode
        self.cfg.mode = mode
        self._build_mode_view(mode)

    def _build_mode_view(self, mode: str) -> None:
        self._clear_container()
        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(self.container)
        frm.pack(fill=tk.BOTH, expand=True, **pad)

        # Header: back button + mode title.
        header = ttk.Frame(frm)
        header.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(4, 8))
        self.back_btn = ttk.Button(header, text="‹ Mode", width=8, command=self._back_to_launcher)
        self.back_btn.pack(side=tk.LEFT)
        ttk.Label(header, text=MODE_LABELS[mode], font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT, padx=10)

        row = 1
        # Source input — differs per mode.
        if mode == "dolphin":
            ttk.Label(frm, text="Dolphin folder").grid(row=row, column=0, sticky="w")
            self.folder_var = tk.StringVar(value=self.cfg.folder)
            ttk.Entry(frm, textvariable=self.folder_var).grid(row=row, column=1, sticky="ew", padx=4)
            ttk.Button(frm, text="Browse…", command=self._browse_folder).grid(row=row, column=2)
        else:
            ttk.Label(frm, text="Meet Manager .mdb").grid(row=row, column=0, sticky="w")
            self.mdb_var = tk.StringVar(value=self.cfg.mdb_path)
            ttk.Entry(frm, textvariable=self.mdb_var).grid(row=row, column=1, sticky="ew", padx=4)
            ttk.Button(frm, text="Browse…", command=self._browse_mdb).grid(row=row, column=2)

        # URL (shared)
        row += 1
        ttk.Label(frm, text="Ingest URL").grid(row=row, column=0, sticky="w", pady=(8, 0))
        self.url_var = tk.StringVar(value=self.cfg.base_url)
        ttk.Entry(frm, textvariable=self.url_var).grid(row=row, column=1, columnspan=2, sticky="ew", padx=4, pady=(8, 0))

        # Token (shared, optional)
        row += 1
        ttk.Label(frm, text="Token (optional)").grid(row=row, column=0, sticky="w")
        self.token_var = tk.StringVar(value=self.cfg.token)
        ttk.Entry(frm, textvariable=self.token_var).grid(row=row, column=1, columnspan=2, sticky="ew", padx=4)

        # Mode-specific options
        row += 1
        opt_frm = ttk.Frame(frm)
        opt_frm.grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 0))
        if mode == "dolphin":
            self.csv_var = tk.BooleanVar(value=self.cfg.include_csv)
            self.raw_var = tk.BooleanVar(value=self.cfg.upload_raw)
            self.replay_var = tk.BooleanVar(value=self.cfg.replay_existing)
            ttk.Checkbutton(opt_frm, text="Also watch .csv", variable=self.csv_var).pack(side=tk.LEFT, padx=2)
            ttk.Checkbutton(opt_frm, text="Upload raw files", variable=self.raw_var).pack(side=tk.LEFT, padx=8)
            ttk.Checkbutton(opt_frm, text="Replay existing on start", variable=self.replay_var).pack(side=tk.LEFT, padx=2)
        else:
            ttk.Label(opt_frm, text="Poll every").pack(side=tk.LEFT, padx=(2, 4))
            self.poll_var = tk.StringVar(value=f"{self.cfg.poll_interval:g}")
            ttk.Spinbox(opt_frm, from_=3, to=120, increment=1, width=5, textvariable=self.poll_var).pack(side=tk.LEFT)
            ttk.Label(opt_frm, text="seconds  ·  pushes official results (places, DQs)").pack(side=tk.LEFT, padx=4)

        # Dolphin-events relay: Manager pushes the seeded event list; Dolphin loads it.
        row += 1
        relay = ttk.Frame(frm)
        relay.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        if mode == "manager":
            ttk.Button(relay, text="Push events → Dolphin", command=self._push_events).pack(side=tk.LEFT)
        else:
            ttk.Button(relay, text="Load events from MM", command=self._load_events).pack(side=tk.LEFT)
            ttk.Label(relay, text="→ CSV:").pack(side=tk.LEFT, padx=(8, 2))
            self.events_csv_var = tk.StringVar(value=self.cfg.dolphin_events_csv)
            ttk.Entry(relay, textvariable=self.events_csv_var, width=22).pack(side=tk.LEFT)
            ttk.Button(relay, text="…", width=2, command=self._browse_events_csv).pack(side=tk.LEFT, padx=2)
        self.events_status = ttk.Label(relay, text="events on server: —", foreground="#777")
        self.events_status.pack(side=tk.RIGHT)

        # Start/Stop + status
        row += 1
        ctrl = ttk.Frame(frm)
        ctrl.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        self.start_btn = ttk.Button(ctrl, text="Start", command=self._toggle)
        self.start_btn.pack(side=tk.LEFT)
        self.status_canvas = tk.Canvas(ctrl, width=16, height=16, highlightthickness=0)
        self.status_dot = self.status_canvas.create_oval(2, 2, 14, 14, fill="#888")
        self.status_canvas.pack(side=tk.LEFT, padx=8)
        self.status_label = ttk.Label(ctrl, text="idle")
        self.status_label.pack(side=tk.LEFT)

        self.update_btn = ttk.Button(ctrl, text="Check for updates", command=self._check_updates)
        self.update_btn.pack(side=tk.RIGHT)
        self.counters_label = ttk.Label(ctrl, text="")
        self.counters_label.pack(side=tk.RIGHT, padx=8)

        # Log
        row += 1
        ttk.Label(frm, text="Log").grid(row=row, column=0, sticky="w", pady=(8, 0))
        row += 1
        self.log_widget = scrolledtext.ScrolledText(frm, height=14, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9))
        self.log_widget.grid(row=row, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)

        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(row, weight=1)

    def _back_to_launcher(self) -> None:
        if self.watcher and self.watcher.is_running():
            messagebox.showinfo("Running", "Stop the current sync before switching modes.")
            return
        self.watcher = None
        self._show_launcher()

    # ---- actions ------------------------------------------------------

    def _browse_folder(self) -> None:
        d = filedialog.askdirectory(initialdir=self.folder_var.get() or "C:/")
        if d:
            self.folder_var.set(d)

    def _browse_mdb(self) -> None:
        start = self.mdb_var.get()
        f = filedialog.askopenfilename(
            initialdir=str(Path(start).parent) if start else "C:/",
            filetypes=[("Meet Manager database", "*.mdb"), ("Access database", "*.accdb"), ("All files", "*.*")],
        )
        if f:
            self.mdb_var.set(f)

    def _toggle(self) -> None:
        if self.watcher and self.watcher.is_running():
            self.watcher.stop()
            self.watcher = None
            self.start_btn.configure(text="Start")
            self.back_btn.configure(state=tk.NORMAL)
            self._log_line("stopped")
            return

        url = self.url_var.get().strip()
        token = self.token_var.get().strip()
        if not url:
            self._log_line("ERROR: ingest URL is required")
            return

        # Persist shared fields.
        self.cfg.mode = self.mode or "dolphin"
        self.cfg.base_url = url
        self.cfg.token = token

        if self.mode == "manager":
            watcher = self._make_mm_watcher(url, token)
        else:
            watcher = self._make_dolphin_watcher(url, token)
        if watcher is None:
            return

        self.cfg.save()
        self.watcher = watcher
        self.watcher.start()
        self.start_btn.configure(text="Stop")
        self.back_btn.configure(state=tk.DISABLED)

    def _make_dolphin_watcher(self, url: str, token: str):
        folder = self.folder_var.get().strip()
        if not folder:
            self._log_line("ERROR: Dolphin folder is required")
            return None
        self.cfg.folder = folder
        self.cfg.include_csv = bool(self.csv_var.get())
        self.cfg.upload_raw = bool(self.raw_var.get())
        self.cfg.replay_existing = bool(self.replay_var.get())
        wcfg = WatcherConfig(
            folder=Path(folder),
            base_url=url,
            token=token,
            include_csv=self.cfg.include_csv,
            upload_raw=self.cfg.upload_raw,
            replay_existing=self.cfg.replay_existing,
            tier="unofficial",
        )
        return Watcher(wcfg, on_event=self.log_q.put)

    def _make_mm_watcher(self, url: str, token: str):
        from .mm_watcher import MmWatcher, MmWatcherConfig

        mdb = self.mdb_var.get().strip()
        if not mdb:
            self._log_line("ERROR: Meet Manager .mdb path is required")
            return None
        if not Path(mdb).exists():
            self._log_line(f"ERROR: .mdb not found: {mdb}")
            return None
        try:
            poll = max(3.0, float(self.poll_var.get()))
        except ValueError:
            poll = 12.0
        self.cfg.mdb_path = mdb
        self.cfg.poll_interval = poll
        mcfg = MmWatcherConfig(mdb_path=Path(mdb), base_url=url, token=token, poll_interval=poll)
        return MmWatcher(mcfg, on_event=self.log_q.put)

    # ---- dolphin-events relay -----------------------------------------

    def _browse_events_csv(self) -> None:
        f = filedialog.asksaveasfilename(
            initialfile="dolphin_events.csv", defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
        )
        if f:
            self.events_csv_var.set(f)

    def _set_events_status(self, text: str) -> None:
        if self._alive and getattr(self, "events_status", None):
            try:
                self.events_status.configure(text=text)
            except Exception:
                pass

    def _push_events(self) -> None:
        """Manager: read the seeded event list from the .mdb, push it to makosmeets."""
        url = self.url_var.get().strip()
        token = self.token_var.get().strip()
        mdb = self.mdb_var.get().strip()
        if not url:
            self._log_line("ERROR: ingest URL is required")
            return
        if not mdb or not Path(mdb).exists():
            self._log_line(f"ERROR: .mdb not found: {mdb or '(blank)'}")
            return
        self.cfg.mdb_path = mdb
        self._set_events_status("events on server: pushing…")

        def work():
            try:
                from .client import IngestClient
                from .mdb_reader import read_event_list_from_mdb
                events = read_event_list_from_mdb(mdb)
                res, updated_at = IngestClient(url, token).push_dolphin_events(events)
                out = (res, updated_at, len(events), None)
            except Exception as e:
                out = (None, None, 0, e)
            try:
                self.root.after(0, lambda: self._after_push(*out))
            except Exception:
                pass

        threading.Thread(target=work, name="makosync-pushev", daemon=True).start()

    def _after_push(self, res, updated_at, count, err) -> None:
        if not self._alive:
            return
        if err is not None:
            self._log_line(f"push events failed: {err}")
            self._set_events_status("events on server: error")
            return
        if res and res.ok:
            self.cfg.save()
            self._log_line(f"pushed {count} events to server (updated {updated_at or 'ok'})")
            self._set_events_status(f"events on server: {updated_at or 'ok'}")
        else:
            s = res.status if res else "?"
            self._log_line(f"push events rejected: {s} {res.detail if res else ''}")
            self._set_events_status(f"events on server: error {s}")

    def _load_events(self) -> None:
        """Dolphin: fetch the event list from makosmeets, write the Dolphin CSV."""
        url = self.url_var.get().strip()
        token = self.token_var.get().strip()
        csv_path = self.events_csv_var.get().strip()
        if not url:
            self._log_line("ERROR: ingest URL is required")
            return
        if not csv_path:
            self._log_line("ERROR: choose where to save the events CSV (→ CSV)")
            return
        self._set_events_status("events on server: loading…")

        def work():
            try:
                from .client import IngestClient
                from .mdb_reader import write_dolphin_events_csv
                res, events, updated_at = IngestClient(url, token).fetch_dolphin_events()
                if res.ok and events:
                    write_dolphin_events_csv(csv_path, events)
                out = (res, events, updated_at, csv_path, None)
            except Exception as e:
                out = (None, [], None, csv_path, e)
            try:
                self.root.after(0, lambda: self._after_load(*out))
            except Exception:
                pass

        threading.Thread(target=work, name="makosync-loadev", daemon=True).start()

    def _after_load(self, res, events, updated_at, csv_path, err) -> None:
        if not self._alive:
            return
        if err is not None:
            self._log_line(f"load events failed: {err}")
            self._set_events_status("events on server: error")
            return
        if not res or not res.ok:
            s = res.status if res else "?"
            self._log_line(f"load events failed: {s} {res.detail if res else ''}")
            self._set_events_status(f"events on server: error {s}")
            return
        if not events:
            self._log_line("no events on server yet — push from the Manager machine first")
            self._set_events_status("events on server: none yet")
            return
        self.cfg.dolphin_events_csv = csv_path
        self.cfg.save()
        self._log_line(f"loaded {len(events)} events (server updated {updated_at or '?'}); wrote {csv_path}")
        self._set_events_status(f"events on server: {updated_at or 'ok'}")

    # ---- manual update check ------------------------------------------

    def _check_updates(self) -> None:
        """Manual only — runs the GitHub check off the UI thread, shows result."""
        self.update_btn.configure(state=tk.DISABLED, text="Checking…")

        def work():
            try:
                res = (check_for_update(), None)
            except Exception as e:  # network/parse — report, don't crash
                res = (None, e)
            try:
                self.root.after(0, lambda: self._show_update_result(*res))
            except Exception:
                pass  # window closed mid-check

        threading.Thread(target=work, name="makosync-updchk", daemon=True).start()

    def _show_update_result(self, info, err) -> None:
        if not self._alive:
            return
        try:
            self.update_btn.configure(state=tk.NORMAL, text="Check for updates")
        except Exception:
            return  # widget gone (mode switched)
        if err is not None:
            messagebox.showwarning(
                "Update check failed",
                f"Couldn't reach GitHub to check for updates.\n\n{err}",
            )
            return
        if info.available:
            if messagebox.askyesno(
                "Update available",
                f"Version {info.latest} is available.\n"
                f"You have {info.current}.\n\nOpen the download page?",
            ):
                webbrowser.open(info.release_url)
        else:
            messagebox.showinfo(
                "Up to date", f"You have the latest version ({info.current})."
            )

    # ---- polling for log + status -------------------------------------

    def _drain_log(self) -> None:
        try:
            while True:
                msg = self.log_q.get_nowait()
                self._log_line(msg)
        except queue.Empty:
            pass
        except Exception:
            logger.exception("drain_log error")
        finally:
            if self._alive:
                self.root.after(200, self._drain_log)

    def _log_line(self, msg: str) -> None:
        if not getattr(self, "log_widget", None):
            return  # on the launcher screen — no log widget
        self.log_widget.configure(state=tk.NORMAL)
        self.log_widget.insert(tk.END, msg + "\n")
        # Trim from the top so the widget can't grow without bound.
        line_count = int(self.log_widget.index("end-1c").split(".")[0])
        if line_count > MAX_LOG_LINES:
            self.log_widget.delete("1.0", f"{line_count - MAX_LOG_LINES}.0")
        self.log_widget.see(tk.END)
        self.log_widget.configure(state=tk.DISABLED)

    def _refresh_status(self) -> None:
        try:
            if self.mode is None or not getattr(self, "status_canvas", None):
                return  # launcher screen — nothing to refresh
            running = bool(self.watcher and self.watcher.is_running())
            if not running:
                color, label = "#888", "idle"
            else:
                s = self.watcher.stats
                if s.errors and s.last_error:
                    color, label = "#d28a00", f"running · last error: {s.last_error[:40]}"
                else:
                    color, label = "#2ca02c", "running"
                if self.mode == "manager":
                    self.counters_label.configure(text=f"sent: {s.sent_heat} official · {s.errors} errors")
                else:
                    self.counters_label.configure(text=f"sent: {s.sent_heat} heat · {s.sent_file} raw · {s.errors} errors")
            self.status_canvas.itemconfigure(self.status_dot, fill=color)
            self.status_label.configure(text=label)
        except Exception:
            logger.exception("refresh_status error")
        finally:
            if self._alive:
                self.root.after(1000, self._refresh_status)

    def _on_close(self) -> None:
        self._alive = False
        if self.watcher:
            self.watcher.stop()
        self.root.destroy()


def run_gui() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = tk.Tk()
    GuiApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(run_gui())
