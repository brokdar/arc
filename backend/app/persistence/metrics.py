"""The versioned metric artefact: one session's numbers, as of one moment.

Invariant 1 of the build plan, applied to metrics: a recomputation writes a
**new version** and supersedes the old one; nothing is updated in place, and
every version stays readable. That is not bookkeeping — a verdict the athlete
confirmed was confirmed against *particular numbers*, and WP-7 has to be able
to show which.

The row is modelled on this repository's one existing versioned artefact,
`app.persistence.planned_sessions.PlannedSessionIntentRow`, field for field:
``version`` / ``as_of`` / ``superseded_by`` / ``recompute_reason``, a
``UniqueConstraint(session_id, version)``, and an ``artefact_id`` property so
the row satisfies `app.domain.versioning.VersionRecord` structurally and the
domain's chain helpers work on it unchanged.

**What is a column and what is JSON.** The metrics themselves are one JSON
payload: the set will grow every work package, and nothing queries an
individual metric. The **pins** are columns, because those are what queries
will need — "recompute everything that used this FTP version" is the query the
versioning doctrine exists to make possible, and it cannot be a JSON scan. The
zone model is pinned per channel for the same reason A5.5 gives: without it,
every historical time-in-zone silently re-derives the day a second model
exists.

``superseded_by`` is deliberately **not** a foreign key: the old version and
its successor are written in one flush, and a self-referential FK would order
them for no benefit — the same reasoning the intent table records.
"""

import datetime as dt
import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import (
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.versioning import current_version
from app.domain.zones import ZoneModel
from app.persistence.db import Base, flush
from app.persistence.types import JSONColumn, UtcDateTime, enum_column

#: Longest a recompute reason may be. The same bound the intent chain uses.
MAX_REASON_LENGTH = 200


class SessionMetricsRow(Base):
    """One immutable version of one session's computed metrics."""

    __tablename__ = "session_metrics"
    __table_args__ = (
        # One row per (session, version). The chain is how the no-overwrite
        # rule is enforced, so a duplicate version number is a corruption of
        # it rather than a cosmetic problem.
        UniqueConstraint("session_id", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: No index of its own: the constraint above leads on this column, so
    #: "the versions of one session" already has one.
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE")
    )
    #: 1-based, strictly increasing within a session.
    version: Mapped[int] = mapped_column(Integer)
    #: When this version was computed (aware UTC).
    as_of: Mapped[dt.datetime] = mapped_column(UtcDateTime, server_default=func.now())
    #: Id of the version that replaced this one; NULL on the tip of the chain.
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    #: Why this version exists. NULL on version 1.
    recompute_reason: Mapped[str | None] = mapped_column(String(MAX_REASON_LENGTH))

    # --- the pins (A5.5) ------------------------------------------------
    #
    # All nullable: a session with no power pins no FTP, and an athlete with
    # no resting-HR anchor pins none. Real foreign keys because anchor history
    # is append-only and nothing ever deletes a version — the reference cannot
    # dangle, so saying so in the schema costs nothing and stops a fabricated
    # id from being stored as if it named something.
    ftp_anchor_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("anchor_versions.id")
    )
    lthr_anchor_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("anchor_versions.id")
    )
    max_hr_anchor_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("anchor_versions.id")
    )
    resting_hr_anchor_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("anchor_versions.id")
    )
    #: The zone model each channel's distribution was banded by; NULL when
    #: that channel produced no distribution.
    power_zone_model: Mapped[ZoneModel | None] = mapped_column(enum_column(ZoneModel))
    hr_zone_model: Mapped[ZoneModel | None] = mapped_column(enum_column(ZoneModel))

    #: The whole metric set, as `app.domain.session_analysis.analysis_to_json`
    #: renders it: every value beside its explanation, every absence as its
    #: reason.
    payload: Mapped[dict[str, object]] = mapped_column(JSONColumn, default=dict)

    @property
    def artefact_id(self) -> uuid.UUID:
        """The versioned artefact's stable identity: the session.

        Present so the row satisfies `app.domain.versioning.VersionRecord`
        and the domain's chain helpers apply to it unchanged.
        """
        return self.session_id


class SessionMetricsRepository:
    """SQLAlchemy repository for the metric artefact's version chain."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def history(self, session_id: uuid.UUID) -> Sequence[SessionMetricsRow]:
        """Every version of one session's metrics, oldest first."""
        result = await self._session.execute(
            select(SessionMetricsRow)
            .where(SessionMetricsRow.session_id == session_id)
            .order_by(SessionMetricsRow.version.asc())
        )
        return list(result.scalars())

    async def get_current(self, session_id: uuid.UUID) -> SessionMetricsRow | None:
        """The version in force, or ``None`` when nothing has been computed.

        Resolved through the domain's `current_version` over the whole chain
        rather than by ``ORDER BY version DESC LIMIT 1``: the two differ
        exactly when the chain is broken, and the domain's answer — ``None``,
        loudly — is the one that does not hide the corruption.
        """
        # pyrefly: ignore[bad-specialization]
        # The row satisfies `VersionRecord` at runtime; pyrefly does not see
        # through SQLAlchemy's `Mapped[X]` descriptors when structurally
        # matching a protocol. Same suppression, same reason, as the intent
        # chain's `current_intent`.
        return current_version(await self.history(session_id))

    async def current_for_sessions(
        self, session_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, SessionMetricsRow]:
        """The version in force for each of several sessions, in one query.

        For the list and week joins, where a per-session round trip would
        scale with what is on screen. Sessions with no artefact are simply
        absent from the result — an unanalysed session is not an error.
        """
        ids = list(session_ids)
        if not ids:
            return {}
        result = await self._session.execute(
            select(SessionMetricsRow).where(
                SessionMetricsRow.session_id.in_(ids),
                SessionMetricsRow.superseded_by.is_(None),
            )
        )
        current: dict[uuid.UUID, SessionMetricsRow] = {}
        for row in result.scalars():
            held = current.get(row.session_id)
            # A broken chain can leave more than one unsuperseded row; take
            # the highest version, which is what `current_version` would.
            if held is None or row.version > held.version:
                current[row.session_id] = row
        return current

    async def all_current(self) -> Sequence[SessionMetricsRow]:
        """The version in force for **every** session that has one.

        The scan behind "recompute everything that used this FTP version" —
        the query the pin columns exist to make possible (see the module
        docstring). Same broken-chain tolerance as
        :meth:`current_for_sessions`: of several unsuperseded rows, the
        highest version counts.
        """
        result = await self._session.execute(
            select(SessionMetricsRow).where(SessionMetricsRow.superseded_by.is_(None))
        )
        current: dict[uuid.UUID, SessionMetricsRow] = {}
        for row in result.scalars():
            held = current.get(row.session_id)
            if held is None or row.version > held.version:
                current[row.session_id] = row
        return list(current.values())

    async def add(self, row: SessionMetricsRow) -> SessionMetricsRow:
        """Persist one version and refresh its generated fields.

        Raises:
            ConflictError: When the write violates a database constraint —
                including two writers appending the same version number.
        """
        self._session.add(row)
        await flush(self._session)
        await self._session.refresh(row)
        return row
