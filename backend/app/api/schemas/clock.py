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
    #: Today on that clock, at the moment of the read. The server's own answer,
    #: so a client with a skewed system clock still opens on the right day; a
    #: client that stays open across midnight re-derives it from `timezone`.
    today: dt.date
