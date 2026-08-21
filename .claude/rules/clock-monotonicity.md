---
paths: backend/app/**, backend/tests/**
---

# The wall clock steps backwards; code may not assume it doesn't

`CLOCK_REALTIME` is not monotonic: NTP corrections step it backwards in
production, and the WSL2 dev host steps back ~85–130 ms about twice a minute
(measured 2026-08-18). Three shipped defects assumed otherwise before this
became a rule:

1. **#66** — `anchor_as_of` filtered `created_at <= now`, so a row written
   moments ago vanished when the clock stepped back past its stamp.
2. **#74** — the newest-anchor tie-break keyed on `created_at`, so a
   correction appended after a step lost to the value it corrected.
3. **#61 (root cause)** — itsdangerous rejects a signed timestamp from the
   "future" (`age < 0`), so a session cookie signed at login 401'd the next
   request after a step rewound the clock past the login instant. Fixed in
   `app/api/session.py`.

When writing or reviewing code that compares two wall-clock reads:

- **Never compare a freshly written timestamp against a later `now()`** and
  treat "in the future" as invalid, expired, or absent. A stamp a few
  seconds ahead of the reader's clock means skew, not fraud. Reject freely on
  the *old* side of a validity window; on the future side, forgive only up to
  a bounded skew (`CLOCK_STEP_TOLERANCE` in `app/api/session.py`, 300 s).
  Unbounded forgiveness is not the same fix: it stops being "the clock moved"
  and starts being "the old-side bound is skipped whenever the reader's clock
  is behind", which a restored snapshot or a dead RTC turns into no expiry at
  all.
- Durations and deadlines come from `time.monotonic()` / `perf_counter()`,
  never from subtracting two `datetime.now()` reads.
- Ordering of events the same process wrote needs an explicit sequence
  (autoincrement, uuid7 insertion order) — not `created_at`.
- **Tests**: reproduce clock sensitivity by stamping the artifact into the
  future (a row, a cookie timestamp) rather than monkeypatching the clock —
  same state, deterministic. See `test_auth.py`'s clock-step tests.
- An intermittent local failure in a time-adjacent test: measure the clock
  first (compare `time.time()` against `time.monotonic()` for a minute)
  before hunting the test.
