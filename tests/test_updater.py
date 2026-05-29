"""Manual update-check logic: version compare + GitHub response parsing."""

from __future__ import annotations

import json

from makosync import updater


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._b = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(payload: dict):
    def _f(req, timeout=8.0):
        return _FakeResp(payload)
    return _f


def test_version_tuple_is_numeric():
    # 0.1.10 must sort above 0.1.9 (tuple compare, not string).
    assert updater._version_tuple("v0.1.10") > updater._version_tuple("v0.1.9")
    assert updater._version_tuple("0.1.1") == (0, 1, 1)
    assert updater._version_tuple("") == (0,)


def test_update_available(monkeypatch):
    monkeypatch.setattr(updater, "__version__", "0.1.1")
    monkeypatch.setattr(updater.request, "urlopen",
                        _fake_urlopen({"tag_name": "v0.2.0", "html_url": "https://example/r"}))
    info = updater.check_for_update()
    assert info.available is True
    assert info.current == "0.1.1"
    assert info.latest == "0.2.0"
    assert info.release_url == "https://example/r"


def test_up_to_date(monkeypatch):
    monkeypatch.setattr(updater, "__version__", "0.1.1")
    monkeypatch.setattr(updater.request, "urlopen", _fake_urlopen({"tag_name": "v0.1.1"}))
    assert updater.check_for_update().available is False


def test_remote_older_is_not_an_update(monkeypatch):
    monkeypatch.setattr(updater, "__version__", "0.2.0")
    monkeypatch.setattr(updater.request, "urlopen", _fake_urlopen({"tag_name": "v0.1.9"}))
    assert updater.check_for_update().available is False
