## MakoSync v0.2.0

Pushes live swim results to makosmeets. **Renamed from DolphinSync** and now
**dual-mode** — pick **Dolphin** or **Meet Manager** from the launcher when it
opens.

### What's new since v0.1.2

- **Renamed to MakoSync** (new Mako shark icon). Old "Makos DolphinSync" installs
  can be uninstalled; this installs fresh per-user.
- **Two modes, one app:**
  - **Dolphin** — watches a CTS Dolphin folder and pushes unofficial times (as before).
  - **Meet Manager** — reads the Hy-Tek Meet Manager `.mdb` on the scoring PC and
    pushes the reconciled **official** results (finish places included). It reads
    a lock-safe copy via bundled mdbtools, so it works while Meet Manager has the
    file open, and needs nothing installed on the scoring PC.

### Install

1. Download **MakoSync-Setup-0.2.0.exe** below.
2. Run it. It installs per-user — **no admin password needed**.
3. Optional checkboxes during install: desktop shortcut, and "start automatically when I log in."

### ⚠️ First-launch Windows warning (expected)

This installer isn't code-signed, so Windows SmartScreen shows a blue **"Windows protected your PC"** dialog the first time you run it:

- Click **More info**
- Click **Run anyway**

Windows remembers your choice — you'll only see it once per machine.

### Using it

Open the app and pick a mode. Set the **Ingest URL** (your makosmeets base URL)
and, for Dolphin the **folder** / for Meet Manager the **.mdb path**, then click
**Start**. The token field is optional.
