# Dolphin mode — send latency & flaky-network behavior

**Status: mostly fixed 2026-06-16 (not yet released).** The analysis below was
verified against the current code (it still held exactly) and the low-risk fixes
landed — see **What shipped** at the bottom. The one remaining item is the
structural send-queue (ranked fix #3), deliberately deferred to its own release.
Found 2026-06-03 during the first live meet run. Investigation was code-only (the
Dolphin PC sits on an isolated `10.1.1.x` meet subnet, unreachable from the venue
wifi the Mac was on, so there are no corroborating live logs yet). The line
numbers below are against `watcher.py`/`client.py` as of 2026-06-03; the fixes
shifted them slightly.

## Symptom

Operator report: results are "kind of slow to send to the server from the Dolphin
computer," and the feel gets worse on spotty pool wifi — results trickle, then
arrive in a clump.

## Root cause: the heat POST is synchronous on the single poll thread

`Watcher._main_loop` (`watcher.py:121`) is the only thread that detects files,
and it calls `client.send_heat()` **inline** in `_handle` (`watcher.py:212`).
There is no send queue for the heat JSON. So anything that slows or stalls one
send stalls *detection of every following heat* — classic head-of-line blocking.

(The raw-file upload is correctly offloaded to a second thread, `_raw_loop`
(`watcher.py:234`), pulling from `_raw_q`. The forensic raw upload therefore does
**not** block `/tv`. Only the heat JSON — the thing the live board needs — runs
serially.)

## Source 1 — ~2–4 s baked in per heat, even on perfect wifi

Two constants in `watcher.py`:

```python
POLL_INTERVAL = 2.0          # watcher.py:37  — folder scanned every 2 s
SIZE_STABLE_GRACE = 0.5      # watcher.py:38
```

`_is_stable` (`watcher.py:177`) requires a file to be observed **twice** with an
unchanged size before it is parsed: the first poll that sees a new file records
its size and returns `False`; only the *next* poll (~2 s later) clears the
stability gate. So from "Dolphin writes the `.do4`" to "POST starts":

- best case ~2 s (file lands just before a poll, cleared on the next),
- worst case ~4 s (file lands just after a poll → 2 s to first-detect, +2 s to
  confirm stable).

A Dolphin `.do4` is a few hundred bytes written in one shot, so the double-poll
"stability" wait buys almost nothing here while costing a guaranteed extra
`POLL_INTERVAL` on **every** heat. This is the dominant "normally slow" factor.

The Dolphin poll interval is **not operator-adjustable**: `_make_dolphin_watcher`
in `gui.py:368` builds the `WatcherConfig` without passing `poll_interval`, so it
always uses the hardcoded `2.0`. The GUI's poll-interval field is wired only to
Manager mode (`gui.py:401`, `gui.py:785`). You cannot turn this down from the app
at the meet.

## Source 2 — flaky wifi: it *does* back off, but blocking and serially

On a network error or 5xx, `client._send_with_retry` (`client.py:221`) does
exponential backoff **inside one send call**:

```python
DEFAULT_TIMEOUT = 8.0        # client.py:37  — per-attempt socket timeout
RETRY_DELAYS = (1, 2, 4, 8)  # client.py:38  — prepended with 0 → 5 attempts
```

- each attempt waits up to **8 s** on the socket,
- between attempts it sleeps **1 s, 2 s, 4 s, 8 s** (15 s of sleeps total),
- a 4xx is treated as permanent and is **not** retried (`client.py:240` — correct).

So:

| Network condition | Cost of one `send_heat` |
|---|---|
| healthy | < 0.5 s |
| one packet drop, succeeds on retry | ~9 s (8 s timeout + 1 s sleep) |
| two drops | ~19 s |
| fully dead | **55 s** (5 × 8 s timeouts + 15 s sleeps) before it returns failure |

And there is a **second retry layer on top**. When `_send_with_retry` finally
returns failure, `_handle` returns `False`, so `_note_failure` (`watcher.py:150`)
leaves the file unsent and the watcher re-runs the *entire* send cycle on the next
poll, up to `MAX_HANDLE_ATTEMPTS = 8` times (`watcher.py:39`). Worst case for a
single un-sendable heat on a dead link: **~8 × 55 s ≈ 7.5 minutes / ~40 HTTP
attempts**, during all of which the live feed is frozen and later heats pile up
behind it.

So the answer to "does it delay before trying again?" is **yes** — 1/2/4/8 s
within a call, 2 s between whole-call retries. The defect isn't a missing backoff;
it's that the backoff is **blocking and serial on the one thread that also does
detection**, so a flaky link stalls the whole live feed rather than just the one
heat. That is the trickle-then-clump symptom.

## Fixes, ranked

1. **Cut the baseline.** Drop the Dolphin `POLL_INTERVAL` (~0.75 s) and treat a
   size-stable-on-first-sight small `.do4`/`.do3` as ready instead of requiring a
   second poll. Takes the per-heat floor from 2–4 s to < 1 s. Highest leverage,
   lowest risk. (Optionally expose poll interval in the Dolphin GUI like Manager
   mode already has.)
2. **Fail fast.** Shorten `DEFAULT_TIMEOUT` for the heat POST (8 s → ~3 s). A ~1 KB
   JSON POST to Cloudflare completes well under a second on a live link; 8 s just
   wastes time before the backoff on a flaky one.
3. **Move the heat POST off the detection thread** — give it a send queue like the
   raw uploader already has — so a stuck send can't stall detection/sending of the
   following heats. This is the structural fix for the clumping.
4. **Collapse the doubled retry layer.** The in-call 5-attempt backoff *and* the
   8× across-poll retry compound to ~40 attempts / ~7 min for one stuck heat. One
   layer is enough.

Items 1–2 are constant changes and safe to ship between meets; 3 is the larger
structural change. Any fix ships as a new **release** that operators reinstall —
never mid-meet.

## What shipped (2026-06-16, unreleased)

Fixes 1, 2, and 4 landed; fix 3 (send queue) is deferred. The adversarial review
found the naive form of each "fix" silently drops results, so the shipped
versions are the *safe* variants:

1. **Baseline cut.** `POLL_INTERVAL 2.0 → 0.75` (`watcher.py`). Instead of the
   unsafe "trust on first sight," `_is_stable` now trusts a `.do3/.do4` on first
   sight **only if it already ends with its Dolphin checksum line** (the
   end-of-write sentinel — `parser.ends_with_checksum` / `Watcher._looks_complete`).
   A mid-write file lacks the checksum and still falls back to the two-poll
   size-stable gate, so the silent-zero-lane-drop hazard is avoided. `.csv` (no
   sentinel) always uses the size-stable gate. Per-heat floor drops from ~2–4 s
   to <~1 s. **Exposed in the Dolphin GUI** ("Scan every … s", `cfg.dolphin_poll`,
   floor 0.25 s) — previously hardcoded.
2. **Fail fast (split timeout).** New `HEAT_TIMEOUT = 3.0` for the heat POST only;
   the raw upload + relay/download GETs keep `DEFAULT_TIMEOUT = 8.0`. (Not a flat
   global cut — the timeout is shared across all endpoints, so cutting the global
   constant would make the large raw upload and the `download_file` pull fragile.)
4. **Stop the two retry layers multiplying.** The 7.5 min came from
   (5 in-call attempts) × (8 poll retries) = the 8 s socket timeout multiplied 40×.
   The heat POST now opts OUT of the in-call backoff (`send_heat` passes
   `retry_delays=()`), so one heat send = ONE ~3 s attempt; the watcher's
   across-poll layer is the only retry, capped at `MAX_HANDLE_ATTEMPTS = 3`.
   (`RETRY_DELAYS` stays `(1,2,4,8)` for the off-thread raw upload + relays, which
   are fine to retry longer.) That across-poll layer is kept because it is also the
   *only* retry for parse/lock failures (`parse_file → None`), so deleting it would
   silently drop a still-writing file. Worst-case for one stuck heat: was
   ~7.5 min / ~40 attempts → now ~3 × 3 s ≈ **10 s** / 3 attempts, then we give up
   and keep the feed moving (the venue link is reliable, so a 10 s-failing send is
   a real outage, not a blip).

Tests: `tests/test_watcher_stability.py` (checksum fast-path + truncation
safety), `tests/test_client_timeout.py` (split timeout + bounded retries),
`tests/test_parser.py::test_ends_with_checksum` + `…_real_samples…`.

**Still open — fix 3 (send queue).** The heat POST is still synchronous on the
detection thread, so a stuck send still head-of-line-blocks following heats (now
for ~80–90 s, not ~7.5 min). The structural cure (offload heat JSON to a worker
like `_raw_loop`) is its own release: it inverts where `_seen`/retry state lives
(out-of-order requeue, restart double-send/lost-send, `--once` path, rewritten
retry tests) and needs a corroborating live-log capture from the meet subnet
plus makosmeets-owner sign-off that a late older heat can't overwrite a newer one.

## Code map

- `watcher.py:37-39` — `POLL_INTERVAL`, `SIZE_STABLE_GRACE`, `MAX_HANDLE_ATTEMPTS`
- `watcher.py:121-142` — `_main_loop` (single detection thread)
- `watcher.py:177-192` — `_is_stable` (the double-poll stability gate)
- `watcher.py:194-230` — `_handle` (synchronous `send_heat` inline)
- `watcher.py:150-158` — `_note_failure` (across-poll retry, cap)
- `watcher.py:234-252` — `_raw_loop` (raw upload, correctly offloaded)
- `client.py:37-38` — `DEFAULT_TIMEOUT`, `RETRY_DELAYS`
- `client.py:221-247` — `_send_with_retry` (in-call exponential backoff)
- `gui.py:368` — Dolphin `WatcherConfig` built without `poll_interval` (hardcoded 2 s)
