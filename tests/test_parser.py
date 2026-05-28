"""Parser tests — against the real samples in ../samples/ and a few synthetic edge cases."""

from __future__ import annotations

from pathlib import Path

import pytest

from dolphinsync.parser import (
    _final_from_timers, _to_secs, format_seconds,
    parse_csv_text, parse_do3_text, parse_do4_text, parse_file,
)

SAMPLES = Path(__file__).parent.parent / "samples"


def test_to_secs_blank_zero_junk():
    assert _to_secs("") is None
    assert _to_secs("  ") is None
    assert _to_secs("0") is None
    assert _to_secs("0.00") is None
    assert _to_secs("junk") is None
    assert _to_secs("12.34") == 12.34


def test_final_from_timers_rules():
    assert _final_from_timers([None, None, None]) is None
    assert _final_from_timers([12.34, None, None]) == 12.34
    assert _final_from_timers([10.0, 12.0, None]) == 11.0     # average of 2
    assert _final_from_timers([10.0, 11.0, 100.0]) == 11.0    # median of 3 (outlier rejected)


def test_format_seconds_subminute_and_minute():
    assert format_seconds(41.9) == "41.90"
    assert format_seconds(95.32) == "1:35.32"
    assert format_seconds(60.0) == "1:00.00"


# ---------- .do4 ----------

def test_do4_real_sample_001_lane5_only():
    """004-000-001A-0001.do4 — single timer 8.13 in lane 5, all other lanes empty."""
    h = parse_file(SAMPLES / "004-000-001A-0001.do4")
    assert h is not None
    assert h.format == "do4"
    assert h.dataset == "004"
    assert h.race_id == "0001"
    assert h.round == "F"
    timed = h.timed_lanes
    assert len(timed) == 1 and timed[0].lane == 5 and timed[0].time == "8.13"


def test_do4_real_sample_010_lane5_2_11():
    h = parse_file(SAMPLES / "004-000-001A-0010.do4")
    assert h is not None and h.race_id == "0010"
    timed = h.timed_lanes
    assert len(timed) == 1 and timed[0].lane == 5 and timed[0].time == "2.11"


def test_do4_synthetic_three_timers_uses_median():
    text = ";1;1;All\nLane1;10.00;11.00;100.00\n" + "\n".join(f"Lane{i};0;0;0" for i in range(2, 11)) + "\nDEADBEEFCAFEBABE\n"
    h = parse_do4_text(text, "008-000-001A-0050.do4")
    assert h is not None
    lane1 = next(ln for ln in h.lanes if ln.lane == 1)
    assert lane1.time == "11.00"  # median of 10/11/100


def test_do4_synthetic_minute_time():
    text = ";2;1;F\nLane1;75.50;;\n" + "\n".join(f"Lane{i};0;0;0" for i in range(2, 11)) + "\nDEAD\n"
    h = parse_do4_text(text, "008-000-002F-0099.do4")
    lane1 = next(ln for ln in h.lanes if ln.lane == 1)
    assert lane1.time == "1:15.50"


# ---------- .do3 ----------

def test_do3_real_sample_001():
    """004-000-00F0001.do3 — same lane 5 / 8.13 as the .do4 twin, different format."""
    h = parse_file(SAMPLES / "004-000-00F0001.do3")
    assert h is not None
    assert h.format == "do3"
    assert h.dataset == "004"
    assert h.race_id == "0001"
    timed = h.timed_lanes
    assert len(timed) == 1 and timed[0].lane == 5 and timed[0].time == "8.13"


def test_do3_real_sample_010():
    h = parse_file(SAMPLES / "004-000-00F0010.do3")
    assert h is not None and h.race_id == "0010"
    timed = h.timed_lanes
    assert len(timed) == 1 and timed[0].lane == 5 and timed[0].time == "2.11"


def test_do3_synthetic_two_timers_average():
    text = "0;0;1;A\n1;10.00;12.00;\n" + "\n".join(f"{i};;;" for i in range(2, 11)) + "\nDEADBEEF\n"
    h = parse_do3_text(text, "008-000-00F0050.do3")
    assert h is not None
    lane1 = next(ln for ln in h.lanes if ln.lane == 1)
    assert lane1.time == "11.00"  # avg of 2


# ---------- .csv ----------

def test_csv_real_sample_001():
    h = parse_file(SAMPLES / "004_Event__Heat_1_Race_1_5_26_2026_14_39.csv")
    assert h is not None and h.format == "csv"
    timed = h.timed_lanes
    assert len(timed) == 1 and timed[0].lane == 5 and timed[0].time == "8.13"


# ---------- robustness ----------

@pytest.mark.parametrize("ext", ["do3", "do4"])
def test_empty_file_returns_none(ext, tmp_path):
    p = tmp_path / f"x.{ext}"
    p.write_text("", encoding="cp1252")
    assert parse_file(p) is None


def test_unknown_extension_returns_none(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text(";1;1;All\nLane1;1.0;;\nDEAD\n", encoding="cp1252")
    assert parse_file(p) is None
