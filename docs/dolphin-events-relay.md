# Dolphin-events relay — endpoint plan (makosmeets side)

A tiny, **transient** relay so the **Manager** machine can hand the seeded
event/heat list to the **Dolphin** machine without the two PCs talking directly
(both only make outbound HTTPS to makosmeets — no LAN/firewall/peer config). The
Dolphin client turns the list into the CSV its Events screen imports, so the
operator picks "Event 5, Heat 2" instead of hand-typing ~40 events.

This is **not historical data** — it's a per-meet scratch mailbox in KV, the same
spirit as the live-results tier. Test/garbage pushes never touch `/athlete`,
`/team`, or any historical JSON.

The MakoSync client side is **already built** (`src/makosync/client.py`:
`push_dolphin_events` / `fetch_dolphin_events`; path `DOLPHIN_EVENTS_PATH`). This
doc is the server contract to implement.

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
  "events": [
    { "event": 1, "name": "Boys 9-10 50 Meter Freestyle", "heats": 2 },
    { "event": 2, "name": "Mixed Open 200 Meter Medley", "heats": 1 }
  ],
  "count": 2,
  "captured_at": "2026-05-29T17:21:58Z"   // client clock; informational
}
```
- `event` (int, ≥1), `name` (string, operator-facing label — not parity-keyed),
  `heats` (int, ≥1). Validate loosely; drop malformed rows rather than 400 the
  whole push if you like.
- **Store** the list in KV under the live meet, overwriting any prior push:
  suggested key `dolphin_events:<current_meet>` (fall back to a fixed
  `dolphin_events` key if no meet is live). Stamp **`updated_at` = server time**
  on every write (don't trust `captured_at`).
- Give it a TTL or clear it when a new meet is set live, so it doesn't linger
  across meets (a stale list is worse than none).

Response `200`:
```json
{ "ok": true, "updated_at": "2026-05-29T17:22:01Z", "count": 2 }
```
The client reads `updated_at` and shows it as "events on server: …".

### `GET` — Dolphin loads the event list

Response `200` when present:
```json
{ "events": [ /* same shape as posted */ ], "updated_at": "2026-05-29T17:22:01Z", "count": 2 }
```
When nothing has been pushed for the live meet, return `200` with
`{ "events": [], "updated_at": null }` (the client treats empty as "none yet" —
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

## Open item (client side, gated on hardware)

The exact CSV columns/headers the installed **CTS Dolphin** build imports on its
Events screen are unverified. `mdb_reader.write_dolphin_events_csv` currently
writes headerless `event,name,heats` (events2dolphin style). Confirm on the
Dolphin PC (`10.1.1.152`, VNC) before relying on it at a live meet, and adjust
that one writer if needed — no server change required.
