# Dolphin mode — send latency & flaky-network behavior

**Status: known issue, documented, not yet fixed.** Found 2026-06-03 during the
first live meet run. Investigation was code-only (the Dolphin PC sits on an
isolated `10.1.1.x` meet subnet, unreachable from the venue wifi the Mac was on,
so there are no corroborating live logs yet). All line numbers below are against
`src/makosync/watcher.py` and `src/makosync/client.py` as of that date.

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
