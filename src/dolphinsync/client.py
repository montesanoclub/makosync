"""HTTP client for the makosmeets live-results ingest endpoints.

Two channels per heat (see ``docs/ingest-contract.md``):

  * ``POST /api/live-results/ingest/`` — JSON parsed times, fired the instant a
    file is parsed. This is what feeds the pool-deck TV.
  * ``POST /api/live-results/ingest/file/`` — multipart raw file upload, fired
    after the JSON succeeds. Forensic copy; the server archives it to R2 under
    ``dolphin-raw/<date>/E<event>-H<heat>-<race_id>.<ext>``.

Stdlib only (``urllib``). Bearer auth. Exponential backoff on 5xx/network;
permanent fail on 4xx (we don't retry a bad request — the file would just
fail forever).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import mimetypes
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

from . import __version__
from .parser import ParsedHeat

logger = logging.getLogger(__name__)

USER_AGENT = f"DolphinSync/{__version__}"
DEFAULT_TIMEOUT = 8.0       # seconds — the meet-PC network can be flaky
RETRY_DELAYS = (1, 2, 4, 8) # 4 retries (~15s total) then give up

# The makosmeets live-results endpoints. trailingSlash: true on the server
# 308-redirects a slashless POST and drops the body, so the slash is required.
HEAT_PATH = "/api/live-results/ingest/"
FILE_PATH = "/api/live-results/ingest/file/"


def normalize_base_url(raw: str) -> str:
    """Coerce whatever the operator types into a clean base URL.

    Accepts a bare host, a base URL, or the *full* ingest endpoint — with or
    without a trailing slash — and returns a scheme'd base with no trailing
    slash. send_heat/send_file append HEAT_PATH/FILE_PATH themselves, so if the
    operator pastes the full endpoint we must peel it back to the base or we'd
    POST to a doubled path (the bug that bit us in the field).
    """
    u = raw.strip()
    if not u:
        return ""
    if "://" not in u:
        u = "https://" + u
    u = u.rstrip("/")
    # Peel a pasted-in endpoint path back to the base (check the longer one first).
    for suffix in (FILE_PATH.rstrip("/"), HEAT_PATH.rstrip("/")):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
            break
    return u


@dataclass
class IngestResult:
    ok: bool
    status: int
    detail: str = ""


def _heat_to_payload(heat: ParsedHeat, tier: str = "unofficial") -> dict[str, Any]:
    """Build the JSON body for ``POST /ingest/heat``."""
    return {
        "source_file": heat.source_file,
        "format": heat.format,
        "dataset": heat.dataset,
        "race_id": heat.race_id,
        "event": heat.event,
        "heat": heat.heat,
        "round": heat.round,
        "tier": tier,
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lanes": [dataclasses.asdict(ln) for ln in heat.timed_lanes],
    }


class IngestClient:
    def __init__(self, base_url: str, token: str = "", timeout: float = DEFAULT_TIMEOUT):
        self.base_url = normalize_base_url(base_url)
        self.token = token or ""
        self.timeout = timeout

    # ---- public API ---------------------------------------------------

    def send_heat(self, heat: ParsedHeat, tier: str = "unofficial") -> IngestResult:
        body = json.dumps(_heat_to_payload(heat, tier)).encode("utf-8")
        return self._send_with_retry(
            f"{self.base_url}{HEAT_PATH}", body,
            headers={"Content-Type": "application/json"},
        )

    def send_file(self, path: Path, heat: ParsedHeat) -> IngestResult:
        body, content_type = _build_multipart(path, heat)
        return self._send_with_retry(
            f"{self.base_url}{FILE_PATH}", body,
            headers={"Content-Type": content_type},
        )

    # ---- internals ----------------------------------------------------

    def _send_with_retry(self, url: str, body: bytes, *, headers: dict[str, str]) -> IngestResult:
        merged_headers = {
            "User-Agent": USER_AGENT,
            **headers,
        }
        if self.token:
            merged_headers["Authorization"] = f"Bearer {self.token}"
        last: IngestResult = IngestResult(ok=False, status=0, detail="not attempted")
        for attempt, delay in enumerate([0, *RETRY_DELAYS]):
            if delay:
                time.sleep(delay)
            try:
                req = request.Request(url, data=body, headers=merged_headers, method="POST")
                with request.urlopen(req, timeout=self.timeout) as resp:
                    return IngestResult(ok=True, status=resp.status, detail="ok")
            except error.HTTPError as e:
                detail = _read_err(e)
                last = IngestResult(ok=False, status=e.code, detail=detail)
                if 400 <= e.code < 500:
                    logger.warning("POST %s -> %d (no retry): %s", url, e.code, detail)
                    return last  # permanent
                logger.info("POST %s -> %d (attempt %d): %s", url, e.code, attempt + 1, detail)
            except (error.URLError, TimeoutError, OSError) as e:
                last = IngestResult(ok=False, status=0, detail=str(e))
                logger.info("POST %s network error (attempt %d): %s", url, attempt + 1, e)
        return last


def _read_err(e: error.HTTPError) -> str:
    try:
        return e.read().decode("utf-8", "replace")[:500]
    except Exception:
        return ""


def _build_multipart(path: Path, heat: ParsedHeat) -> tuple[bytes, str]:
    """Build a multipart/form-data body with the raw file + a few audit fields."""
    boundary = f"----DolphinSync{uuid.uuid4().hex}"
    crlf = b"\r\n"

    def field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}"
        ).encode("utf-8") + crlf

    file_bytes = path.read_bytes()
    mime, _ = mimetypes.guess_type(path.name)
    mime = mime or "application/octet-stream"

    parts: list[bytes] = []
    parts.append(field("source_file", heat.source_file))
    parts.append(field("format", heat.format))
    parts.append(field("dataset", heat.dataset))
    parts.append(field("race_id", heat.race_id))
    parts.append(field("event", str(heat.event)))
    parts.append(field("heat", str(heat.heat)))
    parts.append(field("round", heat.round))
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(file_bytes)
    parts.append(crlf)
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))

    body = b"".join(parts)
    return body, f"multipart/form-data; boundary={boundary}"
