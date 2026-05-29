"""Tests for the Meet Manager import relay (receiver) — makosync.mm_import."""

from __future__ import annotations

from makosync.client import IngestResult
from makosync.mm_import import MmImportConfig, MmImportWatcher

DO3_BYTES = b"0;0;1;A\n1;4.03;;\n2;2.94;;\n368D4CC7A32037D4\n"


class FakeClient:
    """Stand-in for IngestClient: canned pending list + key->bytes downloads."""

    def __init__(self, files, blobs, pending_ok=True):
        self.files = files
        self.blobs = blobs
        self.pending_ok = pending_ok
        self.downloads: list[str] = []

    def fetch_pending(self):
        if not self.pending_ok:
            return IngestResult(ok=False, status=503, detail="down"), []
        return IngestResult(ok=True, status=200, detail="ok"), [dict(f) for f in self.files]

    def download_file(self, key):
        self.downloads.append(key)
        if key in self.blobs:
            return IngestResult(ok=True, status=200, detail="ok"), self.blobs[key]
        return IngestResult(ok=False, status=404, detail="not found"), b""


def _entry(meet="015", event=22, heat=2, race="0005"):
    return {
        "race_id": race,
        "meet_id": meet,
        "event": event,
        "heat": heat,
        "src_name": f"{meet}-000-00F{race}.do3",
        "out_name": f"{meet}-000-E{event:02d}_H{heat:02d}.do3",
        "key": f"dolphin-raw/2026-05-29/{meet}-000-00F{race}.do3",
    }


def _watcher(tmp_path, files, blobs, **kw):
    toasts: list[tuple[str, str]] = []
    cfg = MmImportConfig(base_url="http://x", import_dir=tmp_path, token="t",
                         poll_interval=2.0, notify=kw.get("notify", True))
    client = FakeClient(files, blobs, pending_ok=kw.get("pending_ok", True))
    w = MmImportWatcher(cfg, client=client,
                        notifier=lambda t, m: toasts.append((t, m)) or True)
    return w, client, toasts


def test_pull_writes_renamed_file_and_toasts(tmp_path):
    e = _entry()
    w, client, toasts = _watcher(tmp_path, [e], {e["key"]: DO3_BYTES})
    w._cycle()

    dest = tmp_path / "015-000-E22_H02.do3"
    assert dest.exists()
    assert dest.read_bytes() == DO3_BYTES          # bytes are relayed verbatim
    assert client.downloads == [e["key"]]
    assert w.stats.sent_file == 1
    # Exact operator-facing wording Kyle specified.
    assert toasts == [("MakoSync", "Event 22 Heat 2 dolphin results pulled from makos meets")]


def test_dedup_only_pulls_once(tmp_path):
    e = _entry()
    w, client, toasts = _watcher(tmp_path, [e], {e["key"]: DO3_BYTES})
    w._cycle()
    w._cycle()  # same pending list again
    assert len(client.downloads) == 1
    assert len(toasts) == 1
    assert w.stats.sent_file == 1


def test_skips_file_already_in_folder(tmp_path):
    e = _entry()
    # Operator already has it (or a prior session wrote it) — don't re-pull/toast.
    (tmp_path / e["out_name"]).write_bytes(b"preexisting")
    w, client, toasts = _watcher(tmp_path, [e], {e["key"]: DO3_BYTES})
    w._cycle()
    assert client.downloads == []          # never downloaded
    assert toasts == []
    assert (tmp_path / e["out_name"]).read_bytes() == b"preexisting"  # untouched


def test_meet_id_preserved_in_out_name(tmp_path):
    e = _entry(meet="014", event=3, heat=1, race="0003")
    w, _, _ = _watcher(tmp_path, [e], {e["key"]: DO3_BYTES})
    w._cycle()
    # First field (meet id) preserved — Meet Manager only imports files matching it.
    assert (tmp_path / "014-000-E03_H01.do3").exists()


def test_download_failure_retries_then_gives_up(tmp_path):
    e = _entry()
    w, client, toasts = _watcher(tmp_path, [e], {})  # blob missing -> 404
    for _ in range(10):
        w._cycle()
    assert not (tmp_path / e["out_name"]).exists()
    assert toasts == []
    assert w.stats.errors > 0
    # Capped at MAX_ATTEMPTS, then gives up (stops re-downloading forever).
    from makosync.mm_import import MAX_ATTEMPTS
    assert len(client.downloads) == MAX_ATTEMPTS


def test_no_part_file_left_behind(tmp_path):
    e = _entry()
    w, _, _ = _watcher(tmp_path, [e], {e["key"]: DO3_BYTES})
    w._cycle()
    assert list(tmp_path.glob("*.part")) == []   # atomic write cleaned up


def test_pending_error_is_non_fatal(tmp_path):
    e = _entry()
    w, _, toasts = _watcher(tmp_path, [e], {e["key"]: DO3_BYTES}, pending_ok=False)
    w._cycle()  # must not raise
    assert toasts == []
    assert w.stats.errors > 0


def test_creates_missing_import_dir(tmp_path):
    # Regression: --once drives _cycle() without start(), and a folder can be
    # deleted mid-meet. The write must mkdir its parent, not fail.
    target = tmp_path / "does" / "not" / "exist"
    e = _entry()
    w, _, _ = _watcher(target, [e], {e["key"]: DO3_BYTES})
    w._cycle()
    assert (target / e["out_name"]).read_bytes() == DO3_BYTES


def test_notify_off_writes_but_no_toast(tmp_path):
    e = _entry()
    w, _, toasts = _watcher(tmp_path, [e], {e["key"]: DO3_BYTES}, notify=False)
    w._cycle()
    assert (tmp_path / e["out_name"]).exists()
    assert toasts == []
