"""The plan as a calendar: week windows, and whether the plan is being enforced.

Two small, pure things WP-3 needs and nothing else owns.

**The week window.** A plan week runs Monday to Sunday (ISO-8601), and the
calendar reads it as seven consecutive days rather than "whatever sessions
exist" — an empty Thursday is part of the week, and a projection that omits it
makes the adapter reconstruct the grid. :func:`week_start` and
:func:`week_dates` are the only two facts involved; they take the day they are
asked about, because `app.domain` has no clock.

**Plan state.** ``paused`` is a statement about *enforcement*, not about
recording: ingestion, matching and scoring carry on exactly as before, and the
one thing that stops is missed-session marking (WP-6.7), so a week the athlete
stepped away from does not fill up with `missed`. Nothing consumes it yet —
the field and its audit trail land now so the later work package has state to
read rather than a migration to write.
"""

import datetime as dt
from enum import StrEnum

#: Days in a plan week. Seven, and named, because the constant appears in the
#: window arithmetic and in the projection that has to produce exactly that
#: many days.
WEEK_DAYS = 7


class PlanState(StrEnum):
    """Whether the plan is being enforced.

    ``PAUSED`` does not pause the application: see the module docstring for
    what it does and does not stop.
    """

    ACTIVE = "active"
    PAUSED = "paused"


def week_start(day: dt.date) -> dt.date:
    """Return the Monday of the week ``day`` falls in.

    ISO-8601 weeks: Monday is day 0. The choice is the calendar's, not the
    athlete's — a training week that starts on Sunday would make "the week of
    the 10th" ambiguous between the UI and the API.
    """
    return day - dt.timedelta(days=day.weekday())


def week_dates(start: dt.date) -> tuple[dt.date, ...]:
    """Return the seven consecutive dates beginning at ``start``.

    ``start`` is taken as given rather than snapped to a Monday: a caller that
    asks for the week beginning Wednesday gets seven days from Wednesday. The
    snapping decision belongs to whoever picks the default window.
    """
    return tuple(start + dt.timedelta(days=offset) for offset in range(WEEK_DAYS))
