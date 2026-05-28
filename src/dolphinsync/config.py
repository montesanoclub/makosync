"""Config persistence — load/save to %APPDATA%\\MakosDolphinSync\\config.json."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

APP_DIR_NAME = "MakosDolphinSync"
CONFIG_FILE = "config.json"


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
    folder: str = ""
    base_url: str = ""
    token: str = ""
    include_csv: bool = False
    upload_raw: bool = True  # also archive the raw .do to R2 (forensic copy)
    replay_existing: bool = False
    tier: str = "unofficial"

    @classmethod
    def load(cls) -> "AppConfig":
        p = config_path()
        if not p.exists():
            return cls()
        try:
            data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception:
            logger.exception("could not load config from %s", p)
            return cls()

    def save(self) -> None:
        p = config_path()
        try:
            p.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except Exception:
            logger.exception("could not save config to %s", p)
