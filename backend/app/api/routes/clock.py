"""The one endpoint that tells a client what day it is (issue #62).

There is one athlete and therefore one local clock — `MATCHING__TIMEZONE`.
Every calendar date this API takes and returns is a day on it: a plan week's
`start`, a wellness `local_date`, an anchor's `effective_date`.

Until this existed, no endpoint exposed that zone, so the browser had no way to
learn it and computed "today" from its own instead. That is a different clock
whenever the athlete travels or the laptop is set wrong, and it decided which
week the calendar opened on, which day the page named "Today" showed, and —
because two forms defaulted a date field from it — which day a wellness reading
and an appended FTP were *filed under*. The frontend's answer was to hide the
standing wellness prompt when the two disagreed, so the athlete silently lost
the day's question with nothing on screen to say why.

Read-only and config-derived: this router carries no service, because there is
no state here to have a use-case about. `app.main` mounts it on the protected
`/api/v1` router like every other one.
"""

from fastapi import APIRouter

from app.api.schemas.clock import ClockRead
from app.core.clock import athlete_timezone, athlete_today

router = APIRouter(prefix="/clock", tags=["clock"])


@router.get("")
async def get_clock() -> ClockRead:
    """The athlete's timezone, and today's date on it.

    Both from one read of the clock, so they cannot disagree with each other
    across a midnight between two calls.
    """
    return ClockRead(timezone=athlete_timezone(), today=athlete_today())
