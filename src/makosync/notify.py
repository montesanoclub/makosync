"""Windows toast notifications (stdlib only — shells out to PowerShell).

The Meet Manager receiver pops a toast when a heat's results land so the
operator knows to "Get Times" in Meet Manager. We build the toast with the
built-in WinRT ``ToastNotificationManager`` via ``powershell -EncodedCommand``
— no extra Python dependency and nothing to install on the meet PC. Anything
that goes wrong degrades to a logged line: a missed toast must never crash the
receiver loop.
"""

from __future__ import annotations

import base64
import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

APP_ID = "MakoSync"

# Uses the built-in WinRT toast API — present on Windows 10/11, no module needed.
_PS_TEMPLATE = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime] | Out-Null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $t.GetElementsByTagName('text')
$texts.Item(0).AppendChild($t.CreateTextNode(__TITLE__)) | Out-Null
$texts.Item(1).AppendChild($t.CreateTextNode(__MESSAGE__)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($t)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier(__APPID__).Show($toast)
"""


def _ps_literal(s: str) -> str:
    """A PowerShell single-quoted string literal (single quotes are doubled)."""
    return "'" + s.replace("'", "''") + "'"


def build_script(title: str, message: str, app_id: str = APP_ID) -> str:
    """The PowerShell toast script (exposed for testing)."""
    return (
        _PS_TEMPLATE
        .replace("__TITLE__", _ps_literal(title))
        .replace("__MESSAGE__", _ps_literal(message))
        .replace("__APPID__", _ps_literal(app_id))
    )


def notify(title: str, message: str) -> bool:
    """Show a Windows toast. Returns True if dispatched; never raises."""
    if sys.platform != "win32":
        logger.info("toast (non-Windows, not shown): %s — %s", title, message)
        return False
    try:
        enc = base64.b64encode(build_script(title, message).encode("utf-16-le")).decode("ascii")
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
            check=True, capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except Exception as e:  # noqa: BLE001 — a missed toast must never break the loop
        logger.warning("toast failed (%s — %s): %s", title, message, e)
        return False
