# MM Import relay (Dolphin → Meet Manager, across two PCs)

How Dolphin timing results get **into Meet Manager** automatically when the
Dolphin software and Meet Manager run on **separate** computers, without the two
PCs talking directly — both only make outbound HTTPS to makosmeets.

```
Dolphin PC                         makosmeets (Cloudflare)            Meet Manager PC
──────────                         ───────────────────────            ───────────────
MakoSync (dolphin mode)            R2 dolphin-raw/<date>/             MakoSync (manager mode: pull half)
  watch C:\CTSDolphin\output  ─┐   <original-filename>          ┌─►  poll /pending every 2s
  POST raw .do3 + .do4 ────────┴─► (forensic sink) ─────────────┘    download new .do3
                                   GET /pending  (do3⨝do4 join)      write <meetid>-000-E##_H##.do3
                                   GET /ingest/file?key=             into MM's import folder
                                                                     → Windows toast → operator Get-Times
```

## Why a rename is needed

A Dolphin `.do3` carries **no event/heat** — the filename is
`<meetid>-000-00F<race>.do3` and the body header is literally `0;0`. The `.do4`
for the same race **does**: `<meetid>-<event>-<heat><round>-<race>.do4`. They
pair on **(meet id, race)** — the first and last fields of the filename. So the
server reads event/heat off the `.do4` and rebuilds the `.do3` name as
`<meetid>-000-E<ev>_H<ht>.do3`:

- **Meet id preserved** — Meet Manager only imports files whose first field
  matches its loaded meet.
- **Event/heat encoded** — so the operator can pick the right file from MM's
  manual Get-Times list (MM does not auto-match by filename here).

The relayed `.do3` is byte-identical to the original; only the name changes.

## Server endpoints (makosmeets v2)

All Bearer-auth (`Authorization: Bearer <LIVE_INGEST_TOKEN>`), fail closed.

| Endpoint | Use |
|---|---|
| `POST /api/live-results/ingest/file/` | Dolphin PC archives each raw `.do3/.do4`. Keyed in R2 by the **original filename** under `dolphin-raw/<date>/` (preserves meet id + race). |
| `GET /api/live-results/pending/` | Lists today's `.do3`s that have a paired `.do4`, each as `{race_id, meet_id, event, heat, src_name, out_name, key}`. |
| `GET /api/live-results/ingest/file/?key=<key>` | Downloads a raw file's bytes (allowlisted to `dolphin-raw/`). |

The date bucket follows the live meet (`current_meet` KV), else today — the
sink and `/pending` compute it identically.

## Operating it

- **Dolphin PC:** MakoSync **Dolphin** mode (unchanged) — it already uploads raw
  files when "Upload raw files" is on (the default).
- **Meet Manager PC:** MakoSync **Manager** mode (this pull is one half of it;
  the other reads the `.mdb` and pushes official results). Set the makosmeets URL
  + token (same as the Dolphin PC), the `.mdb` path, and optionally the import
  folder (defaults to the `.mdb`'s folder). Start. Each new heat lands renamed +
  toasts. To run pull-only without the official-results push, use the headless
  `--mode mm-import` (or `--mode manager --no-import`).

## Notes / limits

- Routed through makosmeets, so it needs internet at the venue. If venue Wi-Fi is
  unreliable, a LAN-direct fallback (receiver polls the Dolphin PC's share) is a
  straightforward follow-up — both PCs are on the same LAN at the deck.
- Re-times produce a new `.do4` race id; the matching `.do3` (if any) relays
  under its own race. Multi-heat exhibition heat-numbering can diverge between
  Dolphin and MM (a known reconciliation quirk) — the operator still picks.
