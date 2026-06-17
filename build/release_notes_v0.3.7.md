## MakoSync v0.3.7

**Faster, more resilient Dolphin sends on flaky meet wifi.** Fixes the report of
results trickling out and then arriving in a clump, with long stalls whenever the
network hiccupped.

- **Results reach the live board faster.** The Dolphin folder is now scanned every
  0.75s (was 2s), and a finished `.do3`/`.do4` is sent the moment it's written
  (detected by its trailing checksum line) instead of waiting an extra poll —
  cutting the typical per-heat delay from ~2–4s to about a second. The scan
  interval is now adjustable on the Dolphin screen ("Scan every … s").

- **No more multi-minute stalls on a bad connection.** A heat that can't send now
  fails fast (3s per try) and retries up to ~10 times across polls (~30s total)
  before giving up and moving on — instead of the old worst case where two retry
  layers multiplied into several minutes of a frozen feed. The (background)
  raw-file upload and Manager-mode relays keep their longer, more patient retries.

No action needed beyond updating. Your settings and modes are unchanged.
