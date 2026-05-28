## MakosDolphinSync v0.1.0

Watches a CTS Dolphin output folder, parses each new `.do3`/`.do4` heat file, and pushes the result to the makosmeets ingest endpoint.

### Install

1. Download **MakosDolphinSync-Setup-0.1.0.exe** below.
2. Run it. It installs per-user — **no admin password needed**.
3. Optional checkboxes during install: desktop shortcut, and "start automatically when I log in."

### ⚠️ First-launch Windows warning (expected)

Because this installer isn't code-signed yet, Windows SmartScreen shows a blue **"Windows protected your PC"** dialog the first time you run it. This is normal:

- Click **More info**
- Click **Run anyway**

Windows remembers your choice — you'll only see it once per machine.

### Using it

Open the app, set the **Dolphin folder** and the **Ingest URL**, then click **Start**. The token field is optional.
