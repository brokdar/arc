"""The athlete's clock — the one answer to "what day is it".

There is one athlete, and therefore one local clock: `MATCHING__TIMEZONE`.
Every calendar date the application derives — the plan week a caller gets when
it names none, the day a wellness prompt is raised for, the day an anchor
becomes effective on, the athlete's age — is that clock's answer.

**Why this module exists at all.** The rule was already written down (see
`app.core.config.WellnessSettings.prompt_hour_local`) and already implemented,
twice, in two services; the layers that could not reach either of them —
`app.api.schemas`, `app.mcp` — grew their own. Issue #62 counted four clocks in
one process (UTC, the container's `TZ`, the browser's, and this one) and one
payload built from three of them. A rule that lives in a docstring is a rule
each layer re-derives; a rule that lives in a module every layer can import is
one they share. It sits in `app.core` rather than `app.services` for exactly
that reason: `app.api.schemas` and `app.mcp` need it too, and a schema reaching
into a service to ask the date would be a worse dependency than this one.

The two clocks this module is **not**:

* **UTC.** Every *instant* the application stores is aware UTC, and that is
  right — `app.persistence.types.UtcDateTime` enforces it. But a calendar date
  is not an instant, and reading one off the UTC instant answers "what day is
  it in Greenwich", which is the wrong question and the wrong answer for a few
  hours out of every day.
* **The container's.** `dt.date.today()` reads `/etc/localtime`, which no
  deployment file in this repository sets. It equals UTC today and is one
  environment variable away from being a fourth answer nobody chose.
"""

import datetime as dt
from functools import lru_cache

from app.core.config import get_settings
from app.domain.activity import parse_timezone

__all__ = ["athlete_now", "athlete_timezone", "athlete_today", "athlete_zone"]


@lru_cache(maxsize=8)
def _resolve(zone: str) -> dt.tzinfo:
    """Resolve and cache one zone name.

    Cached because `parse_timezone` scans the whole zone database on its IANA
    branch and this runs on every read that needs a date. Keyed on the name
    rather than on nothing, so a test that overrides `MATCHING__TIMEZONE` gets
    the zone it asked for; bounded because the set of names one process sees is
    the configured one plus whatever its tests set.
    """
    return parse_timezone(zone)


def athlete_timezone() -> str:
    """The configured zone name, as written in `MATCHING__TIMEZONE`.

    The name rather than a `tzinfo`, for the callers that store it (a manual
    session's `timezone` column) or hand it to a client that has its own zone
    database (the frontend's `Intl`).

    Raises:
        ValueError: When the configured value cannot be resolved. Validated on
            the way out so that a deployment with an unusable zone fails at the
            first read rather than storing a name nothing can resolve later.
    """
    zone = get_settings().matching.timezone
    _resolve(zone)
    return zone


def athlete_zone() -> dt.tzinfo:
    """The configured zone, resolved.

    Raises:
        ValueError: When the configured value cannot be resolved.
    """
    return _resolve(get_settings().matching.timezone)


def athlete_now(now: dt.datetime | None = None) -> dt.datetime:
    """``now`` (default: this instant) as an aware datetime on the athlete's clock.

    For the callers that need the local *hour*, not just the local day — the
    wellness prompt is raised at `WELLNESS__PROMPT_HOUR_LOCAL` on this clock,
    and across a DST transition the local hour is the only thing that answers
    "has the evening arrived".

    Raises:
        ValueError: When the configured timezone cannot be resolved, or when
            ``now`` is naive — a naive instant would be read as local time on
            whichever machine happened to run this, which is the bug this
            module exists to remove.
    """
    moment = now or dt.datetime.now(dt.UTC)
    if moment.tzinfo is None:
        raise ValueError(
            "athlete_now needs an aware instant; a naive one would be read as "
            "local time on whichever machine happened to run this"
        )
    return moment.astimezone(athlete_zone())


def athlete_today(now: dt.datetime | None = None) -> dt.date:
    """Today's date on the athlete's clock (`MATCHING__TIMEZONE`).

    Raises:
        ValueError: When the configured timezone cannot be resolved. Loud
            rather than defaulted: a sweep that silently fell back to UTC would
            mark sessions missed up to a day early for anybody east of it.
    """
    return athlete_now(now).date()
