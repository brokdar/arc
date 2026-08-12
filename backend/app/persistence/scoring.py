"""The scoring artefacts: alignment versions, score versions, and testimony.

Four tables, and the line between them is who wrote them.

**Two are computed and versioned.** `session_alignments` and `session_scores`
are derived artefacts under invariant 1: nothing is ever updated in place, a
recomputation appends version *n+1* and closes the old one off, and every
version stays readable with the inputs it was computed against. Both carry
WP-1's vocabulary verbatim (``version`` / ``as_of`` / ``superseded_by`` /
``recompute_reason``) and satisfy `app.domain.versioning.VersionRecord`
structurally, so the domain's chain helpers work on them unchanged — the same
shape `app.persistence.metrics.SessionMetricsRow` already uses.

**Two are testimony.** `verdict_declarations` holds what the *athlete* said
the session was, and `session_reasons` holds why. They are deliberately **not**
on the score chain: a rescore rewrites the machine's opinion, and if the
declaration lived beside it every recomputation would overwrite the athlete's
words. That is exactly the failure WP-7.4 names when it says a differing
suggestion sets ``contested`` and *never changes the declaration*. Two tables,
one rule: nothing but the athlete writes a declaration, and reasons are
append-only versions rather than an editable row.

**Reasons serve two shapes** (WP-7.3). A matched session's reasons hang off the
verdict declaration; a **missed** session has no declaration to hang from — the
athlete answers an evening prompt about the planned session instead — so the
row points at one or the other and a check constraint keeps it from pointing at
both or neither. One model, because "why did that not go to plan" is one
question and a client that had to merge two tables to answer it would.
"""

import datetime as dt
import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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

from app.domain.scoring import Verdict
from app.domain.versioning import current_version
from app.persistence.db import Base, flush
from app.persistence.types import JSONColumn, UtcDateTime, enum_column

#: Longest a recompute (or revision) reason may be. The same bound the metric
#: and intent chains use.
MAX_REASON_LENGTH = 200

#: Longest stored actor string — the width `audit_log.actor` uses.
MAX_ACTOR_LENGTH = 120

#: Longest free-text note beside a set of reasons. Mirrors
#: `app.domain.scoring.MAX_REASON_NOTE_CHARS`.
MAX_NOTE_LENGTH = 1_000


class SessionAlignmentRow(Base):
    """One immutable version of how a recording lines up with a prescription.

    Persisted from WP-7 rather than WP-5 because an alignment describes a
    *match*, and matches did not exist until WP-6. The offset is the
    reason it is versioned at all (A7.1): the athlete slides the planned
    timeline, that creates version *n+1*, and every score records which version
    it was computed against — so a score taken before the correction stays
    explicable after it.
    """

    __tablename__ = "session_alignments"
    __table_args__ = (
        # One row per (session, version). The chain is how the no-overwrite
        # rule is enforced.
        UniqueConstraint("session_id", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: No index of its own: the constraint above leads on this column.
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE")
    )
    #: The prescription this alignment is against. Nulled rather than cascaded
    #: if the planned session is deleted: the alignment still records what was
    #: compared, and destroying an artefact to tidy a reference would break
    #: invariant 1.
    planned_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("planned_sessions.id", ondelete="SET NULL"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    as_of: Mapped[dt.datetime] = mapped_column(UtcDateTime, server_default=func.now())
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    recompute_reason: Mapped[str | None] = mapped_column(String(MAX_REASON_LENGTH))
    #: Seconds the planned timeline was slid by before the assignment was made
    #: (A7.1). Positive means the workout began *later* than the recording did.
    offset_s: Mapped[int] = mapped_column(Integer, default=0)
    #: `app.domain.alignment.Alignment` as JSON: the pairs kept, the pairs the
    #: confidence gate refused, and both sides' leftovers.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    created_by: Mapped[str] = mapped_column(String(MAX_ACTOR_LENGTH))

    @property
    def artefact_id(self) -> uuid.UUID:
        """The versioned artefact's identity: the completed session.

        Not the (session, planned session) pair, because the MVP allows one
        link per session — so the session names the pair exactly. When
        set-to-set matching arrives the artefact identity is what widens, and
        the chain helpers keep working either way.
        """
        return self.session_id


class SessionScoreRow(Base):
    """One immutable version of one session's score (WP-7.4).

    Everything the score was computed *from* is recorded beside it, because
    "why does this session read `under`" has to be answerable a year later
    against inputs that have all moved since: the intent version, the anchor
    versions that intent pinned, the metric artefact the numbers came from and
    the alignment version the steps were paired by.
    """

    __tablename__ = "session_scores"
    __table_args__ = (UniqueConstraint("session_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: No index of its own: the constraint above leads on this column.
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE")
    )
    #: The planned session this score judged the recording against. Nulled, not
    #: cascaded, for the reason the alignment row gives.
    planned_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("planned_sessions.id", ondelete="SET NULL"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    as_of: Mapped[dt.datetime] = mapped_column(UtcDateTime, server_default=func.now())
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    recompute_reason: Mapped[str | None] = mapped_column(String(MAX_REASON_LENGTH))

    #: Which intent version was scored. A post-hoc edit writes intent n+1 and
    #: triggers a rescore, so this is what tells two score versions apart.
    intent_version: Mapped[int | None] = mapped_column(Integer)
    #: Anchor type -> anchor version id, both as strings — a copy of what the
    #: intent pinned, frozen here so the score says what it resolved against
    #: even after the intent chain moves on.
    pinned_anchor_versions: Mapped[dict[str, str]] = mapped_column(
        JSONColumn, default=dict
    )
    #: The metric artefact the recorded numbers came from. A real foreign key:
    #: metric versions are append-only and nothing deletes one.
    metrics_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("session_metrics.id")
    )
    #: The alignment version the aligned work steps came from (A7.1).
    alignment_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("session_alignments.id")
    )
    #: What the rule table suggested. A column rather than a JSON key because
    #: the contested check reads it on every rescore, and the week strip reads
    #: it for every card.
    suggested_verdict: Mapped[Verdict] = mapped_column(enum_column(Verdict))
    #: `app.domain.scoring.score_to_json`: every axis with its value or its
    #: reason, every criterion with its pass/fail detail.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    created_by: Mapped[str] = mapped_column(String(MAX_ACTOR_LENGTH))

    @property
    def artefact_id(self) -> uuid.UUID:
        """The versioned artefact's identity: the completed session."""
        return self.session_id


class VerdictDeclarationRow(Base):
    """What the athlete said the session was. Never written by anything else.

    One standing declaration per session — the athlete may change their mind,
    and the audit log is what keeps every version of that — but the *machine*
    never touches :attr:`declared_verdict`. A rescore that disagrees sets
    :attr:`contested` and stops there (WP-7.4).
    """

    __tablename__ = "verdict_declarations"
    __table_args__ = (UniqueConstraint("session_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: No index of its own: the constraint above leads on this column.
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE")
    )
    planned_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("planned_sessions.id", ondelete="SET NULL"), index=True
    )
    declared_verdict: Mapped[Verdict] = mapped_column(enum_column(Verdict))
    declared_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    #: What the machine was suggesting at the moment the athlete declared.
    #: Kept because it is the only way to tell an agreement from an override
    #: afterwards, and because the contested rule compares against it.
    suggested_at_declaration: Mapped[Verdict | None] = mapped_column(
        enum_column(Verdict)
    )
    #: The score version the athlete was looking at.
    score_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("session_scores.id")
    )
    #: Set when a later score suggests something that contradicts what the
    #: athlete declared *and* differs from what they ruled on. Surfaced, never
    #: acted on.
    contested: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    contested_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    #: The suggestion that contests the declaration; null while it stands.
    contested_verdict: Mapped[Verdict | None] = mapped_column(enum_column(Verdict))
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )


class SessionReasonsRow(Base):
    """One version of the reasons a session was not as intended (WP-7.3).

    Append-only: a revision is a **new row** at version *n+1* with the previous
    one closed off, never an edit. What the athlete said on Tuesday is part of
    the record even after Thursday's correction, and a scoring history that
    silently rewrote it would be worth nothing as testimony.

    Exactly one of :attr:`declaration_id` and :attr:`planned_session_id` is
    set — see the module docstring.
    """

    __tablename__ = "session_reasons"
    __table_args__ = (
        CheckConstraint(
            "(declaration_id IS NULL) <> (planned_session_id IS NULL)",
            name="one_subject",
        ),
        # One row per (subject, version), like both sibling chains — and
        # **two** constraints, because this chain has two possible subjects
        # and the version is numbered within whichever one the row names
        # (`SessionReasonsRepository.for_declaration` /
        # `for_planned_session` are what `next_version` counts). A
        # `(session_id, version)` key would be neither: there is no
        # `session_id` here, and the missed side has no session at all.
        # The check constraint leaves exactly one of the two columns non-null,
        # and NULLs are distinct in a unique index on both SQLite and
        # Postgres, so each constraint binds only the rows it is about.
        UniqueConstraint("declaration_id", "version"),
        UniqueConstraint("planned_session_id", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    #: The declaration these reasons explain, for a session that was done.
    #: No index of its own: the constraint above leads on this column.
    declaration_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("verdict_declarations.id", ondelete="CASCADE")
    )
    #: The planned session these reasons explain, for one that was missed —
    #: there is no declaration to hang them from. Indexed by its own
    #: constraint, as above.
    planned_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("planned_sessions.id", ondelete="CASCADE")
    )
    version: Mapped[int] = mapped_column(Integer)
    as_of: Mapped[dt.datetime] = mapped_column(UtcDateTime, server_default=func.now())
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    #: Why this revision was written. Null on version 1. (The name is WP-1's
    #: versioning vocabulary, which the chain helpers match structurally; for
    #: reasons it holds why the athlete revised rather than why a machine
    #: recomputed.)
    recompute_reason: Mapped[str | None] = mapped_column(String(MAX_REASON_LENGTH))
    #: `app.domain.scoring.Reason` values, **ordered by primacy** — the first
    #: is the main one. A list rather than rows because the order is the datum
    #: and one to three values is not a table.
    reasons: Mapped[list[str]] = mapped_column(JSONColumn, default=list)
    #: The athlete's own words beside the controlled list, never instead of it.
    note: Mapped[str | None] = mapped_column(String(MAX_NOTE_LENGTH))
    recorded_by: Mapped[str] = mapped_column(String(MAX_ACTOR_LENGTH))

    @property
    def artefact_id(self) -> uuid.UUID:
        """Whichever subject these reasons are about.

        Present so the row satisfies `app.domain.versioning.VersionRecord`; the
        check constraint guarantees exactly one of the two is set, so the
        fallback is unreachable rather than a default.
        """
        subject = self.declaration_id or self.planned_session_id
        if subject is None:  # pragma: no cover — the check constraint forbids it
            raise ValueError(f"reasons row {self.id} names no subject")
        return subject


# --- repositories ---------------------------------------------------------------


class SessionAlignmentRepository:
    """SQLAlchemy repository for the alignment artefact's version chain."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def history(self, session_id: uuid.UUID) -> Sequence[SessionAlignmentRow]:
        """Every version of one session's alignment, oldest first."""
        result = await self._session.execute(
            select(SessionAlignmentRow)
            .where(SessionAlignmentRow.session_id == session_id)
            .order_by(SessionAlignmentRow.version.asc())
        )
        return list(result.scalars())

    async def get_current(self, session_id: uuid.UUID) -> SessionAlignmentRow | None:
        """The alignment version in force, or ``None``.

        Through the domain's `current_version` rather than ``ORDER BY version
        DESC``: the two differ exactly when the chain is broken, and the
        domain's answer — ``None``, loudly — does not hide the corruption.
        """
        # pyrefly: ignore[bad-specialization]
        # The row satisfies `VersionRecord` at runtime; pyrefly does not see
        # through SQLAlchemy's `Mapped[X]` descriptors when structurally
        # matching a protocol. Same suppression, same reason, as the metric
        # chain's `get_current`.
        return current_version(await self.history(session_id))

    async def add(self, row: SessionAlignmentRow) -> SessionAlignmentRow:
        """Persist one version and refresh its generated fields.

        Raises:
            ConflictError: When the write violates a database constraint.
        """
        self._session.add(row)
        await flush(self._session)
        await self._session.refresh(row)
        return row


class SessionScoreRepository:
    """SQLAlchemy repository for the score artefact's version chain."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def history(self, session_id: uuid.UUID) -> Sequence[SessionScoreRow]:
        """Every version of one session's score, oldest first."""
        result = await self._session.execute(
            select(SessionScoreRow)
            .where(SessionScoreRow.session_id == session_id)
            .order_by(SessionScoreRow.version.asc())
        )
        return list(result.scalars())

    async def get_current(self, session_id: uuid.UUID) -> SessionScoreRow | None:
        """The score version in force, or ``None`` when nothing is scored."""
        # pyrefly: ignore[bad-specialization]
        # See `SessionAlignmentRepository.get_current`.
        return current_version(await self.history(session_id))

    async def current_for_sessions(
        self, session_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, SessionScoreRow]:
        """The version in force for each of several sessions, in one query.

        For the week strip, where a per-session round trip would scale with
        what is on screen.
        """
        ids = list(session_ids)
        if not ids:
            return {}
        result = await self._session.execute(
            select(SessionScoreRow).where(
                SessionScoreRow.session_id.in_(ids),
                SessionScoreRow.superseded_by.is_(None),
            )
        )
        current: dict[uuid.UUID, SessionScoreRow] = {}
        for row in result.scalars():
            held = current.get(row.session_id)
            # A broken chain can leave more than one unsuperseded row; take the
            # highest version, which is what `current_version` would.
            if held is None or row.version > held.version:
                current[row.session_id] = row
        return current

    async def add(self, row: SessionScoreRow) -> SessionScoreRow:
        """Persist one version and refresh its generated fields.

        Raises:
            ConflictError: When the write violates a database constraint —
                including two writers appending the same version number.
        """
        self._session.add(row)
        await flush(self._session)
        await self._session.refresh(row)
        return row


class VerdictDeclarationRepository:
    """SQLAlchemy repository for the athlete's declarations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_session(self, session_id: uuid.UUID) -> VerdictDeclarationRow | None:
        """The declaration standing on one session, or ``None``."""
        result = await self._session.execute(
            select(VerdictDeclarationRow).where(
                VerdictDeclarationRow.session_id == session_id
            )
        )
        return result.scalar_one_or_none()

    async def for_sessions(
        self, session_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, VerdictDeclarationRow]:
        """Declarations for several sessions, in one query, keyed by session."""
        ids = list(session_ids)
        if not ids:
            return {}
        result = await self._session.execute(
            select(VerdictDeclarationRow).where(
                VerdictDeclarationRow.session_id.in_(ids)
            )
        )
        return {row.session_id: row for row in result.scalars()}

    async def add(self, row: VerdictDeclarationRow) -> VerdictDeclarationRow:
        """Persist a declaration (new or revised) and refresh it.

        Raises:
            ConflictError: When the write violates a database constraint.
        """
        self._session.add(row)
        await flush(self._session)
        await self._session.refresh(row)
        return row


class SessionReasonsRepository:
    """SQLAlchemy repository for the append-only reasons chain."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_declaration(
        self, declaration_id: uuid.UUID
    ) -> Sequence[SessionReasonsRow]:
        """Every version of one declaration's reasons, oldest first."""
        result = await self._session.execute(
            select(SessionReasonsRow)
            .where(SessionReasonsRow.declaration_id == declaration_id)
            .order_by(SessionReasonsRow.version.asc())
        )
        return list(result.scalars())

    async def for_planned_session(
        self, planned_session_id: uuid.UUID
    ) -> Sequence[SessionReasonsRow]:
        """Every version of one missed session's reasons, oldest first."""
        result = await self._session.execute(
            select(SessionReasonsRow)
            .where(SessionReasonsRow.planned_session_id == planned_session_id)
            .order_by(SessionReasonsRow.version.asc())
        )
        return list(result.scalars())

    async def add(self, row: SessionReasonsRow) -> SessionReasonsRow:
        """Append one version and refresh its generated fields.

        Raises:
            ConflictError: When the write violates a database constraint.
        """
        self._session.add(row)
        await flush(self._session)
        await self._session.refresh(row)
        return row
