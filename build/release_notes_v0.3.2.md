## MakoSync v0.3.2

Folds **MM Import into Manager** so the Meet Manager PC has one mode that does
everything, and **pre-fills the makosmeets connection** so a fresh install is
ready to go.

### What's new since v0.3.1

- **One Manager mode for the scoring PC.** The separate "MM Import" button is
  gone — **Manager** now does both halves of the Meet Manager PC's job at once,
  each on its own cadence:
  - **pulls** the relayed Dolphin `.do3` files into the folder Meet Manager
    imports from (toast per heat) — default every **2 s**;
  - **reads** the `.mdb` and **pushes** the reconciled official results — places,
    DQs — default every **12 s**.
  - Both intervals are adjustable in the Manager view. The import folder defaults
    to the folder your `.mdb` lives in, and the "Push events → Dolphin" button
    stays put. Existing installs that were set to "MM Import" move to Manager
    automatically.
- **Prebaked connection.** New installs come pre-filled with the makosmeets URL
  and the shared dolphin token — volunteers don't type anything. Still editable
  in the app (and remembered) if a meet needs different.

> Headless note: `--mode manager` runs both loops (`--no-import` for push-only);
> `--mode mm-import` remains as a pull-only mode.

### Install

1. Download **MakoSync-Setup-0.3.2.exe** below — per-user, **no admin needed**.
2. Unsigned, so SmartScreen shows **"Windows protected your PC"** on first launch:
   **More info → Run anyway** (remembered per machine).

> If you're already on v0.3.1, MakoSync can update itself — **Check for updates**.
