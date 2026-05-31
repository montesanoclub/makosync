## MakoSync v0.3.4

Installer polish: it now closes a running MakoSync for you, so updating never
throws the "couldn't close applications" / "DeleteFile failed; Access is denied"
errors — whether you run the installer by hand or the in-app updater applies it.

### What's changed since v0.3.3

- **Installer closes MakoSync automatically** before replacing files (a graceful
  close first so your URL/token/settings are saved, then a hard close as a
  backstop). No more "Setup was unable to automatically close all applications"
  or "Access is denied" when installing over a running copy — and no need to end
  the task in Task Manager first.

> On 0.3.3 already? Just hit **Check for updates** — MakoSync will pull this one
> itself (no manual install needed).

### Install

1. Download **MakoSync-Setup-0.3.4.exe** below — per-user, **no admin needed**.
2. Unsigned, so SmartScreen shows **"Windows protected your PC"** on first launch:
   **More info → Run anyway** (remembered per machine).
