"""Manual 'check for updates' against GitHub Releases (stdlib only).

No background polling and no auto-install — the GUI calls check_for_update()
only when the operator clicks the button. It compares the latest published
release tag to this build's __version__ and reports whether a newer one exists.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
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


def _version_tuple(tag: str) -> tuple[int, ...]:
    """'v0.1.10' -> (0, 1, 10). Numeric compare, so 0.1.10 > 0.1.9."""
    nums = re.findall(r"\d+", tag or "")
    return tuple(int(n) for n in nums) if nums else (0,)


def check_for_update(timeout: float = 8.0) -> UpdateInfo:
    """Query GitHub for the latest release tag and compare to __version__.

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
    logger.info("update check: current=%s latest=%s available=%s",
                __version__, latest_tag, available)
    return UpdateInfo(
        current=__version__,
        latest=latest_tag.lstrip("v"),
        available=available,
        release_url=release_url,
    )
