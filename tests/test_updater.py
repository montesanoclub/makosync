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


# ---- installer asset + download ------------------------------------------

def test_installer_asset_picks_setup_exe():
    data = {"assets": [
        {"name": "ingest-contract.md", "browser_download_url": "u0"},
        {"name": "MakoSync-Setup-0.3.0.exe", "browser_download_url": "u1"},
    ]}
    assert updater._installer_asset(data) == ("u1", "MakoSync-Setup-0.3.0.exe")


def test_installer_asset_none_when_no_setup_exe():
    assert updater._installer_asset({"assets": [{"name": "notes.txt", "browser_download_url": "u"}]}) == (None, "")
    assert updater._installer_asset({}) == (None, "")


def test_check_for_update_surfaces_asset(monkeypatch):
    payload = {
        "tag_name": "v0.3.0", "html_url": "https://example/r",
        "assets": [{"name": "MakoSync-Setup-0.3.0.exe", "browser_download_url": "https://dl/exe"}],
    }
    monkeypatch.setattr(updater, "__version__", "0.2.2")
    monkeypatch.setattr(updater.request, "urlopen", _fake_urlopen(payload))
    info = updater.check_for_update()
    assert info.available and info.asset_url == "https://dl/exe"
    assert info.asset_name == "MakoSync-Setup-0.3.0.exe"


class _FakeDownloadResp:
    def __init__(self, data: bytes):
        self._data, self._pos = data, 0
        self.headers = {"Content-Length": str(len(data))}

    def read(self, n=-1):
        chunk = self._data[self._pos:self._pos + (n if n and n > 0 else len(self._data))]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_download_streams_to_file(tmp_path, monkeypatch):
    data = b"MZ" + b"x" * 5000  # pretend installer bytes
    monkeypatch.setattr(updater.request, "urlopen", lambda req, timeout=60.0: _FakeDownloadResp(data))
    seen = []
    dest = tmp_path / "sub" / "MakoSync-Setup.exe"  # parent doesn't exist yet
    out = updater.download("http://x/file.exe", dest, progress=lambda d, t: seen.append((d, t)))
    assert out == dest and dest.read_bytes() == data
    assert seen and seen[-1] == (len(data), len(data))
