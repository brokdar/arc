"""Agent notes: writing interpretive text, and answering it (WP-8.5).

Two use-cases with opposite permissions, which is the whole design:

* **the agent writes** — evaluations and annotations are the coach's words,
  and there is no athlete-side create. An athlete writing a "coach note" would
  make attribution meaningless the first time it happened, and the athlete
  already has places to write in their own voice (session notes, rejection
  reasons, declaration reasons).
* **the athlete disputes** — one tap, up or down, overwritable, and the agent
  cannot touch it. A model that could rate its own output would be grading its
  own homework, and this rating is the seed of the coach-quality loop.

Neither of those is enforced in the MCP shell; both are here, for the reason
`app.services.guardrails` gives.
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ForbiddenError,
    NotFoundError,
    ValidationError,
    domain_rules,
)
from app.domain.actor import Actor, ActorKind
from app.domain.agent_notes import (
    DisputeRating,
    NoteKind,
    check_plan_week,
    clean_model_id,
    clean_text,
    parse_cites,
)
from app.persistence.agent_notes import AgentNoteRepository, AgentNoteRow
from app.persistence.audit import AuditRepository
from app.persistence.db import commit, refresh
from app.services.activity import SessionService
from app.services.guardrails import check_write_cap, is_agent

#: `entity_type` written on this use-case's audit rows.
ENTITY_TYPE = "agent_note"


class AgentNoteService:
    """Use-cases for interpretive notes. Raises AppError subclasses."""

    def __init__(
        self,
        session: AsyncSession,
        repository: AgentNoteRepository,
        audit: AuditRepository,
        sessions: SessionService,
    ) -> None:
        self._session = session
        self._repository = repository
        self._audit = audit
        self._sessions = sessions

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(
            session,
            AgentNoteRepository(session),
            AuditRepository(session),
            SessionService.from_session(session),
        )

    # --- reads ---------------------------------------------------------------

    async def get(self, note_id: uuid.UUID) -> AgentNoteRow:
        """Return one note.

        Raises:
            NotFoundError: When no note has that id.
        """
        row = await self._repository.get(note_id)
        if row is None:
            raise NotFoundError(f"Agent note {note_id} not found")
        return row

    async def list(
        self,
        *,
        session_id: uuid.UUID | None = None,
        plan_week: dt.date | None = None,
    ) -> Sequence[AgentNoteRow]:
        """Return every note about one session or one plan week, oldest first.

        Unpaged on purpose: a note is written about a subject, and the number
        of things worth saying about one session or one week is small. If that
        stops being true the fix is a limit on writing, not a page on reading.

        Raises:
            ValidationError: When the two targets are not exactly one.
        """
        _check_one_target(session_id, plan_week)
        if session_id is not None:
            return await self._repository.for_session(session_id)
        assert plan_week is not None  # noqa: S101 — narrowing, checked above
        with domain_rules():
            check_plan_week(plan_week)
        return await self._repository.for_week(plan_week)

    # --- writing one ----------------------------------------------------------

    async def create(
        self,
        *,
        actor: Actor,
        kind: NoteKind,
        text: str,
        model_id: str,
        session_id: uuid.UUID | None = None,
        plan_week: dt.date | None = None,
        cites: Sequence[str | uuid.UUID] = (),
        dry_run: bool = False,
    ) -> AgentNoteRow:
        """Write a note about a session or a plan week.

        **The dry run returns the note it would have written**, as a transient
        row that is never added to the session — so the answer to "what would
        this do" is the same object as the answer to "what did it do", and the
        two cannot drift. Its ``id`` and ``created_at`` are unset, which is the
        honest rendering of a row that does not exist. Like every dry run here,
        it costs no rate-cap budget, because checking before acting must be
        the cheap option.

        Args:
            actor: Who is writing. **Agents only** — see the module docstring.
            kind: An evaluation (of one session) or an annotation.
            text: The note. Stored verbatim, parsed by nothing.
            model_id: Which model wrote it. Required.
            session_id: The session it is about, or None.
            plan_week: The Monday of the week it is about, or None. Exactly
                one of this and ``session_id``.
            cites: Artefact ids the note rests on; may be empty.
            dry_run: Validate and return the note without writing it.

        Returns:
            The stored note, or the transient one a dry run would have stored.

        Raises:
            ForbiddenError: When the actor is not an agent.
            RateLimitedError: When the agent's trailing-hour cap is spent.
            ValidationError: When the targets are not exactly one, the text or
                attribution is empty, a citation is not a uuid, an evaluation
                is aimed at a week, or ``plan_week`` is not a Monday.
            NotFoundError: When ``session_id`` names no session.
        """
        if not is_agent(actor):
            raise ForbiddenError(
                "Agent notes are the coaching agent's words and are attributed "
                "to a model. The athlete's own commentary belongs on the "
                "session itself, not in a note signed by someone else."
            )
        if not dry_run:
            await check_write_cap(self._session, actor)

        _check_one_target(session_id, plan_week)
        if kind is NoteKind.EVALUATION and session_id is None:
            # An evaluation is a read of what the athlete *did*, and a week is
            # not a thing anyone did. Commentary about a week is an annotation.
            raise ValidationError(
                "An evaluation is about one session, so it needs session_id. "
                "Use an annotation to comment on a plan week."
            )
        with domain_rules():
            body = clean_text(text)
            author = clean_model_id(model_id)
            citations = parse_cites(cites)
            if plan_week is not None:
                check_plan_week(plan_week)
        if session_id is not None:
            # Raises NotFoundError. A note about a session that does not exist
            # is unreachable by every read there is, so writing one is a
            # silent no-op dressed as a success.
            await self._sessions.get(session_id)

        row = AgentNoteRow(
            session_id=session_id,
            plan_week=plan_week,
            kind=kind,
            text=body,
            model_id=author,
            created_by=str(actor),
            cites=[str(value) for value in citations],
        )
        if dry_run:
            return row

        row = await self._repository.add(row)
        await self._audit.record(
            actor=actor,
            action="agent_note.created",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload={
                "kind": kind.value,
                "model_id": author,
                "session_id": None if session_id is None else str(session_id),
                "plan_week": None if plan_week is None else plan_week.isoformat(),
                "cites": row.cites,
                "chars": len(body),
            },
        )
        await commit(self._session)
        await refresh(self._session, row)
        return row

    # --- answering one --------------------------------------------------------

    async def dispute(
        self,
        note_id: uuid.UUID,
        *,
        actor: Actor,
        rating: DisputeRating | None,
    ) -> AgentNoteRow:
        """Set, change or clear the athlete's rating of a note.

        **Overwrite, not append.** A dispute is a toggle on a card: the athlete
        taps it, taps the other one a second later because they misread the
        note, and taps it off again. Storing that as a chain would make the
        history of a mis-tap look like a change of mind, and nothing reads
        this except an aggregate over *current* opinion. What actually
        happened is in the audit log, which is where the evidence belongs.

        ``None`` clears it — the third state of a two-state toggle is "not
        answered", and an athlete who cannot take a rating back will stop
        giving them.

        Args:
            note_id: The note being rated.
            actor: Who is rating. **The athlete only.**
            rating: Up, down, or None to clear.

        Raises:
            ForbiddenError: When the actor is not the athlete.
            NotFoundError: When no note has that id.
        """
        if actor.kind is not ActorKind.ATHLETE:
            raise ForbiddenError(
                "Only the athlete disputes a note: this rating is the signal "
                "the coach is measured by, and a model rating its own output "
                "measures nothing."
            )
        row = await self.get(note_id)
        row.dispute = rating
        row.disputed_at = None if rating is None else dt.datetime.now(dt.UTC)
        row = await self._repository.add(row)
        await self._audit.record(
            actor=actor,
            action="agent_note.disputed",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload={
                "rating": None if rating is None else rating.value,
                "model_id": row.model_id,
                "written_by": row.created_by,
            },
        )
        await commit(self._session)
        await refresh(self._session, row)
        return row


def _check_one_target(session_id: uuid.UUID | None, plan_week: dt.date | None) -> None:
    """Refuse a note that is about both things or neither.

    The database says this too (``ck_agent_notes_one_target``), but a
    constraint violation surfaces as a 409 about a constraint name, and the
    caller most likely to get this wrong is a language model reading the
    error. So it is checked here first, in words.

    Raises:
        ValidationError: When the targets are not exactly one.
    """
    if session_id is not None and plan_week is not None:
        raise ValidationError(
            "A note is about a session or about a plan week, not both. Pass "
            "session_id or plan_week, not the two together."
        )
    if session_id is None and plan_week is None:
        raise ValidationError(
            "A note needs a subject: pass session_id for a session, or "
            "plan_week (a Monday) for a week."
        )
