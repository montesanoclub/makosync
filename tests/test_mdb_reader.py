"""Meet Manager field-parity tests.

These lock MM-mode output to what ``v2/containers/mdb-parser/convert.mjs`` would
produce for the same rows — the overlay must key to the SAME (event, heat, lane)
the bake used, or official times land on the wrong row. Exercises the pure
``rows_to_heats`` transform (no pyodbc, no real .mdb needed).

Expected values below are computed the way convert.mjs computes them:
  * event  = Event.Event_no (joined via Event_ptr)
  * heat   = Pre_heat || Fin_heat            (PRE wins — convert.mjs:111-113)
  * lane   = Pre_lane || Fin_lane            (PRE wins — convert.mjs:126)
  * time   = parseResult(Fin_Time): decimal seconds, drop <= 0, SS.ss / M:SS.ss
  * place  = parseInt(Fin_place) || None     (0/blank -> None)
"""

from __future__ import annotations

from makosync import mdb_reader as mr
from makosync.mdb_reader import (
    _csv_to_rows, _parse_float, _parse_int, build_event_list, result_time,
    rows_to_heats, write_dolphin_events_csv,
)


# ---- helpers (JS parseFloat/parseInt + parseResult parity) ----------------


def test_result_time_matches_parseResult():
    # decimal seconds -> swim time, byte-identical to convert.mjs parseResult.
    assert result_time("8.13") == "8.13"
    assert result_time("41.9") == "41.90"
    assert result_time("65.43") == "1:05.43"
    assert result_time("125.7") == "2:05.70"
    # blank / zero / non-numeric -> no result (None), like `if (!val || val<=0)`.
    assert result_time("") is None
    assert result_time("0") is None
    assert result_time("NT") is None
    assert result_time(None) is None
    # typed (pyodbc) values parse the same as their string form.
    assert result_time(65.43) == "1:05.43"
    assert result_time(0) is None


def test_parse_int_float_js_semantics():
    assert _parse_int("3") == 3
    assert _parse_int("0") == 0
    assert _parse_int("") is None
    assert _parse_int("12X") == 12     # leading int, trailing junk ignored
    assert _parse_int(3.0) == 3
    assert _parse_int(None) is None
    assert _parse_float("65.43") == 65.43
    assert _parse_float("X65") is None  # no leading number
    assert _parse_float(7) == 7.0


# ---- rows_to_heats --------------------------------------------------------

EVENTS = [
    {"Event_ptr": "10", "Event_no": "5", "Ind_rel": "I"},
    {"Event_ptr": "20", "Event_no": "8", "Ind_rel": "R"},
]


def _heat(heats, event, heat_no):
    return next(h for h in heats if h.event == event and h.heat == heat_no)


def test_individual_timed_finals_basic():
    entries = [
        {"Event_ptr": "10", "Pre_heat": "1", "Fin_heat": "1",
         "Pre_lane": "4", "Fin_lane": "4", "Fin_Time": "65.43", "Fin_place": "1"},
        {"Event_ptr": "10", "Pre_heat": "1", "Fin_heat": "1",
         "Pre_lane": "3", "Fin_lane": "3", "Fin_Time": "56.10", "Fin_place": "2"},
    ]
    heats = rows_to_heats(EVENTS, entries, [])
    assert len(heats) == 1
    h = heats[0]
    assert h.format == "mm" and h.event == 5 and h.heat == 1 and h.round == "F"
    # lanes sorted ascending; 56.10s stays sub-minute, 65.43s rolls to M:SS.ss
    assert [(ln.lane, ln.time, ln.place, ln.dq) for ln in h.lanes] == [
        (3, "56.10", 2, False),
        (4, "1:05.43", 1, False),
    ]


def test_pre_wins_over_fin_for_heat_and_lane():
    # The parity catch: convert.mjs keys Pre_ first, NOT Fin_ first as the plan
    # prose said. A prelim/finals divergence (Pre != Fin) must follow Pre.
    entries = [
        {"Event_ptr": "10", "Pre_heat": "2", "Fin_heat": "9",
         "Pre_lane": "4", "Fin_lane": "7", "Fin_Time": "30.00", "Fin_place": "1"},
    ]
    heats = rows_to_heats(EVENTS, entries, [])
    assert len(heats) == 1
    h = heats[0]
    assert h.heat == 2          # Pre_heat, not Fin_heat (9)
    assert h.lanes[0].lane == 4  # Pre_lane, not Fin_lane (7)


def test_fin_fallback_when_pre_absent():
    # Pre_ blank/zero -> fall back to Fin_ (convert.mjs `preHeat || finHeat`).
    entries = [
        {"Event_ptr": "10", "Pre_heat": "0", "Fin_heat": "3",
         "Pre_lane": "", "Fin_lane": "5", "Fin_Time": "30.00", "Fin_place": "1"},
    ]
    h = rows_to_heats(EVENTS, entries, [])[0]
    assert h.heat == 3 and h.lanes[0].lane == 5


def test_no_result_yet_is_dropped():
    # Seeded swimmers without a finish time are already baked; don't publish them.
    entries = [
        {"Event_ptr": "10", "Pre_heat": "1", "Pre_lane": "4", "Fin_Time": "", "Fin_place": ""},
        {"Event_ptr": "10", "Pre_heat": "1", "Pre_lane": "5", "Fin_Time": "0", "Fin_place": "0"},
    ]
    # The only entries have no result -> the heat has no result lanes -> not emitted.
    assert rows_to_heats(EVENTS, entries, []) == []


def test_place_zero_or_blank_is_none():
    entries = [
        {"Event_ptr": "10", "Pre_heat": "1", "Pre_lane": "4", "Fin_Time": "30.00", "Fin_place": "0"},
        {"Event_ptr": "10", "Pre_heat": "1", "Pre_lane": "5", "Fin_Time": "31.00", "Fin_place": ""},
    ]
    h = rows_to_heats(EVENTS, entries, [])[0]
    assert all(ln.place is None for ln in h.lanes)


def test_relays_handled_uniformly():
    relays = [
        {"Event_ptr": "20", "Pre_heat": "1", "Fin_heat": "1",
         "Pre_lane": "1", "Fin_lane": "1", "Fin_Time": "120.55", "Fin_place": "1", "Team_ltr": "A"},
    ]
    heats = rows_to_heats(EVENTS, [], relays)
    assert len(heats) == 1
    h = heats[0]
    assert h.event == 8 and h.heat == 1
    assert (h.lanes[0].lane, h.lanes[0].time, h.lanes[0].place) == (1, "2:00.55", 1)


def test_unknown_event_ptr_skipped():
    entries = [
        {"Event_ptr": "999", "Pre_heat": "1", "Pre_lane": "4", "Fin_Time": "30.00", "Fin_place": "1"},
    ]
    assert rows_to_heats(EVENTS, entries, []) == []


def test_lane_zero_skipped():
    entries = [
        {"Event_ptr": "10", "Pre_heat": "1", "Pre_lane": "0", "Fin_lane": "0", "Fin_Time": "30.00", "Fin_place": "1"},
    ]
    assert rows_to_heats(EVENTS, entries, []) == []


def test_typed_and_case_insensitive_rows():
    # pyodbc hands back typed values and may vary column case — must parse the
    # same as the string/Capitalized form.
    events = [{"event_ptr": 10, "event_no": 5, "ind_rel": "I"}]
    entries = [
        {"event_ptr": 10, "pre_heat": 1, "fin_heat": 1, "pre_lane": 4, "fin_lane": 4,
         "fin_time": 65.43, "fin_place": 1},
    ]
    h = rows_to_heats(events, entries, [])[0]
    assert h.event == 5 and h.heat == 1
    assert (h.lanes[0].lane, h.lanes[0].time, h.lanes[0].place) == (4, "1:05.43", 1)


def test_individual_entry_with_missing_athlete_is_dropped():
    # convert.mjs drops an individual entry whose Ath_no has no Athlete row
    # (so it's never baked). With the roster passed in, we must drop it too.
    athletes = [{"Ath_no": "100"}, {"Ath_no": "101"}]
    entries = [
        {"Event_ptr": "10", "Ath_no": "100", "Pre_heat": "1", "Pre_lane": "4", "Fin_Time": "30.00", "Fin_place": "1"},
        {"Event_ptr": "10", "Ath_no": "999", "Pre_heat": "1", "Pre_lane": "5", "Fin_Time": "31.00", "Fin_place": "2"},
    ]
    h = rows_to_heats(EVENTS, entries, [], athletes=athletes)[0]
    assert [ln.lane for ln in h.lanes] == [4]  # dangling Ath_no 999 (lane 5) dropped


def test_relay_lane_kept_regardless_of_athlete_roster():
    # Relays aren't athlete-keyed at the lane level — never dropped (parity).
    athletes = [{"Ath_no": "100"}]
    relays = [
        {"Event_ptr": "20", "Pre_heat": "1", "Pre_lane": "2", "Fin_Time": "120.55", "Fin_place": "1"},
    ]
    h = rows_to_heats(EVENTS, [], relays, athletes=athletes)[0]
    assert h.lanes[0].lane == 2


def test_no_roster_means_no_athlete_filter():
    # athletes=None -> positional-only, every result lane kept (back-compat).
    entries = [
        {"Event_ptr": "10", "Ath_no": "999", "Pre_heat": "1", "Pre_lane": "5", "Fin_Time": "31.00", "Fin_place": "1"},
    ]
    h = rows_to_heats(EVENTS, entries, [], athletes=None)[0]
    assert h.lanes[0].lane == 5


def test_multiple_events_and_heats_sorted():
    entries = [
        {"Event_ptr": "10", "Pre_heat": "2", "Pre_lane": "4", "Fin_Time": "30.00", "Fin_place": "1"},
        {"Event_ptr": "10", "Pre_heat": "1", "Pre_lane": "4", "Fin_Time": "31.00", "Fin_place": "1"},
    ]
    heats = rows_to_heats(EVENTS, entries, [])
    assert [(h.event, h.heat) for h in heats] == [(5, 1), (5, 2)]  # sorted


# ---- mdb-export CSV pipeline (no binary needed) ---------------------------

def test_csv_export_pipeline():
    # Simulate mdb-export's CSV (header row, "-quoted text) and run the full
    # CSV -> rows -> heats path that the bundled binary feeds.
    events = _csv_to_rows('Event_ptr,Event_no,Ind_rel\n10,5,"I"\n')
    entries = _csv_to_rows(
        'Event_ptr,Ath_no,Pre_heat,Fin_heat,Pre_lane,Fin_lane,Fin_Time,Fin_place\n'
        '10,7,1,1,4,4,65.43,1\n'
        '10,8,1,1,3,3,56.10,2\n'
    )
    athletes = _csv_to_rows('Ath_no\n7\n8\n')
    heats = rows_to_heats(events, entries, [], athletes=athletes)
    assert len(heats) == 1
    h = heats[0]
    assert (h.event, h.heat) == (5, 1)
    assert [(ln.lane, ln.time, ln.place) for ln in h.lanes] == [(3, "56.10", 2), (4, "1:05.43", 1)]


def test_csv_to_rows_empty_and_header_only():
    assert _csv_to_rows("") == []
    assert _csv_to_rows("   ") == []
    assert _csv_to_rows("Event_ptr,Event_no\n") == []  # header, no data rows


def test_resolve_mdb_export_env_override(monkeypatch):
    monkeypatch.setenv(mr._MDB_EXPORT_ENV, r"C:\custom\mdb-export.exe")
    assert mr._resolve_mdb_export() == r"C:\custom\mdb-export.exe"


# ---- event list (Dolphin relay) -------------------------------------------

def test_build_event_list_names_and_heat_counts():
    events = _csv_to_rows(
        "Event_ptr,Event_no,Event_gender,Low_age,High_Age,Event_dist,Event_stroke,Ind_rel\n"
        '10,1,"M","9","10","50","A","I"\n'
        '20,2,"X","0","0","200","E","R"\n'
        '30,3,"F","0","12","100","B","I"\n'  # no entries -> excluded
    )
    entries = _csv_to_rows("Event_ptr,Pre_heat,Fin_heat\n10,1,1\n10,2,2\n10,2,2\n")  # ev1: heats {1,2}
    relays = _csv_to_rows("Event_ptr,Pre_heat\n20,1\n")                              # ev2: heat {1}
    meet = _csv_to_rows("Meet_course\n5\n")  # 5 -> LCM -> "Meter"
    lst = build_event_list(events, entries, relays, meet)
    assert lst == [
        {"event": 1, "name": "Boys 9-10 50 Meter Freestyle", "heats": 2},
        {"event": 2, "name": "Mixed Open 200 Meter Medley", "heats": 1},
    ]


def test_build_event_list_no_meet_course_omits_unit():
    events = _csv_to_rows(
        "Event_ptr,Event_no,Event_gender,Low_age,High_Age,Event_dist,Event_stroke\n"
        '10,1,"F","11","12","100","C"\n'
    )
    entries = _csv_to_rows("Event_ptr,Pre_heat\n10,3\n")
    lst = build_event_list(events, entries, [], None)
    assert lst == [{"event": 1, "name": "Girls 11-12 100 Breaststroke", "heats": 1}]


def test_write_dolphin_events_csv(tmp_path):
    p = tmp_path / "ev.csv"
    n = write_dolphin_events_csv(p, [
        {"event": 1, "name": "Boys 50 Free", "heats": 2},
        {"event": 2, "name": "Girls 100 Back", "heats": 1},
    ])
    assert n == 2
    assert p.read_text(encoding="utf-8").splitlines() == ["1,Boys 50 Free,2", "2,Girls 100 Back,1"]
