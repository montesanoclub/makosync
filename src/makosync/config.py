"""Config persistence — load/save to %APPDATA%\\MakoSync\\config.json."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

APP_DIR_NAME = "MakoSync"
CONFIG_FILE = "config.json"

# Prebaked so a fresh meet-PC install is ready to go without the volunteer typing
# anything — point at the makosmeets ingest endpoint with the shared dolphin
# token. Both are overridable in the GUI (and autosaved) if a meet needs different.
DEFAULT_BASE_URL = "https://www.makosmeets.com/"
DEFAULT_TOKEN = "dev-dolphin-token"


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    p = Path(base) / APP_DIR_NAME
    # Don't let a locked/redirected APPDATA (roaming profiles, group policy)
    # crash the app at startup — load() falls back to defaults, save() logs.
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("could not create app dir %s", p)
    return p


def config_path() -> Path:
    return app_dir() / CONFIG_FILE


@dataclass
class AppConfig:
    # Which producer mode the launcher last ran. "dolphin" watches a CTS Dolphin
    # folder (unofficial times); "manager" runs on the Meet Manager PC and does
    # both jobs at once — pulls Dolphin .do3 into MM's import folder *and* reads
    # the .mdb to push official results. URL + token below are shared across modes.
    # (The old standalone "mm-import" mode folded into "manager"; load() migrates it.)
    mode: str = "dolphin"

    base_url: str = DEFAULT_BASE_URL
    token: str = DEFAULT_TOKEN

    # --- Dolphin mode ---
    folder: str = ""
    include_csv: bool = False
    upload_raw: bool = True  # also archive the raw .do to R2 (forensic copy)
    replay_existing: bool = False
    tier: str = "unofficial"

    # --- Meet Manager mode ---
    mdb_path: str = ""        # path to the live Hy-Tek MM .mdb on the scoring PC
    poll_interval: float = 12.0  # seconds between MDB re-reads

    # --- Meet Manager import-pull (part of "manager" mode) ---
    # The scoring PC pulls the Dolphin .do3 files (relayed via makosmeets),
    # renamed <meetid>-000-E<ev>_H<ht>.do3, into the folder Meet Manager imports
    # Dolphin times from — so the operator just Get-Times instead of hunting for
    # the right file. Runs alongside the .mdb official-results push above, on its
    # own (faster) cadence. import_dir blank => the .mdb's parent folder.
    import_dir: str = ""          # where to drop renamed .do3 for Meet Manager
    import_poll: float = 2.0      # seconds between server polls
    import_notify: bool = True    # Windows toast when a heat lands

    # --- Dolphin-events relay ---
    # Dolphin side: where to write the events CSV pulled from makosmeets.
    # (Manager side builds it from the .mdb above — no source file to configure.)
    dolphin_events_csv: str = ""

    @classmethod
    def load(cls) -> "AppConfig":
        p = config_path()
        if not p.exists():
            return cls()
        try:
            data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
            cfg = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            # Backfill the prebaked endpoint if an older config saved them blank,
            # so the makosmeets URL/token are always pre-filled unless overridden.
            if not cfg.base_url:
                cfg.base_url = DEFAULT_BASE_URL
            if not cfg.token:
                cfg.token = DEFAULT_TOKEN
            if cfg.mode == "mm-import":  # standalone MM Import merged into Manager
                logger.info("migrating saved mode 'mm-import' -> 'manager'")
                cfg.mode = "manager"
            return cfg
        except Exception:
            logger.exception("could not load config from %s", p)
            return cls()

    def save(self) -> None:
        p = config_path()
        try:
            p.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except Exception:
            logger.exception("could not save config to %s", p)
