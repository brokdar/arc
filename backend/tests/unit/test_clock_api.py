"""`GET /clock`: the endpoint that stops the browser inventing a fifth clock.

Before this existed, no endpoint exposed `MATCHING__TIMEZONE`, so the frontend
computed "today" from the browser's own zone and used it to decide which week
the calendar opened on, which day "Today" showed, and — because two forms
defaulted a date field from it — which day a wellness reading and an appended
FTP were *filed under* (issue #62, finding 3).
"""

import datetime as dt
from collections.abc import Callable

from httpx import AsyncClient

from app.core.clock import athlete_today

CLOCK = "/api/v1/clock"


async def test_the_clock_reports_the_configured_zone_and_its_day(
    client: AsyncClient, athlete_zone: Callable[[str], None]
) -> None:
    athlete_zone("Pacific/Auckland")

    response = await client.get(CLOCK)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "timezone": "Pacific/Auckland",
        "today": athlete_today().isoformat(),
    }


async def test_the_day_is_the_zones_and_not_the_servers(
    client: AsyncClient, athlete_zone: Callable[[str], None]
) -> None:
    """Two zones twenty-five hours apart cannot both be on the UTC day.

    Whenever this runs, at least one of these answers is a calendar date the
    UTC clock would not have given — which is the whole point of serving it.
    """
    utc_today = dt.datetime.now(dt.UTC).date()
    served: list[dt.date] = []

    for zone in ("UTC+14:00", "UTC-11:00"):
        athlete_zone(zone)
        body = (await client.get(CLOCK)).json()
        assert body["timezone"] == zone
        served.append(dt.date.fromisoformat(body["today"]))

    assert served[0] > served[1]
    assert any(day != utc_today for day in served)


async def test_the_clock_needs_a_session(anon_client: AsyncClient) -> None:
    """Mounted on the guarded router like every other read."""
    assert (await anon_client.get(CLOCK)).status_code == 401
