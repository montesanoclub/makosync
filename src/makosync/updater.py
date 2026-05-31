"""Update check + installer download against GitHub Releases (stdlib only).

``check_for_update()`` compares the latest published release to this build's
``__version__`` and, when newer, also surfaces the installer asset's download URL
so the app can self-update. ``download()`` streams that asset to disk. No
background polling — the GUI runs the check once at startup (never mid-meet).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib import request

from . import __version__

logger = logging.getLogger(__name__)

# GitHub repo the update check queries (renamed from mm-dolphinsync 2026-05-29).
GITHUB_REPO = "montesanoclub/makosync"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
USER_AGENT = f"MakoSync/{__version__}"


@dataclass
class UpdateInfo:
    current: str
    latest: str
    available: bool
    release_url: str = RELEASES_PAGE
    asset_url: str | None = None   # installer (Setup .exe) download URL, if any
    asset_name: str = ""


def _version_tuple(tag: str) -> tuple[int, ...]:
    """'v0.1.10' -> (0, 1, 10). Numeric compare, so 0.1.10 > 0.1.9."""
    nums = re.findall(r"\d+", tag or "")
    return tuple(int(n) for n in nums) if nums else (0,)


def _installer_asset(data: dict) -> tuple[str | None, str]:
    """Pick the Setup .exe asset's download URL + name from a release payload."""
    assets = data.get("assets") if isinstance(data, dict) else None
    if not isinstance(assets, list):
        return None, ""
    for a in assets:
        if not isinstance(a, dict):
            continue
        name = a.get("name") or ""
        url = a.get("browser_download_url") or ""
        if name.lower().endswith(".exe") and "setup" in name.lower() and url:
            return url, name
    return None, ""


def check_for_update(timeout: float = 8.0) -> UpdateInfo:
    """Query GitHub for the latest release and compare to __version__.

    Raises on network/parse error so the caller can say "couldn't check"
    rather than falsely reporting up-to-date.
    """
    req = request.Request(
        LATEST_RELEASE_API,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    with request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    latest_tag = data.get("tag_name", "") or ""
    release_url = data.get("html_url") or RELEASES_PAGE
    available = _version_tuple(latest_tag) > _version_tuple(__version__)
    asset_url, asset_name = _installer_asset(data)
    logger.info("update check: current=%s latest=%s available=%s asset=%s",
                __version__, latest_tag, available, asset_name)
    return UpdateInfo(
        current=__version__,
        latest=latest_tag.lstrip("v"),
        available=available,
        release_url=release_url,
        asset_url=asset_url,
        asset_name=asset_name,
    )


def download(url: str, dest: str | Path, progress: Callable[[int, int], None] | None = None,
             timeout: float = 60.0) -> Path:
    """Stream a release asset to ``dest``. ``progress(done, total)`` called as it goes.

    Downloads via urllib (raw sockets), which — unlike a browser — does not tag the
    file with Mark-of-the-Web, so a per-user silent install of it generally runs
    without a SmartScreen prompt.
    """
    dest = Path(dest)
    if dest.parent and not dest.parent.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    with request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    return dest
