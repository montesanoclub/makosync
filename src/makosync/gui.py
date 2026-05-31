"""tkinter GUI for the meet-PC volunteer.

Startup shows a **mode picker** — the operator clicks **Dolphin** or **Manager**.
Each mode opens its own little form:

  * Dolphin — Dolphin output folder + options; pushes unofficial times.
  * Manager — runs on the Meet Manager PC and does both jobs at once: pulls the
    relayed Dolphin ``.do3`` into MM's import folder (fast) *and* reads the MM
    ``.mdb`` to push official results (gentler). Each has its own poll interval.

Both share the makosmeets URL + token, a Start/Stop button, a status dot, live
counters, and a scrolling log. Settings persist on Start.

tkinter ships with Windows Python — no extra install needed for the .exe.
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import tempfile
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
    "manager": "Meet Manager PC · pull Dolphin times in + push official results",
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
        self._persist_after_id = None  # debounced settings-autosave handle
        self._update_info = None  # latest UpdateInfo, once the startup check finds one

        self._set_window_icon()
        self.container = ttk.Frame(self.root)
        self.container.pack(fill=tk.BOTH, expand=True)

        self._show_launcher()
        self.root.after(200, self._drain_log)
        self.root.after(1000, self._refresh_status)
        # Quiet GitHub-release check shortly after launch; prompts only if a newer
        # version exists, silent if up-to-date or offline. (Manual button too.)
        self.root.after(1500, self._startup_update_check)
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
        widget_attrs = ("log_widget", "status_canvas", "counters_label", "status_label", "events_status")
        # Also drop the mode's data-bound vars. Dropping the last Python ref lets
        # Tk unset the Tcl variable and clear its trace — otherwise a stale var
        # from the previous mode keeps a live autosave trace and could overwrite
        # config with old values (and traces would pile up each mode switch).
        var_attrs = ("url_var", "token_var", "folder_var", "mdb_var", "import_dir_var",
                     "csv_var", "raw_var", "replay_var", "poll_var", "import_poll_var",
                     "import_notify_var", "events_csv_var")
        for attr in widget_attrs + var_attrs:
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
            ttk.Button(cell, text=MODE_LABELS[mode], style="Mode.TButton", width=14,
                       command=lambda m=mode: self._enter_mode(m)).pack()
            ttk.Label(cell, text=MODE_BLURB[mode], foreground="#777",
                      wraplength=170, justify="center").pack(pady=(6, 0))

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
        else:  # manager — reads the .mdb (official results) AND drops pulled .do3
            ttk.Label(frm, text="Meet Manager .mdb").grid(row=row, column=0, sticky="w")
            self.mdb_var = tk.StringVar(value=self.cfg.mdb_path)
            ttk.Entry(frm, textvariable=self.mdb_var).grid(row=row, column=1, sticky="ew", padx=4)
            ttk.Button(frm, text="Browse…", command=self._browse_mdb).grid(row=row, column=2)
            row += 1
            ttk.Label(frm, text="Import folder").grid(row=row, column=0, sticky="w")
            # Default to the folder the .mdb lives in — that's the folder MM's
            # Get-Times picker already points at, so the operator doesn't pick a
            # second path. (Blank is fine; on Start we fall back to the .mdb's parent.)
            default_dir = self.cfg.import_dir
            if not default_dir and self.cfg.mdb_path:
                default_dir = str(Path(self.cfg.mdb_path).parent)
            self.import_dir_var = tk.StringVar(value=default_dir)
            ttk.Entry(frm, textvariable=self.import_dir_var).grid(row=row, column=1, sticky="ew", padx=4)
            ttk.Button(frm, text="Browse…", command=self._browse_import_dir).grid(row=row, column=2)

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
        else:  # manager — two independent cadences (fast pull, gentler .mdb read) + toast
            ttk.Label(opt_frm, text="Pull .do3 every").pack(side=tk.LEFT, padx=(2, 4))
            self.import_poll_var = tk.StringVar(value=f"{self.cfg.import_poll:g}")
            ttk.Spinbox(opt_frm, from_=1, to=30, increment=1, width=4, textvariable=self.import_poll_var).pack(side=tk.LEFT)
            ttk.Label(opt_frm, text="s").pack(side=tk.LEFT, padx=(2, 12))
            ttk.Label(opt_frm, text="Read .mdb every").pack(side=tk.LEFT, padx=(0, 4))
            self.poll_var = tk.StringVar(value=f"{self.cfg.poll_interval:g}")
            ttk.Spinbox(opt_frm, from_=3, to=120, increment=1, width=4, textvariable=self.poll_var).pack(side=tk.LEFT)
            ttk.Label(opt_frm, text="s").pack(side=tk.LEFT, padx=(2, 12))
            self.import_notify_var = tk.BooleanVar(value=self.cfg.import_notify)
            ttk.Checkbutton(opt_frm, text="Toast on new heat", variable=self.import_notify_var).pack(side=tk.LEFT)

        # Dolphin-events relay: Manager pushes the seeded event list (built from
        # the .mdb); Dolphin loads it and writes the CSV. Both modes show the row.
        if mode in ("dolphin", "manager"):
            row += 1
            relay = ttk.Frame(frm)
            relay.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(6, 0))
            if mode == "manager":
                ttk.Button(relay, text="Push events → Dolphin", command=self._push_events).pack(side=tk.LEFT)
                ttk.Label(relay, text="(built from the .mdb above)", foreground="#999").pack(side=tk.LEFT, padx=6)
            else:  # dolphin
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

        # Autosave fields on edit (debounced) so a close/kill never loses
        # URL/token/paths — config is re-read into the fields on next launch.
        for _name in ("url_var", "token_var", "folder_var", "mdb_var", "poll_var",
                      "events_csv_var", "csv_var", "raw_var", "replay_var",
                      "import_dir_var", "import_poll_var", "import_notify_var"):
            _v = getattr(self, _name, None)
            if _v is not None:
                _v.trace_add("write", self._schedule_persist)

    def _back_to_launcher(self) -> None:
        if self.watcher and self.watcher.is_running():
            messagebox.showinfo("Running", "Stop the current sync before switching modes.")
            return
        self._persist()  # keep this view's fields before leaving it
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
            # First time choosing the .mdb: default the import drop folder to its
            # parent (MM's Get-Times folder) if the operator hasn't set one.
            idv = getattr(self, "import_dir_var", None)
            if idv is not None and not idv.get().strip():
                idv.set(str(Path(f).parent))

    def _browse_import_dir(self) -> None:
        d = filedialog.askdirectory(initialdir=self.import_dir_var.get() or "C:/")
        if d:
            self.import_dir_var.set(d)

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
            watcher = self._make_manager_watcher(url, token)
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

    def _make_manager_watcher(self, url: str, token: str):
        """Manager: pull Dolphin .do3 into MM's folder *and* push official .mdb results."""
        from .manager_watcher import ManagerWatcher, ManagerWatcherConfig

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
        try:
            ipoll = max(1.0, float(self.import_poll_var.get()))
        except ValueError:
            ipoll = 2.0
        # Blank import folder → drop pulled .do3 next to the .mdb (MM's Get-Times folder).
        import_dir = self.import_dir_var.get().strip() or str(Path(mdb).parent)
        self.cfg.mdb_path = mdb
        self.cfg.poll_interval = poll
        self.cfg.import_dir = import_dir
        self.cfg.import_poll = ipoll
        self.cfg.import_notify = bool(self.import_notify_var.get())
        mcfg = ManagerWatcherConfig(
            mdb_path=Path(mdb), base_url=url, token=token,
            poll_interval=poll, import_dir=Path(import_dir), import_poll=ipoll,
            notify=self.cfg.import_notify,
        )
        return ManagerWatcher(mcfg, on_event=self.log_q.put)

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
        """Manager: build the Dolphin events CSV from the .mdb, push it to makosmeets."""
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
                from .mdb_reader import read_dolphin_events_csv_from_mdb
                csv_text = read_dolphin_events_csv_from_mdb(mdb)
                lines = sum(1 for ln in csv_text.splitlines() if ln.strip())
                res, updated_at = IngestClient(url, token).push_dolphin_events_csv(csv_text, name=Path(mdb).name)
                out = (res, updated_at, lines, None)
            except Exception as e:
                out = (None, None, 0, e)
            try:
                self.root.after(0, lambda: self._after_push(*out))
            except Exception:
                pass

        threading.Thread(target=work, name="makosync-pushev", daemon=True).start()

    def _after_push(self, res, updated_at, lines, err) -> None:
        if not self._alive:
            return
        if err is not None:
            self._log_line(f"push events failed: {err}")
            self._set_events_status("events on server: error")
            return
        if res and res.ok:
            self.cfg.save()
            self._log_line(f"pushed {lines} events to server (updated {updated_at or 'ok'})")
            self._set_events_status(f"events on server: {updated_at or 'ok'}")
        else:
            s = res.status if res else "?"
            self._log_line(f"push events rejected: {s} {res.detail if res else ''}")
            self._set_events_status(f"events on server: error {s}")

    def _load_events(self) -> None:
        """Dolphin: fetch the events CSV from makosmeets, write it verbatim for Dolphin."""
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
                res, csv_text, name, updated_at = IngestClient(url, token).fetch_dolphin_events_csv()
                lines = 0
                if res.ok and csv_text:
                    p = Path(csv_path)
                    if p.parent and not p.parent.exists():
                        p.parent.mkdir(parents=True, exist_ok=True)
                    with open(p, "w", newline="", encoding="utf-8") as f:
                        f.write(csv_text)  # verbatim — keep events2dolphin's exact bytes/CRLF
                    lines = sum(1 for ln in csv_text.splitlines() if ln.strip())
                out = (res, lines, updated_at, csv_path, None)
            except Exception as e:
                out = (None, 0, None, csv_path, e)
            try:
                self.root.after(0, lambda: self._after_load(*out))
            except Exception:
                pass

        threading.Thread(target=work, name="makosync-loadev", daemon=True).start()

    def _after_load(self, res, lines, updated_at, csv_path, err) -> None:
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
        if not lines:
            self._log_line("no events on server yet — push from the Manager machine first")
            self._set_events_status("events on server: none yet")
            return
        self.cfg.dolphin_events_csv = csv_path
        self.cfg.save()
        self._log_line(f"loaded {lines} events (server updated {updated_at or '?'}); wrote {csv_path}")
        self._set_events_status(f"events on server: {updated_at or 'ok'}")

    # ---- update checks (startup + manual) -----------------------------

    def _startup_update_check(self) -> None:
        """On launch: check GitHub releases off the UI thread; prompt only if a
        newer version exists. Stays silent when up-to-date or offline."""
        def work():
            try:
                info = check_for_update()
            except Exception:  # noqa: BLE001 — offline/API hiccup: silent on startup
                info = None
            if info is not None:
                try:
                    self.root.after(0, lambda: self._maybe_prompt_update(info))
                except Exception:
                    pass  # window closed mid-check
        threading.Thread(target=work, name="makosync-startupchk", daemon=True).start()

    def _maybe_prompt_update(self, info) -> None:
        if not self._alive or not info.available:
            return
        self._update_info = info
        self._do_self_update(info)  # confirms, then downloads + installs + restarts

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
            self._update_info = info
            self._do_self_update(info)  # confirms, then downloads + installs + restarts
        else:
            messagebox.showinfo(
                "Up to date", f"You have the latest version ({info.current})."
            )

    # ---- self-update (download installer, install, restart) -----------

    def _do_self_update(self, info=None) -> None:
        """Download the installer and launch it to update + restart (idle only)."""
        info = info or self._update_info
        if not info or not info.available:
            return
        if self.watcher and self.watcher.is_running():
            messagebox.showinfo("Sync running", "Stop the current sync before updating.")
            return
        if not info.asset_url:  # no installer asset — fall back to the download page
            if messagebox.askyesno("Update available",
                    f"Version {info.latest} is available.\nOpen the download page?"):
                webbrowser.open(info.release_url)
            return
        if not messagebox.askyesno("Update MakoSync",
                f"Download and install v{info.latest}?\nMakoSync will close and restart."):
            return
        self._persist()  # keep settings before the restart
        self._set_update_busy("Downloading…")

        def work():
            try:
                from . import updater
                dest = Path(tempfile.gettempdir()) / (info.asset_name or "MakoSync-Setup.exe")
                updater.download(info.asset_url, dest, progress=self._download_progress)
                out = (str(dest), None)
            except Exception as e:  # noqa: BLE001 — surfaced to the user below
                out = (None, e)
            try:
                self.root.after(0, lambda: self._after_download(*out))
            except Exception:
                pass

        threading.Thread(target=work, name="makosync-update", daemon=True).start()

    def _set_update_busy(self, text: str) -> None:
        btn = getattr(self, "update_btn", None)
        if btn:
            try:
                btn.configure(state=tk.DISABLED, text=text)
            except Exception:
                pass

    def _restore_update_btn(self) -> None:
        btn = getattr(self, "update_btn", None)
        if btn:
            try:
                btn.configure(state=tk.NORMAL, text="Check for updates")
            except Exception:
                pass

    def _download_progress(self, done: int, total: int) -> None:
        pct = f"{done * 100 // total}%" if total else f"{done // 1024}KB"
        try:
            self.root.after(0, lambda: self._set_update_busy(f"Downloading… {pct}"))
        except Exception:
            pass

    def _after_download(self, path, err) -> None:
        if not self._alive:
            return
        if err is not None or not path:
            self._log_line(f"update download failed: {err}")
            self._restore_update_btn()
            messagebox.showwarning("Update failed", f"Couldn't download the installer.\n\n{err}")
            return
        self._log_line("downloaded installer; launching to install + restart…")
        try:
            flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
            subprocess.Popen([path, "/VERYSILENT", "/SUPPRESSMSGBOXES"],
                             creationflags=flags, close_fds=True)
        except Exception as e:  # noqa: BLE001 — surfaced to the user below
            self._log_line(f"could not launch installer: {e}")
            self._restore_update_btn()
            messagebox.showwarning("Update failed", f"Couldn't launch the installer.\n\n{e}")
            return
        # Exit so the installer can replace this exe; CloseApplications + the
        # silent-relaunch [Run] entry bring MakoSync back up on the new version.
        self.root.after(700, self._on_close)

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
                    self.counters_label.configure(text=f"pulled: {s.sent_file} · official: {s.sent_heat} · {s.errors} errors")
                else:
                    self.counters_label.configure(text=f"sent: {s.sent_heat} heat · {s.sent_file} raw · {s.errors} errors")
            self.status_canvas.itemconfigure(self.status_dot, fill=color)
            self.status_label.configure(text=label)
        except Exception:
            logger.exception("refresh_status error")
        finally:
            if self._alive:
                self.root.after(1000, self._refresh_status)

    # ---- settings autosave -------------------------------------------

    def _schedule_persist(self, *_) -> None:
        """Debounce field autosave: write config ~1s after the last edit."""
        if self._persist_after_id:
            try:
                self.root.after_cancel(self._persist_after_id)
            except Exception:
                pass
        try:
            self._persist_after_id = self.root.after(1000, self._persist)
        except Exception:
            self._persist_after_id = None

    def _persist(self) -> None:
        """Capture the currently-shown fields into config and save to disk."""
        self._persist_after_id = None
        try:
            if self.mode:
                self.cfg.mode = self.mode
            if hasattr(self, "url_var"):
                self.cfg.base_url = self.url_var.get().strip()
            if hasattr(self, "token_var"):
                self.cfg.token = self.token_var.get().strip()
            if hasattr(self, "folder_var"):
                self.cfg.folder = self.folder_var.get().strip()
            if hasattr(self, "mdb_var"):
                self.cfg.mdb_path = self.mdb_var.get().strip()
            if hasattr(self, "csv_var"):
                self.cfg.include_csv = bool(self.csv_var.get())
            if hasattr(self, "raw_var"):
                self.cfg.upload_raw = bool(self.raw_var.get())
            if hasattr(self, "replay_var"):
                self.cfg.replay_existing = bool(self.replay_var.get())
            if hasattr(self, "events_csv_var"):
                self.cfg.dolphin_events_csv = self.events_csv_var.get().strip()
            if hasattr(self, "poll_var"):
                try:
                    self.cfg.poll_interval = max(3.0, float(self.poll_var.get()))
                except (ValueError, tk.TclError):
                    pass
            if hasattr(self, "import_dir_var"):
                self.cfg.import_dir = self.import_dir_var.get().strip()
            if hasattr(self, "import_poll_var"):
                try:
                    self.cfg.import_poll = max(1.0, float(self.import_poll_var.get()))
                except (ValueError, tk.TclError):
                    pass
            if hasattr(self, "import_notify_var"):
                self.cfg.import_notify = bool(self.import_notify_var.get())
            self.cfg.save()
        except Exception:
            logger.exception("settings autosave failed")

    def _on_close(self) -> None:
        self._alive = False
        self._persist()  # save whatever's in the fields on a clean close
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
