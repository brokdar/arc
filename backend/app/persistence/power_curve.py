"""A session's stored power curve: one row per session, replaced on recompute.

Its own module and table rather than a block on the metric artefact
(`app.persistence.metrics`) because the two have different lifetimes. The
artefact is append-only and pins the anchors it was computed against, so its
history answers "what did the numbers say when the verdict was given". The
power curve depends on no anchor and no athlete — it is a pure function of the
stored stream (`app.domain.power_curve`) — so a version chain would only ever
record that the same function ran again.
"""

import datetime as dt
import uuid

from sqlalchemy import ForeignKey, Integer, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.power_curve import MmpPoint, PowerCurve
from app.persistence.db import Base, flush
from app.persistence.types import JSONColumn, UtcDateTime


class SessionMmpRow(Base):
    """One session's mean-maximal power at every grid duration.

    **One row per session, replaced on recompute** — no anchor pin, no
    version chain. The curve is a pure function of the stream, so the newest
    computation is the whole truth about it, and a versioned table would keep
    copies nobody can tell apart. Pinned by
    `test_session_mmp_persistence.py::test_recompute_replaces_the_row`.

    The points are stored as four parallel arrays, one entry per grid duration
    in ascending order, because the curve is always read whole and a row per
    duration would be ~700 rows per long ride.

    **`clean` is stored, not derived later.** Each duration records whether
    its winning span contained zero-filled seconds or a region the cleaner
    repaired, computed from the very stream the watts came from. Recomputing it
    on read would consult whatever stream is on disk *then* — and a rebuilt
    stream may have different repairs than the one the best was found on.
    Pinned by `test_domain_power_curve.py::test_clean_flag`.

    `watts_dw` is in **tenths of a watt**, as integers: exactly the precision
    the domain rounds to, so a round trip through JSONB cannot drift.
    """

    __tablename__ = "session_mmp"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), unique=True
    )
    #: `app.domain.power_curve.GRID_VERSION` the durations were drawn from.
    grid_version: Mapped[int] = mapped_column(Integer)
    durations_s: Mapped[list[int]] = mapped_column(JSONColumn)
    watts_dw: Mapped[list[int]] = mapped_column(JSONColumn)
    #: First row of each winning window on the session's 1 Hz grid; the window
    #: is `[start, start + duration)`.
    start_indices: Mapped[list[int]] = mapped_column(JSONColumn)
    clean: Mapped[list[bool]] = mapped_column(JSONColumn)
    computed_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )

    def curve(self) -> PowerCurve:
        """The stored curve, as the domain value it was written from."""
        return PowerCurve(
            grid_version=self.grid_version,
            points=tuple(
                MmpPoint(
                    duration_s=duration,
                    watts=watts / 10,
                    start_index=start,
                    clean=clean,
                )
                for duration, watts, start, clean in zip(
                    self.durations_s,
                    self.watts_dw,
                    self.start_indices,
                    self.clean,
                    strict=True,
                )
            ),
        )


def row_for(session_id: uuid.UUID, curve: PowerCurve) -> SessionMmpRow:
    """A new row holding ``curve`` for one session."""
    return SessionMmpRow(
        session_id=session_id,
        grid_version=curve.grid_version,
        durations_s=[point.duration_s for point in curve.points],
        watts_dw=[round(point.watts * 10) for point in curve.points],
        start_indices=[point.start_index for point in curve.points],
        clean=[point.clean for point in curve.points],
    )


class PowerCurveRepository:
    """SQLAlchemy repository for stored power curves."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, session_id: uuid.UUID) -> SessionMmpRow | None:
        """The curve stored for one session, or ``None``."""
        result = await self._session.execute(
            select(SessionMmpRow).where(SessionMmpRow.session_id == session_id)
        )
        return result.scalar_one_or_none()

    async def replace(
        self, session_id: uuid.UUID, row: SessionMmpRow | None
    ) -> SessionMmpRow | None:
        """Swap one session's curve for ``row``, or remove it when ``None``.

        Delete-then-insert, flushed in that order, so the unique session key
        never sees two rows.
        """
        await self._session.execute(
            delete(SessionMmpRow).where(SessionMmpRow.session_id == session_id)
        )
        await flush(self._session)
        if row is None:
            return None
        self._session.add(row)
        await flush(self._session)
        return row
