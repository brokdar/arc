"""Test-wide guards on the two ambient inputs a run must not depend on.

**The suite never reads the developer's `.env`.** `Settings` deliberately reads
the repo-root `.env` so host processes started from `backend/` see what
`just init` wrote (see `app/core/config.py`). Tests must not: a run's outcome
would then depend on whose machine it is on — a random POSTGRES__PASSWORD,
POSTGRES__HOST=db, real MCP keys. Disabling the dotenv source here (once, at
collection time, before any fixture or test module constructs `Settings`)
leaves the process environment as the only input, which tests set explicitly
with `monkeypatch.setenv`.

A test that needs dotenv behaviour can still pass `_env_file=<path>` itself.

**The suite never runs in UTC.** Nothing in this application may read the
*container's* clock: every instant it stores is aware UTC and every calendar
date it derives comes from `app.core.clock` (`MATCHING__TIMEZONE`). Ruff's DTZ
rules refuse the ways of writing that on purpose; pinning `TZ` here catches the
ways they cannot see — a library that reads the ambient zone behind our back,
and any spelling DTZ has no rule for. Unpinned, the container sits in UTC,
`MATCHING__TIMEZONE` also defaults to UTC, and a third clock is
indistinguishable from the right one, which is how four of them lived here
without a red test (issue #62).

`Pacific/Kiritimati` is UTC+14 all year with no DST, so the container's day
differs from Greenwich's for ten hours out of every twenty-four and never
agrees with the athlete's when a test moves them (`athlete_zone`).

**Hypothesis never asserts on wall-clock timings.** Its default profile carries
`deadline=200ms`, which is a timing assertion this suite does not intend: every
`@given` here covers pure domain code (`tests/unit/test_domain_*.py`) whose
examples run in microseconds, so the only way to exceed 200 ms is to be
descheduled — which under `pytest -n auto` on a loaded machine is ordinary. The
replay then passes, and hypothesis reports the disagreement as `FlakyFailure`
("unreliable test timings") rather than as the scheduling noise it is. Two
tests already paid for this one at a time (`test_domain_prediction.py`,
`test_domain_alignment.py`, both `deadline=None` per-test); this is the same
decision made once, for the suite.

Set **here** rather than in the `just test` recipe: a recipe is one of the ways
this suite is run, and it was not the one that gated merges — CI and the
pre-push hook both invoke `pytest` directly, so a pin that lives in the
justfile is absent from exactly the two runs that matter. The frontend pins its
two the same way, in `vitest.config.mts` and `playwright.config.ts` rather than
in a script.
"""

import os
import time

os.environ["TZ"] = "Pacific/Kiritimati"
# POSIX-only, and the deployment and CI are both Linux. Guarded rather than
# assumed so a contributor on Windows gets a suite that runs unpinned instead
# of one that will not collect.
if hasattr(time, "tzset"):
    time.tzset()

from hypothesis import settings as hypothesis_settings  # noqa: E402

from app.core.config import Settings  # noqa: E402

Settings.model_config["env_file"] = None

# Derived from whatever profile is already active, so this cannot displace one:
# `hypothesis._settings` loads its own `ci` profile (derandomize, no database,
# `print_blob`) at import time when `is_in_ci()`, and inheriting the current
# default carries all of that through while overriding the single setting this
# suite has an opinion about. Reading `is_in_ci` ourselves would work too, but
# it is private, and asking hypothesis what it decided is not the same as
# guessing it again. Shrinking, replay and `max_examples` are untouched.
hypothesis_settings.register_profile("arc", hypothesis_settings(deadline=None))
hypothesis_settings.load_profile("arc")
