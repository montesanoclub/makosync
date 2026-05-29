"""Dolphin-events relay client: response parsing for push/fetch.

The server endpoints don't exist yet (spec in docs/dolphin-events-relay.md), so
these stub the HTTP layer and exercise payload-building + response-parsing only.
"""

from __future__ import annotations

import json

from makosync.client import DOLPHIN_EVENTS_PATH, IngestClient, IngestResult, _updated_at_from


def test_updated_at_from():
    assert _updated_at_from('{"updated_at":"2026-05-29T10:00:00Z"}') == "2026-05-29T10:00:00Z"
    assert _updated_at_from('{"count":3}') is None
    assert _updated_at_from("not json") is None
    assert _updated_at_from("") is None


def test_fetch_dolphin_events_parses(monkeypatch):
    c = IngestClient("https://makosmeets.com")
    body = '{"events":[{"event":1,"name":"E1","heats":2}],"updated_at":"T","count":1}'
    monkeypatch.setattr(c, "_send_with_retry",
                        lambda *a, **k: IngestResult(ok=True, status=200, body=body))
    res, events, updated_at = c.fetch_dolphin_events()
    assert res.ok
    assert events == [{"event": 1, "name": "E1", "heats": 2}]
    assert updated_at == "T"


def test_fetch_dolphin_events_empty(monkeypatch):
    c = IngestClient("https://makosmeets.com")
    monkeypatch.setattr(c, "_send_with_retry",
                        lambda *a, **k: IngestResult(ok=True, status=200, body='{"events":[],"updated_at":null}'))
    res, events, updated_at = c.fetch_dolphin_events()
    assert res.ok and events == [] and updated_at is None


def test_fetch_dolphin_events_error_passthrough(monkeypatch):
    c = IngestClient("https://makosmeets.com")
    monkeypatch.setattr(c, "_send_with_retry",
                        lambda *a, **k: IngestResult(ok=False, status=404, detail="not found"))
    res, events, updated_at = c.fetch_dolphin_events()
    assert not res.ok and events == [] and updated_at is None


def test_push_dolphin_events_builds_payload_and_parses(monkeypatch):
    c = IngestClient("https://makosmeets.com")
    seen = {}

    def fake(url, body, *, headers, method="POST"):
        seen.update(url=url, method=method, body=body, headers=headers)
        return IngestResult(ok=True, status=200, body='{"updated_at":"T2","count":1}')

    monkeypatch.setattr(c, "_send_with_retry", fake)
    res, updated_at = c.push_dolphin_events([{"event": 1, "name": "E1", "heats": 2}])
    assert res.ok and updated_at == "T2"
    assert seen["method"] == "POST"
    assert seen["url"] == "https://makosmeets.com" + DOLPHIN_EVENTS_PATH
    sent = json.loads(seen["body"])
    assert sent["count"] == 1 and sent["events"][0]["event"] == 1 and "captured_at" in sent


def test_push_dolphin_events_failure_no_updated_at(monkeypatch):
    c = IngestClient("https://makosmeets.com")
    monkeypatch.setattr(c, "_send_with_retry",
                        lambda *a, **k: IngestResult(ok=False, status=503, detail="down"))
    res, updated_at = c.push_dolphin_events([])
    assert not res.ok and updated_at is None
