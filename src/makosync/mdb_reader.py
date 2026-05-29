"""Manager mode (Meet Manager) — read the Hy-Tek MM ``.mdb`` on the scoring PC,
emit official heat results.

The live *official* overlay is purely positional. The makosmeets program is
already baked from this same ``.mdb`` by ``v2/containers/mdb-parser/convert.mjs``
at meet import, so a result just needs to land on the right baked lane:
``(event_number, heat, lane) -> time/place``. Athlete identity, teams, and relay
legs are already in the bake — we do **not** re-read them here. That makes
MM-mode far simpler than the full ``convert.mjs``: only the *result* slice.

Parity with ``convert.mjs`` is NON-NEGOTIABLE — the overlay must key to the
**same** lane the bake used, or the official time lands on the wrong row (or
nowhere):

  * **event** — ``Event.Event_no``, joined from ``Entry``/``Relay.Event_ptr``
    (``convert.mjs`` builds ``event_number`` from ``Event_no``; entries join via
    ``Event_ptr``: lines 287, 318).
  * **heat** — ``int(Pre_heat) or int(Fin_heat)`` — **Pre wins** (``convert.mjs``
    lines 111-113: ``heatNo = preHeat || finHeat``).
  * **lane** — ``int(Pre_lane) or int(Fin_lane)`` — **Pre wins** (line 126).
  * **time** — ``parseResult(Fin_Time)``: parse decimal seconds, drop if ``<= 0``,
    format ``SS.ss`` / ``M:SS.ss`` (lines 89-97). Reuses ``parser.format_seconds``,
    which matches ``convert.mjs``'s formatting for every hundredth-precision value
    (i.e. all real swim times). The two round-mode quirks only differ on exact
    sub-hundredth ``.xx5`` ties, which MM never stores — so it can't diverge here.
  * **place** — ``int(Fin_place) or None`` (``0``/blank -> ``None``; line 95).
  * **athlete presence** — ``convert.mjs`` drops an individual entry whose
    ``Ath_no`` has no matching ``Athlete`` row (``if (!athlete) return null``,
    line 122), so that lane is never baked. When the ``Athlete`` roster is passed
    in, we mirror that: an individual result with a dangling ``Ath_no`` is skipped
    so our output stays a subset of the baked lanes. (Relays aren't athlete-keyed
    at the lane level, so they're never dropped — same as ``convert.mjs``.)

    ⚠ The plan's prose table said "Fin_heat with Pre_ fallback" — that's
    **inverted** vs. the actual ``convert.mjs`` code, which is Pre-first. We
    mirror the CODE. For RCSL timed finals ``Pre_ == Fin_`` so it's moot; for a
    prelim/finals meet (e.g. City Meet) it matters and Pre-first is correct
    because the bake keyed off the seed (Pre) positions.

DQ is not represented in ``convert.mjs`` nor in the documented ``Entry`` schema
(``docs/hy-tek-mdb-schema.md`` §1), so we do **not** synthesize it here
(``LaneTime.dq`` stays ``False``). Wiring real DQ detection needs a confirmed
field from a real DQ'd row on the scoring PC — see the validation protocol in
the plan.

Reads are done by shelling out to **mdbtools** (``mdb-export``) — the same tool
``convert.mjs`` and ``dump_hy_tek_mdb.py`` use server-side. mdbtools reads the raw
Jet structure, so (unlike the ACE/ODBC driver) it **ignores the Hy-Tek database
password** that protects real RCSL files, and it reads both Meet Manager and Team
Manager databases. The ``mdb-export`` binary is bundled into the ``.exe`` (or found
on ``PATH`` in dev/CI). The pure transform :func:`rows_to_heats` has no external
dependency and is what the field-parity test exercises.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from .parser import LaneTime, ParsedHeat, format_seconds

logger = logging.getLogger(__name__)

# Tables we export. Missing tables degrade to [] (a meet with no relays simply
# has no Relay rows).
_EVENT_TABLE = "Event"
_ENTRY_TABLE = "Entry"
_RELAY_TABLE = "Relay"
_ATHLETE_TABLE = "Athlete"
_MEET_TABLE = "Meet"

# Code maps for building human event names (mirror convert.mjs).
_STROKE_MAP = {"A": "Freestyle", "B": "Backstroke", "C": "Breaststroke",
               "D": "Butterfly", "E": "Medley", "F": "Individual Medley"}
_GENDER_MAP = {"F": "Girls", "M": "Boys", "X": "Mixed"}
_COURSE_MAP = {"1": "SCY", "2": "SCM", "3": "SCY", "4": "LCY", "5": "LCM"}

# mdbtools `mdb-export` binary. Resolution order: env override, bundled copy,
# then PATH (dev/CI on Linux/macOS).
_MDB_EXPORT_ENV = "MAKOSYNC_MDB_EXPORT"
_MDB_EXPORT_NAME = "mdb-export.exe" if os.name == "nt" else "mdb-export"

# Spawn mdb-export with no console window. The shipped app is windowed
# (--noconsole), so without this each table read flashes a console — annoyingly,
# every poll cycle during a meet, not just on the events push.
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


# ---- JS-faithful numeric parsing -----------------------------------------
# convert.mjs runs on strings from mdb-export and uses JS parseFloat/parseInt,
# which parse a *leading* number and ignore trailing junk (and yield NaN when
# there's no leading number). pyodbc instead hands us already-typed values
# (int/float). These helpers accept either and reproduce the JS semantics so
# the result is identical regardless of how the value arrived.

_LEADING_FLOAT = re.compile(r"^\s*[+-]?(\d+\.?\d*|\.\d+)")
_LEADING_INT = re.compile(r"^\s*[+-]?\d+")


def _parse_float(v: Any) -> float | None:
    """Mirror JS ``parseFloat``: leading number or ``None``."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _LEADING_FLOAT.match(str(v))
    return float(m.group(0)) if m else None


def _parse_int(v: Any) -> int | None:
    """Mirror JS ``parseInt``: leading integer or ``None``."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    m = _LEADING_INT.match(str(v))
    return int(m.group(0)) if m else None


def result_time(fin_time: Any) -> str | None:
    """``convert.mjs`` ``parseResult`` time half: decimal seconds -> swim time.

    Returns ``None`` for blank / zero / non-positive (no result yet), matching
    ``if (!val || val <= 0) return [null, null]``.
    """
    val = _parse_float(fin_time)
    if not val or val <= 0:
        return None
    return format_seconds(val)


def _heat_key(row: Mapping[str, Any]) -> int:
    """``preHeat || finHeat`` — Pre wins (convert.mjs:111-113)."""
    pre = _parse_int(row.get("pre_heat")) or 0
    fin = _parse_int(row.get("fin_heat")) or 0
    return pre or fin


def _lane_key(row: Mapping[str, Any]) -> int:
    """``Pre_lane || Fin_lane || 0`` — Pre wins (convert.mjs:126)."""
    pre = _parse_int(row.get("pre_lane")) or 0
    fin = _parse_int(row.get("fin_lane")) or 0
    return pre or fin


def _lower_keyed(row: Mapping[str, Any]) -> dict[str, Any]:
    """Case-insensitive row access: MM column case can vary by export path."""
    return {str(k).lower(): v for k, v in row.items()}


def rows_to_heats(
    events: Iterable[Mapping[str, Any]],
    entries: Iterable[Mapping[str, Any]],
    relays: Iterable[Mapping[str, Any]],
    athletes: Iterable[Mapping[str, Any]] | None = None,
    source_file: str = "",
) -> list[ParsedHeat]:
    """Pure transform: MM table rows -> official :class:`ParsedHeat` list.

    Rows are dict-like (column name -> value); keys are matched
    case-insensitively. Only lanes that already have a finish time are emitted
    (an entry with no ``Fin_Time`` is just a seeded swimmer, already baked), and
    only heats with at least one result lane are returned.

    Individual (``Entry``) and relay (``Relay``) rows both map to
    ``(event_no, heat, lane) -> time/place`` for the positional overlay; relay
    leg names are irrelevant (they're in the bake). The one asymmetry mirrors
    ``convert.mjs``: when ``athletes`` is given, an *individual* entry whose
    ``Ath_no`` isn't in the roster is dropped (the bake drops it too, so emitting
    it would target a lane that was never baked). Relays are never athlete-dropped.
    When ``athletes`` is ``None`` the filter is off (positional-only).
    """
    # Event_ptr -> Event_no. Entries/relays carry the ptr; the program is keyed
    # by the human event number.
    ptr_to_no: dict[int, int] = {}
    for ev in events:
        r = _lower_keyed(ev)
        ptr = _parse_int(r.get("event_ptr"))
        no = _parse_int(r.get("event_no"))
        if ptr and no:
            ptr_to_no[ptr] = no

    # Roster of known athlete numbers, mirroring convert.mjs's athleteLookup keys.
    ath_set: set[int] | None = None
    if athletes is not None:
        ath_set = set()
        for a in athletes:
            no = _parse_int(_lower_keyed(a).get("ath_no"))
            if no:
                ath_set.add(no)

    # (event_no, heat) -> {lane: LaneTime}. Dict-by-lane so a later row for the
    # same lane overrides (mutable MDB; last read wins, mirroring a fresh parse).
    heats: dict[tuple[int, int], dict[int, LaneTime]] = {}

    def _add(row: Mapping[str, Any], *, is_individual: bool) -> None:
        r = _lower_keyed(row)
        ptr = _parse_int(r.get("event_ptr"))
        if not ptr or ptr not in ptr_to_no:
            return
        if is_individual and ath_set is not None:
            ath = _parse_int(r.get("ath_no"))
            if ath is None or ath not in ath_set:
                return  # dangling Ath_no — convert.mjs drops it, so do we
        event_no = ptr_to_no[ptr]
        heat = _heat_key(r)
        if not heat:
            return
        lane = _lane_key(r)
        if lane <= 0:
            # No assigned lane to overlay onto — skip (server requires lane >= 0
            # but a baked lane is always 1..N; lane 0 means unseeded).
            return
        time = result_time(r.get("fin_time"))
        if time is None:
            return  # seeded but no result yet — nothing to publish
        place = _parse_int(r.get("fin_place")) or None
        heats.setdefault((event_no, heat), {})[lane] = LaneTime(
            lane=lane, time=time, place=place,
        )

    for row in entries:
        _add(row, is_individual=True)
    for row in relays:
        _add(row, is_individual=False)

    out: list[ParsedHeat] = []
    for (event_no, heat) in sorted(heats):
        lanes = [heats[(event_no, heat)][ln] for ln in sorted(heats[(event_no, heat)])]
        out.append(ParsedHeat(
            format="mm",
            dataset="mm",
            event=event_no,
            heat=heat,
            round="F",
            race_id="",  # the watcher stamps a per-heat content hash before POST
            lanes=lanes,
            source_file=source_file,
        ))
    return out


# ---- event list (for the Dolphin events-relay) ---------------------------
# Hands the Dolphin software the seeded event/heat list so the operator picks
# events instead of hand-typing. Names mirror convert.mjs's `description`; the
# name is just an operator-facing label (NOT a parity-keyed field), so empty
# components are dropped for readability.

def _age_group(r: Mapping[str, Any]) -> str:
    low = _parse_int(r.get("low_age")) or 0
    high = _parse_int(r.get("high_age")) or 0
    if low == 0 and high == 0:
        return "Open"
    if low == 0:
        return f"{high} & Under"
    if high == 0:
        return f"{low} & Over"
    return f"{low}-{high}"


def _event_name(r: Mapping[str, Any], dist_unit: str = "") -> str:
    def m(d: dict, key: str) -> str:
        v = str(r.get(key, "")).strip()
        return d.get(v, v)
    gender = m(_GENDER_MAP, "event_gender")
    distance = str(r.get("event_dist", "")).strip()
    stroke = m(_STROKE_MAP, "event_stroke")
    parts = [p for p in (gender, _age_group(r), distance, dist_unit, stroke) if p]
    return " ".join(parts)


def write_dolphin_events_csv(path: str | Path, events: Iterable[Mapping[str, Any]]) -> int:
    """Write the event list as a Dolphin events CSV (``event,name,heats`` per row).

    Format mirrors JohnStrunk's ``events2dolphin`` (event number, name, heat count),
    headerless. ⚠ The exact columns/headers the installed CTS Dolphin build imports
    are UNVERIFIED — confirm on the Dolphin PC's Events screen before relying on it.
    """
    p = Path(path)
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    rows = list(events)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for e in rows:
            w.writerow([e.get("event"), e.get("name"), e.get("heats")])
    return len(rows)


def build_event_list(
    events: Iterable[Mapping[str, Any]],
    entries: Iterable[Mapping[str, Any]],
    relays: Iterable[Mapping[str, Any]],
    meet: Iterable[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """MM table rows -> ``[{event, name, heats}]`` for the Dolphin events screen.

    Only events with at least one seeded heat are returned (those are the ones
    the operator runs). ``heats`` = count of distinct heat numbers across the
    event's entries/relays, keyed the same way as results (``Pre_heat || Fin_heat``).
    """
    dist_unit = ""
    meet_rows = list(meet or [])
    if meet_rows:
        course = _COURSE_MAP.get(str(_lower_keyed(meet_rows[0]).get("meet_course", "")).strip())
        if course:
            dist_unit = "Yard" if course.endswith("Y") else "Meter"

    heats_by_ptr: dict[int, set[int]] = {}
    for row in (*entries, *relays):
        r = _lower_keyed(row)
        ptr = _parse_int(r.get("event_ptr"))
        heat = _heat_key(r)
        if ptr and heat:
            heats_by_ptr.setdefault(ptr, set()).add(heat)

    out: list[dict[str, Any]] = []
    for ev in events:
        r = _lower_keyed(ev)
        ptr = _parse_int(r.get("event_ptr"))
        no = _parse_int(r.get("event_no"))
        if not ptr or not no:
            continue
        heat_count = len(heats_by_ptr.get(ptr, ()))
        if heat_count == 0:
            continue  # not seeded / no entries — nothing to run
        out.append({"event": no, "name": _event_name(r, dist_unit), "heats": heat_count})
    out.sort(key=lambda e: e["event"])
    return out


# ---- mdbtools (mdb-export) I/O -------------------------------------------


def _resolve_mdb_export() -> str:
    """Locate the ``mdb-export`` binary: env override -> bundled -> PATH."""
    env = os.environ.get(_MDB_EXPORT_ENV)
    if env:
        return env
    candidates: list[Path] = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates += [
            Path(base) / "makosync" / "tools" / _MDB_EXPORT_NAME,
            Path(base) / "tools" / _MDB_EXPORT_NAME,
            Path(base) / _MDB_EXPORT_NAME,
        ]
    candidates.append(Path(__file__).resolve().parent / "tools" / _MDB_EXPORT_NAME)
    for c in candidates:
        if c.exists():
            return str(c)
    return _MDB_EXPORT_NAME  # rely on PATH (dev/CI on Linux/macOS)


def _csv_to_rows(csv_text: str) -> list[dict[str, str]]:
    """Parse ``mdb-export`` CSV (header row, ``"``-quoted fields) into row dicts."""
    if not csv_text.strip():
        return []
    return [dict(r) for r in csv.DictReader(io.StringIO(csv_text))]


def _export(mdb_path: Path, table: str) -> list[dict[str, str]]:
    """``mdb-export <db> <table>`` -> list of column->value dicts; [] if absent.

    mdbtools reads the raw Jet file, so it ignores the Hy-Tek database password
    and never writes — safe against the (copied) file.
    """
    exe = _resolve_mdb_export()
    try:
        proc = subprocess.run(
            [exe, "-D", "%Y-%m-%d %H:%M:%S", str(mdb_path), table],
            capture_output=True, text=True, check=False, **_NO_WINDOW,
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            f"mdb-export not found ('{exe}'). Manager mode needs the bundled "
            f"mdbtools binary, the {_MDB_EXPORT_ENV} env var, or mdb-export on PATH."
        ) from e
    if proc.returncode != 0:
        # Missing table or transient read glitch — treat as empty (mirrors
        # dump_hy_tek_mdb.py), so a relay-less meet just has no Relay rows.
        logger.info("mdb-export %s rc=%s: %s", table, proc.returncode, proc.stderr.strip()[:200])
        return []
    return _csv_to_rows(proc.stdout)


def copy_to_temp(mdb_path: str | Path) -> Path:
    """Copy the live ``.mdb`` to a temp file and return the copy's path.

    This is the key move: reading a *copy* sidesteps any lock MM holds on the
    live file, and keeps the 3 MB on local disk (only changed-heat JSON crosses
    the wire). ``shutil.copy2`` opens the source read-only/shared, so it does
    not block MM.
    """
    src = Path(mdb_path)
    fd, tmp = tempfile.mkstemp(suffix=src.suffix or ".mdb", prefix="makosync_mm_")
    os.close(fd)
    try:
        shutil.copy2(src, tmp)
    except Exception:
        # The copy is expected to fail sometimes (MM holds the file mid-write).
        # Don't leak the empty mkstemp file on every failed cycle — clean up,
        # then re-raise so the watcher retries next tick.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return Path(tmp)


def read_mdb(mdb_path: str | Path, *, use_copy: bool = True) -> list[ParsedHeat]:
    """Read the MM ``.mdb`` via mdb-export and return official heats (convert.mjs parity).

    By default reads a temp copy (lock-safe vs. MM holding the file). Raises on a
    copy failure or a missing ``mdb-export`` so the caller can retry next cycle —
    it never blocks MM.
    """
    src = Path(mdb_path)
    work = copy_to_temp(src) if use_copy else src
    try:
        events = _export(work, _EVENT_TABLE)
        entries = _export(work, _ENTRY_TABLE)
        relays = _export(work, _RELAY_TABLE)
        athletes = _export(work, _ATHLETE_TABLE)
    finally:
        if use_copy:
            try:
                work.unlink()
            except OSError:
                logger.debug("could not remove temp MDB copy %s", work)
    return rows_to_heats(events, entries, relays, athletes=athletes, source_file=src.name)


def read_event_list_from_mdb(mdb_path: str | Path, *, use_copy: bool = True) -> list[dict[str, Any]]:
    """Read the MM ``.mdb`` and return the seeded event list for the Dolphin relay."""
    src = Path(mdb_path)
    work = copy_to_temp(src) if use_copy else src
    try:
        events = _export(work, _EVENT_TABLE)
        entries = _export(work, _ENTRY_TABLE)
        relays = _export(work, _RELAY_TABLE)
        meet = _export(work, _MEET_TABLE)
    finally:
        if use_copy:
            try:
                work.unlink()
            except OSError:
                logger.debug("could not remove temp MDB copy %s", work)
    return build_event_list(events, entries, relays, meet)
