## MakoSync v0.3.1

Adds **one-click self-update** — finishing the auto-update story v0.3.0 started.

### What's new since v0.3.0

- **Self-update from inside the app.** When the startup check (or the manual
  **Check for updates** button) finds a newer release, MakoSync now offers to
  **download and install it for you**: it pulls `MakoSync-Setup-X.Y.Z.exe`
  straight from the GitHub Release, runs it silently, and **relaunches on the new
  version** — no browser, no manual download, no hunting for the installer.
  - The download streams over a raw socket (not a browser), so the installer
    isn't tagged Mark-of-the-Web and the silent per-user install generally runs
    without a SmartScreen prompt.
  - **Won't update mid-sync.** If a Dolphin/Meet Manager/MM Import sync is
    running, it asks you to stop it first — updates only happen while idle (and
    the check itself still only runs at startup, never during a meet).
  - If a release has no installer asset, it falls back to opening the download
    page (the old v0.3.0 behavior).

### Install

1. Download **MakoSync-Setup-0.3.1.exe** below — per-user, **no admin needed**.
2. Unsigned, so SmartScreen shows **"Windows protected your PC"** on first launch:
   **More info → Run anyway** (remembered per machine).

> From 0.3.1 onward you won't need to do this by hand — the app updates itself.
