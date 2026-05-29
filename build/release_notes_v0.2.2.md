## MakoSync v0.2.2

Fixes the Dolphin event-list sync so it actually imports into the Dolphin software.

### What's new since v0.2.1

- **Dolphin events sync now works.** "Push events → Dolphin" (Manager) builds the
  Dolphin Events CSV **directly from the Meet Manager `.mdb`** — no more running
  the `.scb` → events2dolphin dance. The output is verified byte-for-byte against a
  real events2dolphin file (`Event#,NAME,heats,1,A`), so the Dolphin software
  imports it correctly. "Load events from MM" (Dolphin) writes that CSV verbatim.
- Each Push reflects the **current** meet's seeded events; both sides show the
  server's "last updated" time.

> The makosmeets `dolphin-events` relay endpoint must be deployed for Push/Load to
> reach the server (see `docs/dolphin-events-relay.md`).

### Install

1. Download **MakoSync-Setup-0.2.2.exe** below — per-user, **no admin needed**.
2. Unsigned, so SmartScreen shows **"Windows protected your PC"** on first launch:
   **More info → Run anyway** (remembered per machine).
