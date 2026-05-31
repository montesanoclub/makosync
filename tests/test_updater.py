"""Manual update-check logic: version compare + GitHub response parsing."""

from __future__ import annotations

import json
import os

import pytest

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
        {"name": "ingest-contract.md", "browser_download_url": "u0", "size": 10},
        {"name": "MakoSync-Setup-0.3.0.exe", "browser_download_url": "u1", "size": 16360000},
    ]}
    assert updater._installer_asset(data) == ("u1", "MakoSync-Setup-0.3.0.exe", 16360000)


def test_installer_asset_none_when_no_setup_exe():
    assert updater._installer_asset({"assets": [{"name": "notes.txt", "browser_download_url": "u"}]}) == (None, "", 0)
    assert updater._installer_asset({}) == (None, "", 0)


def test_check_for_update_surfaces_asset(monkeypatch):
    payload = {
        "tag_name": "v0.3.0", "html_url": "https://example/r",
        "assets": [{"name": "MakoSync-Setup-0.3.0.exe", "browser_download_url": "https://dl/exe", "size": 999}],
    }
    monkeypatch.setattr(updater, "__version__", "0.2.2")
    monkeypatch.setattr(updater.request, "urlopen", _fake_urlopen(payload))
    info = updater.check_for_update()
    assert info.available and info.asset_url == "https://dl/exe"
    assert info.asset_name == "MakoSync-Setup-0.3.0.exe"
    assert info.asset_size == 999


class _FakeDownloadResp:
    def __init__(self, data: bytes, content_length: int | None = None):
        self._data, self._pos = data, 0
        # Content-Length may be overstated to simulate a truncated transfer.
        cl = len(data) if content_length is None else content_length
        self.headers = {"Content-Length": str(cl)}

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


def test_download_rejects_truncated_by_content_length(tmp_path, monkeypatch):
    data = b"MZ" + b"x" * 50  # server claims 9999 but only 52 arrive
    monkeypatch.setattr(updater.request, "urlopen",
                        lambda req, timeout=60.0: _FakeDownloadResp(data, content_length=9999))
    dest = tmp_path / "MakoSync-Setup.exe"
    try:
        updater.download("http://x/file.exe", dest)
        assert False, "expected IOError on truncated download"
    except IOError:
        pass
    assert not dest.exists()  # partial file removed, never handed to the installer


def test_download_rejects_size_mismatch_with_expected(tmp_path, monkeypatch):
    data = b"MZ" + b"x" * 100  # full per Content-Length, but expected_size disagrees
    monkeypatch.setattr(updater.request, "urlopen", lambda req, timeout=60.0: _FakeDownloadResp(data))
    dest = tmp_path / "MakoSync-Setup.exe"
    try:
        updater.download("http://x/file.exe", dest, expected_size=999999)
        assert False, "expected IOError on expected_size mismatch"
    except IOError:
        pass
    assert not dest.exists()


def test_download_refuses_when_size_unknown(tmp_path, monkeypatch):
    # No Content-Length AND no expected_size -> we can't verify wholeness -> refuse,
    # so a silently-truncated installer can never reach the self-updater.
    data = b"MZ" + b"x" * 100
    monkeypatch.setattr(updater.request, "urlopen",
                        lambda req, timeout=60.0: _FakeDownloadResp(data, content_length=0))
    dest = tmp_path / "MakoSync-Setup.exe"
    try:
        updater.download("http://x/file.exe", dest, expected_size=0)
        assert False, "expected IOError when no size is known"
    except IOError:
        pass
    assert not dest.exists()


def test_build_update_script_waits_installs_relaunches():
    s = updater._build_update_script(r"C:\Temp\MakoSync-Setup-9.9.9.exe",
                                     r"C:\Users\Dolphin\AppData\Local\Programs\MakoSync\MakoSync.exe",
                                     pid=4242, image_name="MakoSync.exe")
    # waits on our exact PID + process name (recycled-PID guard), with a 5-min cap
    assert "$targetPid = 4242" in s
    assert "Get-Process -Id $targetPid" in s
    assert "$procName = 'MakoSync'" in s
    assert "(Get-Date).AddMinutes(5)" in s and "while ((Get-Date) -lt $deadline)" in s
    assert "$pid =" not in s                                        # never clobber PowerShell's automatic $pid
    assert r"$installer = 'C:\Temp\MakoSync-Setup-9.9.9.exe'" in s
    assert r"$target = 'C:\Users\Dolphin\AppData\Local\Programs\MakoSync\MakoSync.exe'" in s
    # install only if present; settle for AV; relaunch only if the exe exists afterwards
    assert "if (Test-Path -LiteralPath $installer) {" in s
    assert "Start-Process -FilePath $installer -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -Wait" in s
    assert "Start-Sleep -Seconds 2" in s
    assert "if (Test-Path -LiteralPath $target) { Start-Process -FilePath $target }" in s
    assert "Remove-Item -LiteralPath $MyInvocation.MyCommand.Path" in s   # self-cleans


def test_build_update_script_escapes_single_quotes():
    s = updater._build_update_script(r"C:\T'mp\Set'up.exe", r"C:\a'b\Mako.exe", pid=1, image_name="Mako.exe")
    assert "$installer = 'C:\\T''mp\\Set''up.exe'" in s   # ' doubled for a PS literal
    assert "$target = 'C:\\a''b\\Mako.exe'" in s


def test_launch_update_rejects_empty_target_name():
    with pytest.raises(ValueError):
        updater.launch_update("MakoSync-Setup.exe", "", pid=1)   # no basename -> can't wait on it


@pytest.mark.skipif(os.name != "nt", reason="launch_update writes/launches a Windows helper")
def test_launch_update_writes_script_and_spawns_detached(tmp_path, monkeypatch):
    calls = {}

    def fake_popen(cmd, **kw):
        calls["cmd"] = cmd
        calls["flags"] = kw.get("creationflags")
        class _P:  # noqa: D401 - minimal Popen stand-in
            pass
        return _P()

    monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)
    script = updater.launch_update(tmp_path / "MakoSync-Setup-9.9.9.exe",
                                   tmp_path / "MakoSync.exe", pid=4242, script_dir=tmp_path)
    assert script.exists() and script.name == "makosync_update.ps1"
    body = script.read_text(encoding="utf-8-sig")
    assert "$targetPid = 4242" in body
    assert calls["cmd"][0] == "powershell" and "-File" in calls["cmd"] and calls["cmd"][-1] == str(script)
    assert calls["flags"] & updater._CREATE_BREAKAWAY_FROM_JOB  # detached from our job
