# MakoSync ingest contract

MakoSync POSTs heat results as JSON to the makosmeets live-results endpoint, in
either of two modes:

- **Dolphin mode** → `tier:"unofficial", source:"dolphin"` — parsed `.do3`/`.do4`
  times, plus the raw file (forensic copy).
- **Meet Manager mode** → `tier:"official", source:"mm"` — reconciled results read
  from the MM `.mdb` (official places, backup-watch times). One POST per changed
  heat; no raw upload.

Requires `Authorization: Bearer <token>`; the server fails closed on a
missing/wrong token (and returns `503` if no token is configured server-side).

## `POST /api/live-results/ingest/` — parsed JSON

**Trailing slash required** — the server runs `trailingSlash: true`, which
308-redirects a slashless POST and drops the body. Fired the instant a new
`.do3`/`.do4` is parsed. Small payload, one POST per heat.

```jsonc
{
  "source_file": "004-000-001A-0010.do4",
  "format": "do4",                  // "do3" | "do4" | "csv"
  "dataset": "004",                 // from filename
  "race_id": "0010",                // monotonic dedup key (per dataset)
  "event": 0,                       // from file body; 0 if dry-run/blank
  "heat": 0,
  "round": "F",                     // "F" | "P" | "S"
  "tier": "unofficial",
  "captured_at": "2026-05-26T19:47:13Z",
  "lanes": [
    { "lane": 5, "time": "2.11", "timers": [2.11], "dq": false }
    // empty lanes omitted; the server treats absent lanes as no-time
  ]
}
```

The server reads `event`, `heat`, `race_id`, `tier`, `source`, and `lanes`
(`lane`/`time`/`place`/`dq`). `tier` is validated against
`{unofficial, official}` (default `unofficial`); `source` against
`{dolphin, mm, manual, mdb}` (default `dolphin`). The remaining fields
(`source_file`, `format`, `dataset`, `round`, `captured_at`, `timers`) are
accepted and ignored — safe to keep for audit. The server drops phantom /
zero / blank times, so a lane only reaches the TV with a real swim time (or DQ).

> **Note:** `place` IS read (it's how official results carry finish order) — it
> was previously documented as ignored, but `route.ts` parses it per lane. Send
> it only on the official tier; on the Dolphin/unofficial tier omit it entirely
> (the server does `Number(place)` and `Number(null) === 0`, which would stamp a
> bogus place 0 on every Dolphin lane). MakoSync omits `place` when it's unset.

Response: `200 OK` (`{ok, lanes_written, ...}`) or `4xx` for permanent failures
(won't retry). `5xx` / network errors are retried with backoff.

Idempotency: the server keys results by `(event, heat)` and merges tiers
(`unofficial` from Dolphin, `official` from Meet Manager — or Meet Mobile as a
fallback) without clobbering. Re-times arrive as new `race_id`s; the latest wins
on display.

### Official tier (Meet Manager mode)

Same endpoint, same shape; `tier:"official"`, `source:"mm"`, and each lane carries
`place`. MakoSync reads the MM `.mdb` (a lock-safe temp copy) via **mdbtools
`mdb-export`** — the same tool the server bake uses, which reads the raw Jet file
and so **ignores the Hy-Tek database password** that protects real RCSL files (the
ACE/ODBC driver can't open them). It keys each result to the same
`(event_number, heat, lane)` the meet bake used, and POSTs only the heats whose
lane tuples changed since the last poll. `race_id` is a per-heat content hash
(trace/idempotency tag).

```jsonc
{
  "event": 5,                       // Event.Event_no (joined via Event_ptr)
  "heat": 2,                        // Pre_heat || Fin_heat  (Pre wins)
  "tier": "official",
  "source": "mm",
  "race_id": "9f2ab0008d4d",        // content hash of this heat's lanes
  "captured_at": "2026-05-29T17:21:58Z",
  "lanes": [
    { "lane": 3, "time": "1:05.43", "place": 1, "dq": false },
    { "lane": 4, "time": "1:06.10", "place": 2, "dq": false }
  ]
}
```

Field parity with `v2/containers/mdb-parser/convert.mjs` is mandatory — the
overlay must key to the **same** lane the bake used. See
`src/makosync/mdb_reader.py` for the exact mapping (and the Pre-vs-Fin precedence
note). DQ is not yet emitted: it isn't represented in `convert.mjs` or the
documented `Entry` schema, so it needs a confirmed field from a real DQ'd row.

## `POST /api/live-results/ingest/file/` — raw file (on by default)

Fired right after the JSON POST succeeds. `multipart/form-data`:

- `file` — the raw bytes of the `.do3`/`.do4`/`.csv`, filename preserved.
- fields: `source_file`, `format`, `dataset`, `race_id`, `event`, `heat`,
  `round` (so the server can name/foreground the file without re-parsing).

The server archives it to R2 at:

```
dolphin-raw/<date>/E<event>-H<heat>-<race_id>.<ext>
```

`<date>` is the live meet's date (server-side, from `current_meet`), falling
back to today. The `.do3` and `.do4` for one race land as distinct files (ext
differs); a re-time is a new `race_id` so it's kept too. Re-sending the same
file (restart/replay) is idempotent (same key). Disable with `--no-raw`.

Response: `200 OK` (`{ok, key, bytes}`) or error. Retried on `5xx`/network.

## Rationale

- **Two channels** so JSON lands as fast as possible (TV wants times in <1s),
  while the raw `.do` is the forensic copy — re-parseable server-side if the
  client's parser is ever wrong about an edge case.
- **Bearer auth, fail-closed**: the pool-deck TV is a phantom-result hazard; an
  open ingest endpoint can be used to fabricate times.
