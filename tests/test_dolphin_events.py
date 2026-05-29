"""Dolphin-events relay client: CSV push/fetch response parsing.

The server endpoint doesn't exist yet (spec in docs/dolphin-events-relay.md), so
these stub the HTTP layer and exercise payload-building + response-parsing only.
"""

from __future__ import annotations

import json

from makosync.client import DOLPHIN_EVENTS_PATH, IngestClient, IngestResult, _updated_at_from

CSV = "1,GIRLS 8&U 100 MEDLEY RELAY,1,1,A\r\n2,BOYS 8&U 100 MEDLEY RELAY,1,1,A\r\n"


def test_updated_at_from():
    assert _updated_at_from('{"updated_at":"2026-05-29T10:00:00Z"}') == "2026-05-29T10:00:00Z"
    assert _updated_at_from('{"lines":3}') is None
    assert _updated_at_from("not json") is None
    assert _updated_at_from("") is None


def test_push_dolphin_events_csv_builds_payload(monkeypatch):
    c = IngestClient("https://makosmeets.com")
    seen = {}

    def fake(url, body, *, headers, method="POST"):
        seen.update(url=url, method=method, body=body)
        return IngestResult(ok=True, status=200, body='{"updated_at":"T2","lines":2}')

    monkeypatch.setattr(c, "_send_with_retry", fake)
    res, updated_at = c.push_dolphin_events_csv(CSV, name="meet.mdb")
    assert res.ok and updated_at == "T2"
    assert seen["method"] == "POST"
    assert seen["url"] == "https://makosmeets.com" + DOLPHIN_EVENTS_PATH
    sent = json.loads(seen["body"])
    assert sent["csv"] == CSV and sent["name"] == "meet.mdb" and sent["lines"] == 2 and "captured_at" in sent


def test_push_dolphin_events_csv_failure_no_updated_at(monkeypatch):
    c = IngestClient("https://makosmeets.com")
    monkeypatch.setattr(c, "_send_with_retry",
                        lambda *a, **k: IngestResult(ok=False, status=503, detail="down"))
    res, updated_at = c.push_dolphin_events_csv(CSV)
    assert not res.ok and updated_at is None


def test_fetch_dolphin_events_csv_parses(monkeypatch):
    c = IngestClient("https://makosmeets.com")
    body = json.dumps({"csv": CSV, "name": "meet.mdb", "lines": 2, "updated_at": "T"})
    monkeypatch.setattr(c, "_send_with_retry",
                        lambda *a, **k: IngestResult(ok=True, status=200, body=body))
    res, csv_text, name, updated_at = c.fetch_dolphin_events_csv()
    assert res.ok and csv_text == CSV and name == "meet.mdb" and updated_at == "T"


def test_fetch_dolphin_events_csv_empty(monkeypatch):
    c = IngestClient("https://makosmeets.com")
    monkeypatch.setattr(c, "_send_with_retry",
                        lambda *a, **k: IngestResult(ok=True, status=200, body='{"csv":"","updated_at":null}'))
    res, csv_text, name, updated_at = c.fetch_dolphin_events_csv()
    assert res.ok and csv_text == "" and name == "" and updated_at is None


def test_fetch_dolphin_events_csv_error_passthrough(monkeypatch):
    c = IngestClient("https://makosmeets.com")
    monkeypatch.setattr(c, "_send_with_retry",
                        lambda *a, **k: IngestResult(ok=False, status=404, detail="not found"))
    res, csv_text, name, updated_at = c.fetch_dolphin_events_csv()
    assert not res.ok and csv_text == "" and updated_at is None
