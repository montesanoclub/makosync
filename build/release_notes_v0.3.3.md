## MakoSync v0.3.3

Fixes in-app updating so it's reliable, and ends the "Failed to load Python DLL"
class of startup errors for good. **This is the release the 0.3.1/0.3.2
self-update should have been** — those left some PCs with a corrupt or stale
install when you clicked update.

> One-time step: get **onto** 0.3.3 by installing it by hand (download below) —
> the broken 0.3.1/0.3.2 updater can't be trusted to do it. From 0.3.3 on,
> **Check for updates** is safe and self-applies.

### What's fixed since v0.3.2

- **Reliable self-update.** Updating no longer races to replace a running file.
  A small detached helper now **waits for MakoSync to fully close**, then runs the
  installer, then relaunches — so the exe is never half-written. It also:
  - **verifies the download** is complete before running it (a truncated installer
    is deleted, never executed);
  - **only relaunches an exe that actually exists** afterward — and if an install
    ever fails, Windows Installer rolls back to the previous version and the app
    still comes back up (no more dead/stale installs);
  - won't update while a sync is running.
- **No more "Failed to load Python DLL python3xx.dll".** MakoSync now installs as
  a **folder** (the DLLs sit next to the exe) instead of a single file that
  unpacked to a temp folder on every launch. Nothing extracts to `%Temp%` for
  antivirus or a temp cleaner to break, and startup is faster.

### Install

1. Download **MakoSync-Setup-0.3.3.exe** below — per-user, **no admin needed**.
2. Unsigned, so SmartScreen shows **"Windows protected your PC"** on first launch:
   **More info → Run anyway** (remembered per machine).
