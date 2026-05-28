# DolphinSync ingest contract

The watcher POSTs parsed heat times as JSON to the makosmeets live-results
endpoint. Requires `Authorization: Bearer <token>`; the server fails closed on a
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

The server reads `event`, `heat`, `race_id`, `dataset`, `tier`, and `lanes`
(`lane`/`time`/`dq`). The remaining fields (`source_file`, `format`, `round`,
`captured_at`, `timers`, `place`) are accepted and ignored — safe to keep for
audit. `source` defaults to `"dolphin"` server-side. The server drops phantom /
zero / blank times, so a lane only reaches the TV with a real swim time (or DQ).

Response: `200 OK` (`{ok, lanes_written, ...}`) or `4xx` for permanent failures
(won't retry). `5xx` / network errors are retried with backoff.

Idempotency: the server keys results by `(event, heat)` and merges tiers
(`unofficial` from Dolphin, `official` from Meet Mobile) without clobbering.
Re-times arrive as new `race_id`s; the latest wins on display.

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
