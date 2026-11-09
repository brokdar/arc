#!/usr/bin/env python3
"""Measure whether this host's wall clock steps backwards.

WHY THIS EXISTS. This dev host (WSL2) steps `CLOCK_REALTIME` backwards by
85-180 ms roughly twice a minute, from host time sync. On 18 Aug 2026 that
burned a whole `implement-plan` run — two PRs hard-stopped on `just gate` with
failures in `test_proposals_api.py`, `test_scoring_api.py`,
`test_matching_api.py` and `test_mcp_tools.py`, none related to the feature —
and the same stepping turned out to be a real defect in `AnchorService.current`
(PR #66). So this is one minute of measurement that settles a question agents
have otherwise spent hours arguing from vibes.

It compares `time.time()` against `time.monotonic()`, which by definition never
goes backwards. Any interval where realtime advanced measurably less than
monotonic is a backwards step, and the difference is its size.

Usage:
    python3 .claude/skills/diagnose/clock-check.py [seconds]   # default 75

Exit codes:  0 = clock is well behaved over the sample
             1 = at least one backwards step observed
"""

import sys
import time

# Below this, you are reading scheduler jitter and timer resolution, not a step.
THRESHOLD_S = 0.005


def main() -> int:
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 75.0
    print(f"sampling for {duration:.0f}s ...", flush=True)

    steps: list[tuple[float, float]] = []
    started = time.monotonic()
    prev_real, prev_mono = time.time(), time.monotonic()

    while (elapsed := time.monotonic() - started) < duration:
        time.sleep(0.01)
        real, mono = time.time(), time.monotonic()
        slip = (mono - prev_mono) - (real - prev_real)
        if slip > THRESHOLD_S:
            steps.append((elapsed, slip))
            print(f"  backwards step: {slip * 1000:7.1f} ms at t+{elapsed:5.1f}s", flush=True)
        prev_real, prev_mono = real, mono

    if not steps:
        print(f"clean: no backwards step over {THRESHOLD_S * 1000:.0f} ms in {duration:.0f}s")
        print("An intermittent time-dependent failure here is NOT this. Keep diagnosing.")
        return 0

    worst = max(s for _, s in steps)
    print(f"\n{len(steps)} backwards step(s) in {duration:.0f}s, worst {worst * 1000:.1f} ms")
    print("The host clock is non-monotonic. Now ask the OTHER question:")
    print("is the code under test ENTITLED to assume time moves forwards?")
    print("If it compares a stored created_at against a later now(), it is not — see PR #66.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
