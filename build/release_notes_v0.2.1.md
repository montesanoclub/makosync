## MakoSync v0.2.1

Maintenance update for the dual-mode (Dolphin / Meet Manager) app.

### Fixes since v0.2.0

- **No more console flashes.** `mdb-export` now runs hidden, so no console window
  pops up when Meet Manager mode reads the database — it was flashing on every
  read, including each ~12s poll during a meet.
- **Settings persist.** The Ingest URL, token, and folder/`.mdb`/CSV paths now save
  as you type (and on close) and come back on next launch — no more re-entering
  them after a restart.

### Install

1. Download **MakoSync-Setup-0.2.1.exe** below.
2. Run it — per-user, **no admin needed**.
3. Optional: desktop shortcut, "start automatically when I log in."

### ⚠️ First-launch Windows warning (expected)

Unsigned installer, so SmartScreen shows **"Windows protected your PC"** the first
time: click **More info → Run anyway** (remembered per machine).
