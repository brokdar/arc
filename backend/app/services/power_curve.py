"""Storing and reading a session's power curve.

The curve itself is computed by `app.domain.power_curve` from the stored
stream, which this layer may not read — `app.ingest.analysis.SessionAnalyser`
does that and hands the result to :meth:`PowerCurveService.store` every time a
session's metrics are computed. This service owns the two things a caller needs
around it: replacing the one stored row, and saying *why* there is no curve
when there is none, because "no power meter" and "not computed yet" are fixed
by different actions.
"""

import uuid
from dataclasses import dataclass
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.domain.actor import Actor
from app.domain.power_curve import PowerCurve
from app.domain.streams import StreamChannel
from app.persistence.activity import SessionRepository, SessionRow
from app.persistence.db import commit
from app.persistence.power_curve import PowerCurveRepository, row_for

logger = get_logger(__name__)

#: Why a session typed in by hand has no curve.
NO_RECORDING = (
    "this session was entered by hand, so there is no power stream to find "
    "a best effort in"
)
#: Why a recorded session without a power channel has none.
NO_POWER = "no power was recorded on this session — it needs a power meter"
#: Why a session that recorded power has none: it predates the curve.
NOT_COMPUTED = (
    "the power curve has not been computed for this session yet — recompute "
    "its metrics to add it"
)


@dataclass(frozen=True, slots=True)
class SessionPowerCurve:
    """One session's curve, or the reason it has none — exactly one is set."""

    session_id: uuid.UUID
    curve: PowerCurve | None
    reason: str | None


def _reason(session_row: SessionRow) -> str:
    """Why ``session_row`` has no stored curve, naming what would supply one."""
    if not session_row.recordings:
        return NO_RECORDING
    if any(
        StreamChannel.POWER.value in recording.channels
        for recording in session_row.recordings
    ):
        return NOT_COMPUTED
    return NO_POWER


class PowerCurveService:
    """Use-cases for the stored power curve. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        repository: PowerCurveRepository,
        sessions: SessionRepository,
    ) -> None:
        self._session = session
        self._repository = repository
        self._sessions = sessions

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(session, PowerCurveRepository(session), SessionRepository(session))

    async def read(self, session_id: uuid.UUID) -> SessionPowerCurve:
        """One session's stored curve, or why it has none.

        Raises:
            NotFoundError: When no session has that id.
        """
        session_row = await self._sessions.get(session_id)
        if session_row is None:
            raise NotFoundError(f"Session {session_id} not found")
        row = await self._repository.get(session_id)
        if row is None:
            return SessionPowerCurve(
                session_id=session_id, curve=None, reason=_reason(session_row)
            )
        return SessionPowerCurve(session_id=session_id, curve=row.curve(), reason=None)

    async def store(
        self, session_id: uuid.UUID, curve: PowerCurve | None, *, actor: Actor
    ) -> bool:
        """Make ``curve`` the session's one stored curve; ``None`` removes it.

        Returns whether anything changed. A curve equal to the stored one is
        left alone — the same stream gives the same curve, so a second rebuild
        or recompute of an unchanged session writes nothing. Not audited: the
        metric recomputation that calls this is, and this row is a derivation
        of the same stream, not a decision anyone made.
        """
        existing = await self._repository.get(session_id)
        if existing is None and curve is None:
            return False
        if existing is not None and curve is not None and existing.curve() == curve:
            return False
        await self._repository.replace(
            session_id, None if curve is None else row_for(session_id, curve)
        )
        await commit(self._session)
        logger.info(
            "power_curve_stored",
            session_id=str(session_id),
            points=0 if curve is None else len(curve.points),
            actor=str(actor),
        )
        return True
