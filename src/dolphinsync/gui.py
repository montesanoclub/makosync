"""Tiny tkinter GUI for the meet-PC volunteer.

What the operator sees: a folder picker, the makosmeets URL + token fields,
a Start/Stop button, a status dot (red/amber/green), counters, and a
scrolling log. Settings persist on Start.

tkinter ships with Windows Python — no extra install needed for the .exe.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, scrolledtext, ttk

from .config import AppConfig
from .watcher import Watcher, WatcherConfig

logger = logging.getLogger(__name__)

MAX_LOG_LINES = 2000  # cap the log widget so a long meet can't grow it unbounded


class GuiApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Makos DolphinSync")
        self.root.geometry("720x520")
        self.root.minsize(640, 420)

        self.cfg = AppConfig.load()
        self.watcher: Watcher | None = None
        self.log_q: "queue.Queue[str]" = queue.Queue()
        self._alive = True

        self._build_ui()
        self._refresh_status()
        self.root.after(200, self._drain_log)
        self.root.after(1000, self._refresh_status)

    # ---- layout -------------------------------------------------------

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        frm = ttk.Frame(self.root)
        frm.pack(fill=tk.BOTH, expand=True, **pad)

        # Folder
        row = 0
        ttk.Label(frm, text="Dolphin folder").grid(row=row, column=0, sticky="w")
        self.folder_var = tk.StringVar(value=self.cfg.folder)
        ttk.Entry(frm, textvariable=self.folder_var).grid(row=row, column=1, sticky="ew", padx=4)
        ttk.Button(frm, text="Browse…", command=self._browse).grid(row=row, column=2)

        # URL
        row += 1
        ttk.Label(frm, text="Ingest URL").grid(row=row, column=0, sticky="w", pady=(8, 0))
        self.url_var = tk.StringVar(value=self.cfg.base_url)
        ttk.Entry(frm, textvariable=self.url_var).grid(row=row, column=1, columnspan=2, sticky="ew", padx=4, pady=(8, 0))

        # Token (optional)
        row += 1
        ttk.Label(frm, text="Token (optional)").grid(row=row, column=0, sticky="w")
        self.token_var = tk.StringVar(value=self.cfg.token)
        ttk.Entry(frm, textvariable=self.token_var).grid(row=row, column=1, columnspan=2, sticky="ew", padx=4)

        # Options
        row += 1
        opt_frm = ttk.Frame(frm)
        opt_frm.grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.csv_var = tk.BooleanVar(value=self.cfg.include_csv)
        self.raw_var = tk.BooleanVar(value=self.cfg.upload_raw)
        self.replay_var = tk.BooleanVar(value=self.cfg.replay_existing)
        ttk.Checkbutton(opt_frm, text="Also watch .csv", variable=self.csv_var).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(opt_frm, text="Upload raw files", variable=self.raw_var).pack(side=tk.LEFT, padx=8)
        ttk.Checkbutton(opt_frm, text="Replay existing on start", variable=self.replay_var).pack(side=tk.LEFT, padx=2)

        # Start/Stop row + status
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

        self.counters_label = ttk.Label(ctrl, text="sent: 0 heat · 0 raw · 0 errors")
        self.counters_label.pack(side=tk.RIGHT)

        # Log
        row += 1
        ttk.Label(frm, text="Log").grid(row=row, column=0, sticky="w", pady=(8, 0))
        row += 1
        self.log_widget = scrolledtext.ScrolledText(frm, height=15, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9))
        self.log_widget.grid(row=row, column=0, columnspan=3, sticky="nsew", padx=4, pady=4)

        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(row, weight=1)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- actions ------------------------------------------------------

    def _browse(self) -> None:
        d = filedialog.askdirectory(initialdir=self.folder_var.get() or "C:/")
        if d:
            self.folder_var.set(d)

    def _toggle(self) -> None:
        if self.watcher and self.watcher.is_running():
            self.watcher.stop()
            self.watcher = None
            self.start_btn.configure(text="Start")
            self._log_line("stopped")
            return

        folder = self.folder_var.get().strip()
        url = self.url_var.get().strip()
        token = self.token_var.get().strip()
        if not folder or not url:
            self._log_line("ERROR: folder and URL are required")
            return

        # Persist before starting.
        self.cfg.folder = folder
        self.cfg.base_url = url
        self.cfg.token = token
        self.cfg.include_csv = bool(self.csv_var.get())
        self.cfg.upload_raw = bool(self.raw_var.get())
        self.cfg.replay_existing = bool(self.replay_var.get())
        self.cfg.save()

        wcfg = WatcherConfig(
            folder=Path(folder),
            base_url=url,
            token=token,
            include_csv=self.cfg.include_csv,
            upload_raw=self.cfg.upload_raw,
            replay_existing=self.cfg.replay_existing,
            tier=self.cfg.tier,
        )
        self.watcher = Watcher(wcfg, on_event=self.log_q.put)
        self.watcher.start()
        self.start_btn.configure(text="Stop")

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
            running = bool(self.watcher and self.watcher.is_running())
            if not running:
                color, label = "#888", "idle"
            else:
                s = self.watcher.stats  # type: ignore[union-attr]
                if s.errors and s.last_error:
                    color, label = "#d28a00", f"watching · last error: {s.last_error[:40]}"
                else:
                    color, label = "#2ca02c", "watching"
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
