"""Tests for the relay client methods — makosync.client fetch_pending."""

from __future__ import annotations

from makosync.client import IngestClient, IngestResult


def test_fetch_pending_parses_files(monkeypatch):
    c = IngestClient("https://makosmeets.com", "tok")
    body = (
        '{"ok":true,"count":1,"files":['
        '{"out_name":"015-000-E22_H02.do3",'
        '"key":"dolphin-raw/2026-05-29/015-000-00F0005.do3",'
        '"event":22,"heat":2,"race_id":"0005","meet_id":"015"}]}'
    )
    monkeypatch.setattr(c, "_send_with_retry",
                        lambda *a, **k: IngestResult(ok=True, status=200, detail="ok", body=body))
    res, files = c.fetch_pending()
    assert res.ok
    assert len(files) == 1
    assert files[0]["event"] == 22
    assert files[0]["out_name"] == "015-000-E22_H02.do3"


def test_fetch_pending_error_returns_empty(monkeypatch):
    c = IngestClient("https://makosmeets.com", "tok")
    monkeypatch.setattr(c, "_send_with_retry",
                        lambda *a, **k: IngestResult(ok=False, status=503, detail="down"))
    res, files = c.fetch_pending()
    assert not res.ok
    assert files == []


def test_fetch_pending_bad_json_is_handled(monkeypatch):
    c = IngestClient("https://makosmeets.com", "tok")
    monkeypatch.setattr(c, "_send_with_retry",
                        lambda *a, **k: IngestResult(ok=True, status=200, detail="ok", body="not json"))
    res, files = c.fetch_pending()
    assert not res.ok
    assert files == []
