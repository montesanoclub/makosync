"""Parsers for CTS Dolphin output files (.do3, .do4, .csv).

The Dolphin Windows software writes one immutable file per heat to a folder:

  * ``.do4`` — header ``event;heat;num_splits;round`` then 10 lane rows
    ``LaneN;timerA;timerB;timerC`` (empty timer = ``0``), then a checksum
    line. Default modern format.
  * ``.do3`` — older shape. Same header style (``event;heat;num_splits;round``)
    but the lane rows are ``N;timerA;timerB;timerC`` (bare number, no
    "Lane" prefix) and empty timers are blank (``;;;``) instead of ``0``.
    Filename uses 3 hyphen-separated groups: ``DSET-XXX-RRR####.do3``.
  * ``.csv`` — ``Lane,TimerA,TimerB,TimerC,Final,Empty,DQ`` with a
    pre-computed ``Final`` and explicit Empty/DQ flags.

For .do3 and .do4, the FINAL time is computed from the per-button timers:
1 timer = use it, 2 = average, 3 = median (Dolphin's documented behavior).

Files are cp1252 (Windows-1252). The body is authoritative for event/heat/
round; the filename gives the dataset and the monotonic race-id (our dedup
key).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

logger = logging.getLogger(__name__)

ENCODING = "cp1252"

# .do4: 004-000-001A-0010.do4  ->  dataset=004, race_id=0010
_DO4_NAME = re.compile(r"^(\d+)-\d+-\d+\w*-(\d+)\.do4$", re.IGNORECASE)
# .do3: 004-000-00F0010.do3   ->  dataset=004, race_id=0010 (last 4 digits)
_DO3_NAME = re.compile(r"^(\d+)-\d+-\w*?(\d{4})\.do3$", re.IGNORECASE)
# .csv: 004_Event__Heat_1_Race_10_5_26_2026_14_47.csv
_CSV_NAME = re.compile(r"^(\d+)_Event_(\d*)_Heat_(\d+)_Race_(\d+)_", re.IGNORECASE)

_ROUND_MAP = {
    "all": "F", "a": "F",
    "final": "F", "f": "F",
    "prelim": "P", "p": "P",
    "semi": "S", "s": "S",
}


@dataclass
class LaneTime:
    lane: int
    time: str | None            # "SS.ss" / "M:SS.ss", or None if no time
    timers: list[float] = field(default_factory=list)  # raw button timers (debug/audit)
    dq: bool = False
    place: int | None = None    # official finish place (Meet Manager only); None for Dolphin
    dq_code: str = ""           # raw Hy-Tek Fin_dqcode (Meet Manager only); often empty


@dataclass
class ParsedHeat:
    format: str                 # "do3" | "do4" | "csv" | "mm"
    dataset: str
    event: int
    heat: int
    round: str                  # "F" | "P" | "S"
    race_id: str
    lanes: list[LaneTime] = field(default_factory=list)
    source_file: str = ""
    scored: bool = False        # Meet Manager only: the event has been SCORED
                                # (Event.Event_stat == 'S'). Dolphin/CSV are always
                                # False. The server promotes the official result to
                                # the public TV/meet ONLY when this is True — until an
                                # event is scored the operator can still fix a mis-entry,
                                # so we keep showing the Dolphin time. See mdb_reader.

    @property
    def timed_lanes(self) -> list[LaneTime]:
        return [ln for ln in self.lanes if ln.time is not None or ln.dq]


def format_seconds(secs: float) -> str:
    """Seconds -> swim-time. 95.32 -> '1:35.32', 41.9 -> '41.90'."""
    if secs >= 60:
        m, s = divmod(secs, 60)
        return f"{int(m)}:{s:05.2f}"
    return f"{secs:.2f}"


def _to_secs(token: str) -> float | None:
    """Dolphin timer cell -> seconds, or None for blank/zero/junk."""
    t = token.strip()
    if not t:
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    return v if v > 0 else None


def _final_from_timers(timers: list[float | None]) -> float | None:
    """1 timer -> use; 2 -> average; 3+ -> median. Empty -> None."""
    vals = [v for v in timers if v is not None]
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    if len(vals) == 2:
        return round((vals[0] + vals[1]) / 2, 2)
    return round(median(vals), 2)


def _parse_header(line: str, filename: str) -> tuple[int, int, str] | None:
    """``event;heat;num_splits;round`` -> (event, heat, round). num_splits ignored."""
    parts = line.split(";")
    if len(parts) < 4:
        logger.warning("header malformed in %s: %r", filename, line)
        return None
    try:
        event = int(parts[0]) if parts[0].strip() else 0
        heat = int(parts[1]) if parts[1].strip() else 0
    except ValueError:
        logger.warning("header non-numeric in %s: %r", filename, parts)
        return None
    rnd = _ROUND_MAP.get(parts[3].strip().lower(), "F")
    return event, heat, rnd


def _parse_lane_body(body: list[str]) -> list[LaneTime]:
    """Shared do3/do4 body parser. ``[Lane]N;timerA;timerB;timerC`` rows."""
    lane_rows: dict[int, list[str]] = {}
    for row in body:
        cells = row.split(";")
        if len(cells) < 2:
            continue
        m = re.match(r"(?:Lane)?\s*(\d+)", cells[0].strip(), re.IGNORECASE)
        if not m:
            continue
        lane_no = int(m.group(1))
        lane_rows.setdefault(lane_no, []).append(row)

    lanes: list[LaneTime] = []
    for lane_no in sorted(lane_rows):
        cells = lane_rows[lane_no][-1].split(";")  # last split-row is the final
        timers = [_to_secs(c) for c in cells[1:4]]
        secs = _final_from_timers(timers)
        lanes.append(LaneTime(
            lane=lane_no,
            time=format_seconds(secs) if secs is not None else None,
            timers=[t for t in timers if t is not None],
        ))
    return lanes


def _drop_checksum(lines: list[str]) -> list[str]:
    """Last non-empty line is a hex checksum like 'B02586E5DAB26ABF' — drop it."""
    if lines and re.match(r"^[0-9A-Fa-f]{8,}$", lines[-1].strip()):
        return lines[:-1]
    return lines


def parse_do4_text(text: str, filename: str = "") -> ParsedHeat | None:
    lines = [ln.rstrip("\r\n") for ln in text.split("\n") if ln.strip()]
    if len(lines) < 2:
        return None
    hdr = _parse_header(lines[0], filename)
    if not hdr:
        return None
    event, heat, rnd = hdr

    body = _drop_checksum(lines[1:])
    lanes = _parse_lane_body(body)

    dataset, race_id = "", "0"
    nm = _DO4_NAME.match(Path(filename).name)
    if nm:
        dataset, race_id = nm.group(1), nm.group(2)

    return ParsedHeat(
        format="do4", dataset=dataset, event=event, heat=heat, round=rnd,
        race_id=race_id, lanes=lanes, source_file=Path(filename).name,
    )


def parse_do3_text(text: str, filename: str = "") -> ParsedHeat | None:
    lines = [ln.rstrip("\r\n") for ln in text.split("\n") if ln.strip()]
    if len(lines) < 2:
        return None
    hdr = _parse_header(lines[0], filename)
    if not hdr:
        return None
    event, heat, rnd = hdr

    body = _drop_checksum(lines[1:])
    lanes = _parse_lane_body(body)

    dataset, race_id = "", "0"
    nm = _DO3_NAME.match(Path(filename).name)
    if nm:
        dataset, race_id = nm.group(1), nm.group(2)

    return ParsedHeat(
        format="do3", dataset=dataset, event=event, heat=heat, round=rnd,
        race_id=race_id, lanes=lanes, source_file=Path(filename).name,
    )


def parse_csv_text(text: str, filename: str = "") -> ParsedHeat | None:
    lines = [ln.rstrip("\r\n") for ln in text.split("\n") if ln.strip()]
    if len(lines) < 2:
        return None

    header = [h.strip().lower() for h in lines[0].split(",")]

    def col(*names: str) -> int | None:
        for n in names:
            if n in header:
                return header.index(n)
        return None

    c_lane, c_final = col("lane"), col("final", "time", "result")
    c_a, c_b, c_c = col("timera"), col("timerb"), col("timerc")
    c_empty, c_dq = col("empty"), col("dq")
    if c_lane is None:
        return None

    dataset, event, heat, race_id = "", 0, 0, "0"
    nm = _CSV_NAME.match(Path(filename).name)
    if nm:
        dataset = nm.group(1)
        event = int(nm.group(2)) if nm.group(2) else 0
        heat = int(nm.group(3))
        race_id = nm.group(4)

    lanes: list[LaneTime] = []
    for row in lines[1:]:
        cells = [c.strip() for c in row.split(",")]
        if len(cells) <= c_lane or not cells[c_lane]:
            continue
        try:
            lane_no = int(cells[c_lane])
        except ValueError:
            continue
        is_empty = c_empty is not None and len(cells) > c_empty and cells[c_empty].lower() == "true"
        is_dq = c_dq is not None and len(cells) > c_dq and cells[c_dq].lower() == "true"
        if is_empty and not is_dq:
            continue

        timers: list[float] = []
        for ci in (c_a, c_b, c_c):
            if ci is None or len(cells) <= ci:
                continue
            v = _to_secs(cells[ci])
            if v is not None:
                timers.append(v)

        secs: float | None = None
        if c_final is not None and len(cells) > c_final:
            secs = _to_secs(cells[c_final])
        if secs is None:
            secs = _final_from_timers(timers)  # type: ignore[arg-type]

        lanes.append(LaneTime(
            lane=lane_no,
            time=format_seconds(secs) if secs is not None else None,
            timers=timers,
            dq=is_dq,
        ))

    return ParsedHeat(
        format="csv", dataset=dataset, event=event, heat=heat, round="F",
        race_id=race_id, lanes=lanes, source_file=Path(filename).name,
    )


def parse_file(path: str | Path) -> ParsedHeat | None:
    """Parse by extension. Returns None if unparseable."""
    p = Path(path)
    try:
        text = p.read_text(encoding=ENCODING)
    except OSError as e:
        logger.warning("could not read %s: %s", p, e)
        return None
    ext = p.suffix.lower()
    if ext == ".do4":
        return parse_do4_text(text, p.name)
    if ext == ".do3":
        return parse_do3_text(text, p.name)
    if ext == ".csv":
        return parse_csv_text(text, p.name)
    return None
