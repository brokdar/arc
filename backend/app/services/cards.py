"""Suggestion cards: the use-cases behind the one slot on Today.

**Cards are a separate store with a kind registry; proposals remain the
plan-change kind.** A card is anything arc wants the athlete to see that is
not a change to the committed plan — a swap offer after a pain note, an import
that needs a look, a welcome back after a break. Widening the proposals union
(`app.domain.proposals.ChangeKind`) to carry them was the alternative and was
turned down: a proposal is a set of create/update/move/delete operations on
planned sessions, replayed atomically under concurrency tokens, and none of
that means anything for "your import has 3 sets without a weight". It would
also put every future card producer behind `services.proposals`'s contract,
which may not import wellness — and the first producers after this one read
pain notes. Pinned by `test_cards_api.py::test_proposals_untouched`.

So each card carries a ``kind``, and :func:`register_card_kind` names what
answering it *does*. A kind with no handler is a card whose answer is only
recorded (the answer itself, in the audit trail, is the information).
Creating a card of a kind nobody registered is refused: a card arc cannot act
on is a button that lies.

**What the athlete sees** is `app.domain.cards.visible`'s answer — one card or
none, inside the budget, never during an active session. :meth:`CardService.show`
stamps it as shown (that is what spends the budget and starts the seven-day
clock); :meth:`CardService.peek` answers the same question without writing, for
the coach's read key — a coach looking must not spend the athlete's budget.
"""

import datetime as dt
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self

from apscheduler.schedulers.base import BaseScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import athlete_zone
from app.core.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.scheduler import add_sweep_job, run_sweep
from app.domain.actor import Actor
from app.domain.cards import (
    WELCOME_BACK,
    Budget,
    Card,
    CardAction,
    CardPriority,
    CardState,
    act,
    mark_shown,
    visible,
)
from app.domain.safety import RED_FLAG_FOLLOW_UP, SWAP_OFFER, card_allowed
from app.domain.swaps import SWAP_REST_OF_BLOCK
from app.persistence.athlete import AthleteRepository
from app.persistence.audit import AuditRepository
from app.persistence.cards import CardRepository, CardRow
from app.persistence.db import commit, session_scope

ENTITY_TYPE = "card"
EXPIRY_JOB_ID = "card_expiry"

#: What accepting a card of one kind does, inside the answering transaction.
#: It must not commit: :meth:`CardService.act` commits once, after the audit row.
CardHandler = Callable[[AsyncSession, CardRow, Actor], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class CardKind:
    """One registered kind: its name and what accepting it does."""

    name: str
    on_accept: CardHandler | None = None


_REGISTRY: dict[str, CardKind] = {}


def register_card_kind(name: str, *, on_accept: CardHandler | None = None) -> CardKind:
    """Register (or replace) the kind ``name``. Producers call this at import."""
    kind = CardKind(name=name, on_accept=on_accept)
    _REGISTRY[name] = kind
    return kind


def card_kinds() -> Mapping[str, CardKind]:
    """Every registered kind, by name."""
    return dict(_REGISTRY)


#: The two kinds this module ships: a welcome back (outside the budget, see
#: `app.domain.cards`) and a plain note, whose answer is only recorded.
NOTE = "note"
register_card_kind(WELCOME_BACK)
register_card_kind(NOTE)


async def _resolve_red_flag(session: AsyncSession, row: CardRow, actor: Actor) -> None:
    """Accepting the red flag's follow-up says "I am well again": lower it."""
    # Imported here: `app.services.safety` queues cards through this module.
    from app.services.safety import SafetyService  # noqa: PLC0415

    await SafetyService.from_session(session).resolve_red_flag(actor=actor)


async def _remember_swap(session: AsyncSession, row: CardRow, actor: Actor) -> None:
    """Accepting a swap card makes its swap the pick offered first next time.

    That is all it does: neither card edits the plan. The pain card's offer is
    for "next time", and a rest-of-block swap is a request the coach turns
    into a plan change through a proposal (`app.domain.swaps.SwapScope`).
    """
    # Imported here: `app.services.exercise_preferences` sits above cards in
    # the services this module is imported by.
    from app.services.exercise_preferences import (  # noqa: PLC0415
        ExercisePreferencesService,
    )

    original = row.payload.get("exercise_id")
    pick = row.payload.get("swap_exercise_id")
    if isinstance(original, str) and isinstance(pick, str):
        await ExercisePreferencesService.from_session(session).remember(
            original, pick, actor=actor
        )


#: The safety kinds (`app.domain.safety`): accepting a swap offer remembers
#: its swap; the red flag's follow-up lowers the flag.
register_card_kind(SWAP_OFFER, on_accept=_remember_swap)
register_card_kind(RED_FLAG_FOLLOW_UP, on_accept=_resolve_red_flag)
#: The gym's "rest of block" swap (`app.domain.swaps`), pending until answered.
register_card_kind(SWAP_REST_OF_BLOCK, on_accept=_remember_swap)


def as_card(row: CardRow) -> Card:
    """The budget's view of a stored card."""
    return Card(
        id=row.id,
        kind=row.kind,
        priority=CardPriority(row.priority),
        state=row.state,
        first_shown=row.first_shown,
        snoozed_until=row.snoozed_until,
    )


def _budget() -> Budget:
    settings = get_settings().cards
    return Budget(per_day=settings.per_day, per_week=settings.per_week)


class CardService:
    """Create, show, answer and expire suggestion cards."""

    def __init__(
        self,
        session: AsyncSession,
        repository: CardRepository,
        audit: AuditRepository,
    ) -> None:
        self._session = session
        self._repository = repository
        self._audit = audit

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its repositories to one session."""
        return cls(session, CardRepository(session), AuditRepository(session))

    # --- writes --------------------------------------------------------------

    async def create(
        self,
        *,
        actor: Actor,
        kind: str,
        priority: CardPriority,
        headline: str,
        because: str,
        payload: dict[str, Any] | None = None,
    ) -> CardRow:
        """Queue one card. It is shown when the budget and the queue allow."""
        row = await self.queue(
            actor=actor,
            kind=kind,
            priority=priority,
            headline=headline,
            because=because,
            payload=payload,
        )
        await commit(self._session)
        return row

    async def queue(
        self,
        *,
        actor: Actor,
        kind: str,
        priority: CardPriority,
        headline: str,
        because: str,
        payload: dict[str, Any] | None = None,
    ) -> CardRow:
        """:meth:`create` without the commit, for a use-case that commits once.

        The outbox (`app.services.sync`) queues a swap offer in the middle of
        a batch that must stay all-or-nothing.
        """
        if kind not in _REGISTRY:
            raise ValidationError(f"Unknown card kind {kind!r}.")
        row = await self._repository.add(
            CardRow(
                kind=kind,
                priority=int(priority),
                state=CardState.QUEUED,
                headline=headline,
                because=because,
                payload=payload or {},
                created_by=str(actor),
            )
        )
        await self._audit.record(
            actor=actor,
            action="card.created",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload={"kind": kind, "priority": int(priority)},
        )
        return row

    async def show(
        self, *, actor: Actor, now: dt.datetime | None = None
    ) -> Sequence[CardRow]:
        """The visible card, stamped as shown — the athlete's read.

        Stamping is what makes the budget count and the expiry clock start, so
        it happens here, where the card is put in front of the athlete, and
        only the first display writes an audit row.
        """
        moment = now or dt.datetime.now(dt.UTC)
        rows, chosen = await self._choose(moment)
        for card in chosen:
            row = rows[card.id]
            first = row.first_shown is None
            stamped = mark_shown(card, now=moment)
            self._apply(row, stamped)
            await self._repository.add(row)
            if first:
                await self._audit.record(
                    actor=actor,
                    action="card.shown",
                    entity_type=ENTITY_TYPE,
                    entity_id=row.id,
                    payload={"kind": row.kind, "expires_at": _iso(row.expires_at)},
                )
        if chosen:
            await commit(self._session)
        return [rows[card.id] for card in chosen]

    async def act(
        self,
        card_id: uuid.UUID,
        action: CardAction,
        *,
        actor: Actor,
        now: dt.datetime | None = None,
    ) -> CardRow:
        """Answer one card: accept, dismiss or snooze ("Not now").

        Raises:
            NotFoundError: No such card.
            ConflictError: The card was already answered or has expired.
        """
        moment = now or dt.datetime.now(dt.UTC)
        row = await self._repository.get(card_id)
        if row is None:
            raise NotFoundError(f"Card {card_id} not found")
        try:
            answered = act(as_card(row), action, now=moment, zone=athlete_zone())
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
        self._apply(row, answered)
        if action is not CardAction.SNOOZE:
            row.resolved_at = moment
        await self._repository.add(row)
        handler = _REGISTRY.get(row.kind)
        if action is CardAction.ACCEPT and handler is not None and handler.on_accept:
            await handler.on_accept(self._session, row, actor)
        await self._audit.record(
            actor=actor,
            action=f"card.{answered.state.value}",
            entity_type=ENTITY_TYPE,
            entity_id=row.id,
            payload={"kind": row.kind, "action": action.value},
        )
        await commit(self._session)
        return row

    async def expire(
        self, *, actor: Actor, now: dt.datetime | None = None
    ) -> Sequence[CardRow]:
        """Expire every open card whose seven days since first display are up.

        Idempotent and batched, oldest deadline first, like the proposal sweep.
        Reads already treat these cards as expired; this writes it down.
        """
        moment = now or dt.datetime.now(dt.UTC)
        due = await self._repository.expired(
            now=moment, limit=get_settings().cards.expiry_batch
        )
        for row in due:
            row.state = CardState.EXPIRED
            row.resolved_at = moment
            await self._repository.add(row)
            await self._audit.record(
                actor=actor,
                action="card.expired",
                entity_type=ENTITY_TYPE,
                entity_id=row.id,
                payload={"kind": row.kind, "expires_at": _iso(row.expires_at)},
            )
        if due:
            await commit(self._session)
        return due

    # --- reads ---------------------------------------------------------------

    async def peek(self, *, now: dt.datetime | None = None) -> Sequence[CardRow]:
        """The card :meth:`show` would return at ``now``, writing nothing."""
        rows, chosen = await self._choose(now or dt.datetime.now(dt.UTC))
        return [rows[card.id] for card in chosen]

    # --- internals -----------------------------------------------------------

    async def _choose(
        self, moment: dt.datetime
    ) -> tuple[dict[uuid.UUID, CardRow], list[Card]]:
        rows = {
            row.id: row for row in await self._repository.budget_relevant(now=moment)
        }
        # A red flag blocks every card but its own follow-up until it is
        # resolved (`app.domain.safety`). Filtered before `visible`: under the
        # flag the one candidate kind is priority 1 and outside the budget, and
        # without it only follow-ups — also outside the budget — are dropped,
        # so the budget's count is never the poorer for the filter.
        athlete = await AthleteRepository(self._session).get()
        flagged = athlete is not None and athlete.red_flag_active
        chosen = visible(
            [
                as_card(row)
                for row in rows.values()
                if card_allowed(row.kind, red_flag_active=flagged)
            ],
            now=moment,
            zone=athlete_zone(),
            training_active=await self._repository.training_active(),
            budget=_budget(),
        )
        return rows, chosen

    @staticmethod
    def _apply(row: CardRow, card: Card) -> None:
        row.state = card.state
        row.first_shown = card.first_shown
        row.expires_at = card.expires_at
        row.snoozed_until = card.snoozed_until


def _iso(moment: dt.datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


# --- wiring ---------------------------------------------------------------------


async def run_card_expiry() -> None:
    """The scheduled sweep. Never raises — a failed run must not kill the job."""

    async def sweep() -> int:
        async with session_scope() as session:
            expired = await CardService.from_session(session).expire(
                actor=Actor.system()
            )
        return len(expired)

    await run_sweep("card_expiry", sweep)


def register_card_expiry_job(scheduler: BaseScheduler) -> None:
    """Register the card expiry sweep, on the proposal sweep's shared shape."""
    add_sweep_job(
        scheduler,
        run_card_expiry,
        job_id=EXPIRY_JOB_ID,
        seconds=get_settings().cards.expiry_interval_seconds,
    )
