# MakoSync

The Windows companion app for **[makosmeets.com](https://www.makosmeets.com)**.
It runs on the on-deck meet PCs and moves live swim results between the timing
hardware, the scoring software, and the website — so unofficial times hit the
pool-deck `/tv` board the instant the buttons fire, official results follow from
Meet Manager, and Dolphin files reach the scoring PC without a USB stick.

> Server side (the endpoints this app talks to) lives in the makosmeets repo;
> see its `docs/makosync.md` for the full cross-repo picture.

## Two modes

Pick one at launch (GUI) or with `--mode`. URL + token are shared across modes.

| Mode | Runs on | What it does |
|---|---|---|
| **Dolphin** | the CTS Dolphin PC | Watches the Dolphin output folder, parses each `.do3/.do4/.csv` heat, POSTs **unofficial** times to makosmeets (feeds `/tv`), and archives the raw file. |
| **Manager** | the Hy-Tek Meet Manager PC | Does both halves of the scoring PC's job at once, each on its own cadence: **pulls** the Dolphin `.do3` files (relayed via makosmeets, renamed `<original>_E<ev>_H<ht>.do3` — race number preserved) into the folder Meet Manager imports from — toast per heat (~2 s); and **reads** the live `.mdb` (bundled mdbtools) to POST the reconciled **official** results — places, DQs, and the per-event `scored` flag that gates whether they go live (~12 s). Also pushes the seeded event list to the Dolphin machine. See [`docs/mm-import-relay.md`](docs/mm-import-relay.md). |

Both meet PCs only make **outbound HTTPS** to makosmeets — no LAN/firewall config
between them.

## Run it

GUI (what the meet volunteer uses): launch **MakoSync**, pick the mode, fill the
folder/URL/token, **Start**. Settings persist to `%APPDATA%\MakoSync\config.json`.

Headless (testing / automation):

```
makosync --headless --mode dolphin --folder "C:\CTSDolphin\output" --url https://www.makosmeets.com --token <TOKEN>
makosync --headless --mode manager --mdb-path "C:\swmeets8\meet.mdb" --url https://www.makosmeets.com --token <TOKEN>
# manager pulls .do3 into the .mdb's folder by default; --import-dir overrides, --no-import disables the pull.
# mm-import remains as a pull-only mode (the import half of manager, for a box that shouldn't push official results):
makosync --headless --mode mm-import --import-dir "C:\swmeets8" --url https://www.makosmeets.com --token <TOKEN>
```

Add `--once` to run a single cycle and exit (smoke test).

## Updates

MakoSync checks GitHub Releases **on startup** (and via a manual button) and
prompts if a newer version exists.

## Develop

```
uv run pytest                                  # test suite
uv run makosync --headless --mode dolphin ...  # run from source
```

`tkinter` ships with Windows Python; on Linux install `python3-tk` for the GUI
(headless needs no Tk). The app is otherwise stdlib-only.

## Build & release (Windows)

PyInstaller is platform-native — build on **Windows**. Push a `v*` tag and
`.github/workflows/release.yml` does it on a Windows runner: runs the tests,
builds `MakoSync-Setup-X.Y.Z.exe` (Inno Setup), and publishes the release. To
build locally: `build\build_exe.ps1` (and `build\fetch_mdbtools.ps1` first for
Manager mode). Unsigned → SmartScreen "More info → Run anyway" on first launch.

## Docs

- [`docs/mm-import-relay.md`](docs/mm-import-relay.md) — the Dolphin → Meet Manager relay (MM Import).
- [`docs/dolphin-events-relay.md`](docs/dolphin-events-relay.md) — the seeded-event-list relay.
- [`docs/ingest-contract.md`](docs/ingest-contract.md) — the results ingest payload contract.
- [`docs/dolphin-latency.md`](docs/dolphin-latency.md) — **known issue:** why Dolphin sends feel slow (poll floor + double-stability) and how flaky wifi stalls the feed (blocking serial retry).
