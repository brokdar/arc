"""Response schema for the athlete's clock."""

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class ClockRead(BaseModel):
    """Which clock every calendar date in this API is on.

    Deployment configuration, not profile data — which is why it is its own
    resource and not a field on `AthleteRead`: there is nothing here the
    athlete can PATCH, and `MATCHING__TIMEZONE` is set once by whoever runs the
    instance.
    """

    model_config = ConfigDict(extra="forbid")

    #: The athlete's home timezone (`MATCHING__TIMEZONE`) — an IANA name, a
    #: fixed offset (`UTC+02:00`), or `UTC`. Resolvable by the browser's `Intl`
    #: as well as by Python: `app.domain.activity.parse_timezone` refuses the
    #: zone-database keys `Intl` cannot take, so a client can pass this
    #: straight to `Intl.DateTimeFormat` as a `timeZone`.
    timezone: str = Field(examples=["Europe/Berlin"])
    #: Today on that clock, at the moment of the read.
    #:
    #: **The frontend does not use this, and that is the right call.** It
    #: derives its day from `timezone` and the browser's instant, because a
    #: session left open across a midnight has to change day without a refetch
    #: and a served date cannot. What is served here is the deployment's own
    #: answer, for reading the clock rather than deriving from it: a `curl`
    #: after changing `MATCHING__TIMEZONE`, and a client with no zone database
    #: to resolve `timezone` with. It is not a skew check — nothing compares it
    #: against the caller's clock, and a browser whose system date is a day out
    #: still opens on the wrong day.
    today: dt.date
