## MakoSync v0.3.0

Adds **MM Import** — the scoring PC pulls Dolphin results in automatically — plus
a **startup update check**.

### What's new since v0.2.2

- **MM Import mode (third launcher button).** Runs on the Meet Manager PC and
  pulls the Dolphin `.do3` files the Dolphin PC already relays through makosmeets,
  renamed `<meetid>-000-E<ev>_H<ht>.do3` so you can tell which heat is which in
  Meet Manager's file picker. A **Windows toast** pops on each new heat —
  *"Event 22 Heat 2 dolphin results pulled from makos meets"* — your cue to Get
  Times. No more hunting for the right `00F####.do3`. Defaults its drop folder to
  the folder your Meet Manager `.mdb` lives in.
  - Event/heat come from the paired `.do4` (the `.do3` carries neither in its name
    or body); the meet id (first field) is preserved so MM only imports files for
    its loaded meet.
  - Both meet PCs stay outbound-only to makosmeets — no LAN/firewall config
    between them.
- **Startup update check.** On launch MakoSync quietly checks GitHub Releases and
  prompts only if a newer version exists (the manual "Check for updates" button
  stays). Silent when up to date or offline.

> Requires the makosmeets `/api/live-results/pending/` relay + raw-file download
> to be deployed (see `docs/mm-import-relay.md`).

### Install

1. Download **MakoSync-Setup-0.3.0.exe** below — per-user, **no admin needed**.
2. Unsigned, so SmartScreen shows **"Windows protected your PC"** on first launch:
   **More info → Run anyway** (remembered per machine).
