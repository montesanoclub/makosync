## MakosDolphinSync v0.1.1

Watches a CTS Dolphin output folder, parses each new `.do3`/`.do4` heat file, and pushes the result to the makosmeets ingest endpoint.

### What's new since v0.1.0

- Targets the makosmeets **live-results** ingest endpoint — just enter your site's base URL (e.g. `https://makosmeets...`); the app appends the rest.
- Each heat's raw `.do` file is also archived as a forensic copy.
- The token field is shown in plaintext (it's optional).

### Install

1. Download **MakosDolphinSync-Setup-0.1.1.exe** below.
2. Run it. It installs per-user — **no admin password needed**.
3. Optional checkboxes during install: desktop shortcut, and "start automatically when I log in."

### ⚠️ First-launch Windows warning (expected)

This installer isn't code-signed, so Windows SmartScreen shows a blue **"Windows protected your PC"** dialog the first time you run it:

- Click **More info**
- Click **Run anyway**

Windows remembers your choice — you'll only see it once per machine.

### Using it

Open the app, set the **Dolphin folder** and the **Ingest URL** (your makosmeets base URL), then click **Start**. The token field is optional.
