# DolphinSync ingest contract

The Windows watcher posts to two endpoints. Both require
`Authorization: Bearer <token>`. Server fails closed on missing/wrong token.

## 1. `POST /ingest/heat` — parsed JSON (fast path)

Fired the instant a new `.do3`/`.do4` is parsed. Small payload, fires per heat.

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
    // empty lanes omitted by default; the server should treat absent lanes as no-time
  ]
}
```

Response: `200 OK` (body ignored) or `4xx` for permanent failures (won't
retry). `5xx` / network errors are retried with backoff.

Idempotency: server should treat `(dataset, race_id)` as the dedup key.
Re-times of a heat from Dolphin arrive as new `race_id`s — both should be
stored; the latest wins on display.

## 2. `POST /ingest/file` — raw file upload (slow path)

Fired after the JSON POST succeeds. `multipart/form-data` with one part:

- `file` — the raw bytes of the `.do3`/`.do4`/`.csv`, filename preserved.

Plus form fields (so the server doesn't need to re-parse the filename):
`source_file`, `format`, `dataset`, `race_id`.

Response: `200 OK` or error. Retried on `5xx`/network like the JSON path.

## Rationale

- **Two channels** so JSON lands as fast as possible (TV scoreboard wants
  times in <1s). Raw is forensic — re-parseable on the server side if the
  client's parser is wrong about an edge case.
- **JSON higher cadence**: the JSON path fires per file, no queueing. Raw
  uploads are throttled and may batch / coalesce if the network is slow.
- **Bearer auth, fail-closed**: pool-deck TV is a phantom-result hazard;
  an open ingest endpoint can be used to fabricate times.
