# Dolphin-events relay — endpoint plan (makosmeets side)

A tiny, **transient** relay so the **Manager** machine can hand the seeded
event/heat list to the **Dolphin** machine without the two PCs talking directly
(both only make outbound HTTPS to makosmeets — no LAN/firewall/peer config). The
Dolphin client turns the list into the CSV its Events screen imports, so the
operator picks "Event 5, Heat 2" instead of hand-typing ~40 events.

This is **not historical data** — it's a per-meet scratch mailbox in KV, the same
spirit as the live-results tier. Test/garbage pushes never touch `/athlete`,
`/team`, or any historical JSON.

MakoSync **generates the CSV from the Meet Manager `.mdb`** itself (no
events2dolphin step, no source file to pick) — `mdb_reader.build_dolphin_events_csv`,
verified byte-for-byte against a real events2dolphin output. The relay just carries
that CSV **text** verbatim, so the server is a dumb mailbox: it stores a string and
returns it. Client side is built (`src/makosync/client.py`:
`push_dolphin_events_csv` / `fetch_dolphin_events_csv`; path `DOLPHIN_EVENTS_PATH`).
This doc is the server contract to implement.

## Endpoint

`/api/live-results/dolphin-events/` — **trailing slash required** (the server runs
`trailingSlash: true`; a slashless POST 308-redirects and drops the body, same as
`/ingest/`). Auth: `Authorization: Bearer <LIVE_INGEST_TOKEN>`, **fail closed**
(reuse `getIngestToken` / `bearerMatches` from the ingest route; `503` if no token
configured server-side, `401` on mismatch). Add an `OPTIONS` handler with the same
CORS headers as `ingest/route.ts`.

### `POST` — Manager pushes the event list

Request body (what the client sends):
```jsonc
{
  "csv": "1,GIRLS 8&U 100 MEDLEY RELAY,1,1,A\r\n11,GIRLS 6&U 25 FREE,1,1,A\r\n...",
  "name": "testmeet3.mdb",                 // source .mdb filename; informational
  "lines": 79,                             // event count; informational
  "captured_at": "2026-05-29T17:21:58Z"    // client clock; informational
}
```
- `csv` is an **opaque text blob** (the Dolphin events file, CRLF-delimited).
  Don't parse or reformat it — store it verbatim.
- **Store** it in KV under the live meet, overwriting any prior push: suggested
  key `dolphin_events:<current_meet>` (fall back to a fixed `dolphin_events` key
  if no meet is live). Stamp **`updated_at` = server time** on every write (don't
  trust `captured_at`).
- Give it a TTL or clear it when a new meet is set live, so it doesn't linger
  across meets (a stale list is worse than none).

Response `200`:
```json
{ "ok": true, "updated_at": "2026-05-29T17:22:01Z", "lines": 79 }
```
The client reads `updated_at` and shows it as "events on server: …".

### `GET` — Dolphin loads the event list

Response `200` when present:
```json
{ "csv": "1,GIRLS 8&U 100 MEDLEY RELAY,1,1,A\r\n...", "name": "testmeet3.mdb", "updated_at": "2026-05-29T17:22:01Z", "lines": 79 }
```
When nothing has been pushed for the live meet, return `200` with
`{ "csv": "", "updated_at": null }` (the client treats empty as "none yet" —
**don't** require a 404, though a 404 is also handled as "not available").

## Notes / decisions

- **`updated_at` is the contract for "last updated on server"** — both client
  buttons display exactly this string. Use ISO‑8601 UTC like the ingest route's
  `updated_at`.
- **Scope = one live meet.** A single overwritten key is fine; there's only ever
  one live meet. Keying by `current_meet` just avoids a stale list bleeding into
  the next meet.
- **Size is trivial** (~40 events × ~60 bytes), so KV is plenty; no R2 needed.
- **No bake dependency.** This relay carries the list the *Manager machine*
  extracted from the local `.mdb`, so it works whether or not the meet was
  imported to makosmeets. (If you'd rather the Dolphin client pull straight from
  the already-baked meet, that's a viable alternative — but then it only works
  for meets that were imported, and needs a GET over the baked events.)

## CSV format (resolved)

The CSV format is **verified** — `mdb_reader.build_dolphin_events_csv` reproduces
a real events2dolphin file byte-for-byte for a 96-event RCSL meet:
`Event_no,NAME,heats,1,A` per line, CRLF, no header, where NAME is
`GIRLS/BOYS/MIXED` + `8&U`/`9-10`/`15-17` + distance + `FREE/BACK/BREAST/FLY` or
`MEDLEY RELAY`/`FREE RELAY`. The server never sees any of this — it just stores
and returns the `csv` string.
