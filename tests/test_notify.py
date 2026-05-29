"""Tests for the Windows toast helper — makosync.notify."""

from __future__ import annotations

from makosync import notify


def test_build_script_embeds_title_and_message():
    script = notify.build_script("MakoSync", "Event 22 Heat 2 pulled")
    assert "ToastNotificationManager" in script
    assert "'MakoSync'" in script
    assert "'Event 22 Heat 2 pulled'" in script
    # AppId is supplied to CreateToastNotifier.
    assert "CreateToastNotifier('MakoSync')" in script


def test_build_script_escapes_single_quotes():
    # A stray apostrophe must be doubled, not break out of the PS literal.
    script = notify.build_script("MakoSync", "Lily's heat")
    assert "'Lily''s heat'" in script


def test_notify_non_windows_is_safe(monkeypatch):
    # On macOS/Linux it must not shell out or raise — just returns False.
    monkeypatch.setattr(notify.sys, "platform", "darwin")
    assert notify.notify("t", "m") is False
