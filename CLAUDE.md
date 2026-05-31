# MakoSync — Claude Code guidance

See `~/.claude/CLAUDE.md` for global rules and the ship-verb canon in
`~/workspace/claude-config/ship-vocabulary.md`. This repo lives at
`montesanoclub/makosync` (a shared remote) and is the meet-PC companion to
makosmeets. This file is MakoSync-specific.

## What it is

A Windows desktop companion app for swim-meet operators. Two modes — Dolphin and
Manager (the Meet Manager PC's combined pull-`.do3` + push-official-`.mdb` mode,
each on its own cadence) — plus the Dolphin→Meet Manager relay. Python +
PyInstaller, packaged as an Inno Setup `.exe` installer. Users **download and
install** it; it checks GitHub Releases on startup and prompts to update. Full
domain context lives in the makosmeets repo at `docs/makosync.md`.

## Ship path — what the verbs mean here

There is **no server** — the consumer is a desktop binary, so "ship" means cut a
**release**, not a deploy.

- **commit** — local checkpoint, freely. **push** — to the shared
  `montesanoclub/makosync` remote, on request (a collaborator may pull).
- **release** (the user-facing act) — bump the version + write
  `build/release_notes_vX.Y.Z.md`, then `git tag vX.Y.Z && git push --tags`.
  `.github/workflows/release.yml` runs on a **Windows runner**: tests → builds
  `MakoSync-Setup-X.Y.Z.exe` (Inno Setup) → publishes the GitHub Release. Local
  build (Windows only): `build\build_exe.ps1` (run `build\fetch_mdbtools.ps1`
  first). PyInstaller is platform-native — never try to build the release on
  Linux/Mac.
- **deploy** — n/a (no hosted service).

Users only get the change after the Release publishes AND they update — so a
release is outward-facing and high-stakes for meet operators. Only release when
Kyle asks.
