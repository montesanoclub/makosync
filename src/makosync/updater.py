"""Update check + installer download + self-update against GitHub Releases (stdlib only).

``check_for_update()`` compares the latest published release to this build's
``__version__`` and, when newer, surfaces the installer asset (URL + size) so the
app can self-update. ``download()`` streams that asset to disk and **verifies it
arrived whole** — a truncated installer must never run. ``launch_update()`` hands
off to a small detached helper that **waits for MakoSync to fully exit** (so its
locked ``.exe`` can be replaced), runs the installer silently, then relaunches.

That wait-then-install hand-off is the crux: the running ``.exe`` is locked by
Windows, so the installer can only replace it after we're gone. The helper is
launched detached + broken away from our job so our exit can't kill it mid-write
(the bug that corrupted an install in 0.3.1). And because it waits *before*
touching anything, a failure to break away fails *safe* — the update just doesn't
happen, nothing is corrupted.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
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

# Windows CreateProcess flags for the helper. CREATE_NO_WINDOW (not
# DETACHED_PROCESS) — the helper is a batch script that runs console tools
# (tasklist/find/ping), so it needs a console; we just hide it. BREAKAWAY_FROM_JOB
# so closing the app can't kill the helper mid-install.
_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


@dataclass
class UpdateInfo:
    current: str
    latest: str
    available: bool
    release_url: str = RELEASES_PAGE
    asset_url: str | None = None   # installer (Setup .exe) download URL, if any
    asset_name: str = ""
    asset_size: int = 0            # bytes, from the GitHub asset (download integrity check)


def _version_tuple(tag: str) -> tuple[int, ...]:
    """'v0.1.10' -> (0, 1, 10). Numeric compare, so 0.1.10 > 0.1.9."""
    nums = re.findall(r"\d+", tag or "")
    return tuple(int(n) for n in nums) if nums else (0,)


def _installer_asset(data: dict) -> tuple[str | None, str, int]:
    """Pick the Setup .exe asset's download URL + name + size from a release payload."""
    assets = data.get("assets") if isinstance(data, dict) else None
    if not isinstance(assets, list):
        return None, "", 0
    for a in assets:
        if not isinstance(a, dict):
            continue
        name = a.get("name") or ""
        url = a.get("browser_download_url") or ""
        if name.lower().endswith(".exe") and "setup" in name.lower() and url:
            size = a.get("size")
            return url, name, (int(size) if isinstance(size, int) else 0)
    return None, "", 0


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
    asset_url, asset_name, asset_size = _installer_asset(data)
    logger.info("update check: current=%s latest=%s available=%s asset=%s (%s bytes)",
                __version__, latest_tag, available, asset_name, asset_size)
    return UpdateInfo(
        current=__version__,
        latest=latest_tag.lstrip("v"),
        available=available,
        release_url=release_url,
        asset_url=asset_url,
        asset_name=asset_name,
        asset_size=asset_size,
    )


def download(url: str, dest: str | Path, progress: Callable[[int, int], None] | None = None,
             timeout: float = 60.0, expected_size: int = 0) -> Path:
    """Stream a release asset to ``dest``; verify it arrived whole, else raise.

    ``progress(done, total)`` is called as bytes land. After the stream, the
    bytes written must equal the server's Content-Length (and ``expected_size``
    if given) — otherwise the partial file is deleted and IOError is raised, so a
    truncated installer is never handed to the self-updater.

    Downloads via urllib (raw sockets), which — unlike a browser — does not tag the
    file with Mark-of-the-Web, so a per-user silent install generally runs without
    a SmartScreen prompt.
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
            f.flush()
            os.fsync(f.fileno())  # bytes hit disk before we verify + run the installer
    # Require a known size and an exact match, else delete + raise — an installer we
    # can't verify whole must NEVER reach the self-updater (this is what corrupts installs).
    expected = expected_size or total
    if not expected or done != expected:
        try:
            dest.unlink()
        except OSError:
            pass
        if not expected:
            raise IOError(f"refusing unverifiable download (no Content-Length/expected_size) from {url}")
        raise IOError(f"incomplete download: got {done} of {expected} bytes from {url}")
    return dest


def _build_update_script(installer: str, target_exe: str, pid: int, image_name: str) -> str:
    """PowerShell helper: wait for our PID to exit, install, relaunch, self-clean.

    PowerShell (not a .bat) because the helper runs in a hidden-console detached
    process where ``tasklist`` misbehaves — ``Get-Process``/``Start-Process`` are
    .NET and work headless. Matches PID *and* process name so a recycled PID can't
    fool the wait. ``-Wait`` blocks until the installer finishes before relaunch.
    """
    proc_name = image_name[:-4] if image_name.lower().endswith(".exe") else image_name

    def q(s: str) -> str:  # single-quote a PowerShell literal (double any quotes)
        return s.replace("'", "''")

    return (
        "# MakoSync self-update: wait for the running app to exit, install, relaunch.\r\n"
        "$ErrorActionPreference = 'SilentlyContinue'\r\n"
        f"$targetPid = {pid}\r\n"
        f"$procName = '{q(proc_name)}'\r\n"
        f"$installer = '{q(installer)}'\r\n"
        f"$target = '{q(target_exe)}'\r\n"
        "# Wait (cap 5 min) for the running app to release its locked exe - never\r\n"
        "# touch files while it is alive (that is what corrupted installs in 0.3.1).\r\n"
        "$deadline = (Get-Date).AddMinutes(5)\r\n"
        "while ((Get-Date) -lt $deadline) {\r\n"
        "    $p = Get-Process -Id $targetPid -ErrorAction SilentlyContinue\r\n"
        "    if (-not $p -or $p.ProcessName -ne $procName) { break }\r\n"
        "    Start-Sleep -Milliseconds 500\r\n"
        "}\r\n"
        "if (Test-Path -LiteralPath $installer) {\r\n"
        "    Start-Process -FilePath $installer -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -Wait\r\n"
        "    Start-Sleep -Seconds 2\r\n"   # let AV finish scanning the freshly-written files
        "}\r\n"
        "# Relaunch whatever exe is present now - the new one on success, or the old\r\n"
        "# one Inno restores on a rolled-back failure. Never leave the operator with nothing.\r\n"
        "if (Test-Path -LiteralPath $target) { Start-Process -FilePath $target }\r\n"
        "Remove-Item -LiteralPath $installer -ErrorAction SilentlyContinue\r\n"
        "Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -ErrorAction SilentlyContinue\r\n"
    )


def launch_update(installer: str | Path, target_exe: str | Path, pid: int | None = None,
                  image_name: str | None = None, script_dir: str | Path | None = None) -> Path:
    """Write + launch the detached PowerShell update helper; returns its path.

    The caller should close the app right after this so the helper's wait loop
    completes and the installer can replace the (now-unlocked) exe.
    """
    pid = os.getpid() if pid is None else pid
    target_exe = str(target_exe)
    image_name = image_name or os.path.basename(target_exe)
    if not image_name.strip():
        raise ValueError("cannot launch updater: empty target exe name")
    script_dir = Path(script_dir or tempfile.gettempdir())
    script_dir.mkdir(parents=True, exist_ok=True)
    script = script_dir / "makosync_update.ps1"
    text = _build_update_script(str(installer), target_exe, pid, image_name)
    # UTF-8 BOM so Windows PowerShell 5.1 reads non-ASCII paths correctly.
    script.write_text(text, encoding="utf-8-sig")

    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-WindowStyle", "Hidden", "-File", str(script)]
    base = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
    try:
        # Breakaway from our job so closing the app can't kill the helper mid-install.
        subprocess.Popen(cmd, creationflags=base | _CREATE_BREAKAWAY_FROM_JOB,
                          close_fds=True, cwd=str(script_dir))
    except OSError:
        # Job doesn't permit breakaway — still hidden; the wait-first design means
        # a premature kill just skips the update (no corruption).
        subprocess.Popen(cmd, creationflags=base, close_fds=True, cwd=str(script_dir))
    return script
