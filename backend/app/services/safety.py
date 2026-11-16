"""The gym's safety use-cases: what a pain note or a red flag does on the server.

The rules are `app.domain.safety`'s; this module applies them to rows. None of
these methods commits: each runs inside the outbox batch that carried the
note (`app.services.sync`) or the card answer (`app.services.cards`) or the
profile edit (`app.services.athlete`), and that use-case commits once.

* **Sharp pain** on a set queues one priority-1 swap offer naming the
  exercise, the joint and a swap; taking the note back (or deleting the set)
  withdraws that card again — deleted, not dismissed, because the athlete
  never said it, and audited as ``card.withdrawn``. Today's remaining sets
  are the phone's to drop (it may have no signal); the server never edits the
  plan line (`app.domain.safety`, follow-exactly).
* **A red flag** from the gym raises the athlete's flag with its kind and a
  grade, writes the symptom as the note, and queues the follow-up card — the
  only card shown until the flag is lowered. Lowering it, from that card or
  from the profile, closes every open follow-up.
"""

import datetime as dt
import uuid
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.activity import PainBand, PainKind
from app.domain.actor import Actor
from app.domain.cards import CardPriority, CardState
from app.domain.safety import (
    RED_FLAG_FOLLOW_UP,
    RED_FLAG_GROUP,
    RED_FLAG_SEVERITY,
    SWAP_OFFER,
    SYMPTOM_TEXT,
    RedFlagSymptom,
    follow_up_card,
    joint_of,
    pain_card_text,
    stops_exercise,
)
from app.domain.swaps import pain_swap
from app.persistence.athlete import SINGLETON_ATHLETE_ID, Athlete, AthleteRepository
from app.persistence.audit import AuditRepository
from app.persistence.cards import CardRepository
from app.services.cards import CardService
from app.services.exercise_preferences import ExercisePreferencesService

ATHLETE_ENTITY = "athlete"
CARD_ENTITY = "card"


class SafetyService:
    """Pain notes and red flags. Never commits — see the module docstring."""

    def __init__(
        self,
        session: AsyncSession,
        cards: CardService,
        card_rows: CardRepository,
        athletes: AthleteRepository,
        preferences: ExercisePreferencesService,
        audit: AuditRepository,
    ) -> None:
        self._session = session
        self._cards = cards
        self._card_rows = card_rows
        self._athletes = athletes
        self._preferences = preferences
        self._audit = audit

    @classmethod
    def from_session(cls, session: AsyncSession) -> Self:
        """Wire the service and its collaborators to one session."""
        return cls(
            session,
            CardService.from_session(session),
            CardRepository(session),
            AthleteRepository(session),
            ExercisePreferencesService.from_session(session),
            AuditRepository(session),
        )

    # --- pain ----------------------------------------------------------------

    async def pain_changed(
        self,
        *,
        set_client_id: uuid.UUID,
        session_id: uuid.UUID,
        before: PainKind | None,
        after: PainKind | None,
        exercise_id: str | None,
        exercise_name: str | None,
        band: PainBand | None,
        actor: Actor,
    ) -> None:
        """React to one set's pain kind moving from ``before`` to ``after``.

        Only a crossing matters: into sharp queues the swap offer, out of it
        withdraws the offer. Sharp re-asserted (a new band, a replay) queues
        nothing more — one painful set is one card.

        The caller must have seeded the catalogue already (the outbox does,
        before its first write), so the lookup here never commits.
        """
        was, now = stops_exercise(before), stops_exercise(after)
        if was == now:
            return
        if now:
            await self._offer_swap(
                set_client_id=set_client_id,
                session_id=session_id,
                exercise_id=exercise_id,
                exercise_name=exercise_name,
                band=band,
                actor=actor,
            )
        else:
            await self._withdraw_swap(set_client_id=set_client_id, actor=actor)

    async def _offer_swap(
        self,
        *,
        set_client_id: uuid.UUID,
        session_id: uuid.UUID,
        exercise_id: str | None,
        exercise_name: str | None,
        band: PainBand | None,
        actor: Actor,
    ) -> None:
        library = await self._preferences.library()
        exercise = None if exercise_id is None else library.domain.get(exercise_id)
        joint: str | None = None
        swap_id: str | None = None
        swap_name: str | None = None
        name = exercise_name or exercise_id or "this exercise"
        if exercise is not None:
            name = exercise.name
            found = joint_of(exercise)
            joint = None if found is None else found[0]
            # The same ranking the packet and the coach's tool use, so the
            # card never offers what the athlete avoids or cannot do today.
            swap = pain_swap(exercise, library.domain.values(), library.context)
            if swap is not None:
                swap_id, swap_name = swap.id, swap.name
        headline, because = pain_card_text(name, joint=joint, swap_name=swap_name)
        await self._cards.queue(
            actor=actor,
            kind=SWAP_OFFER,
            priority=CardPriority.SAFETY,
            headline=headline,
            because=because,
            payload={
                "set_client_id": str(set_client_id),
                "session_id": str(session_id),
                "exercise_id": exercise_id,
                "swap_exercise_id": swap_id,
                "joint": joint,
                "pain_band": None if band is None else band.value,
            },
        )

    async def _withdraw_swap(self, *, set_client_id: uuid.UUID, actor: Actor) -> None:
        for card in await self._card_rows.open_of_kind(SWAP_OFFER):
            if card.payload.get("set_client_id") != str(set_client_id):
                continue
            await self._audit.record(
                actor=actor,
                action="card.withdrawn",
                entity_type=CARD_ENTITY,
                entity_id=card.id,
                payload={"kind": card.kind, "set_client_id": str(set_client_id)},
            )
            await self._card_rows.delete(card)

    # --- red flags -----------------------------------------------------------

    async def raise_red_flag(
        self, symptom: RedFlagSymptom, *, session_id: uuid.UUID, actor: Actor
    ) -> Athlete:
        """Raise the athlete's flag from *Stop — something's wrong*.

        A flag already up is overwritten by this one: the gym answer is the
        newest and most specific statement there is.
        """
        kind = RED_FLAG_GROUP[symptom]
        athlete = await self._athletes.get() or Athlete(id=SINGLETON_ATHLETE_ID)
        before = {
            "red_flag_active": athlete.red_flag_active,
            "red_flag_kind": _value(athlete.red_flag_kind),
            "red_flag_severity": _value(athlete.red_flag_severity),
        }
        athlete.red_flag_active = True
        athlete.red_flag_kind = kind
        athlete.red_flag_severity = RED_FLAG_SEVERITY[kind]
        athlete.red_flag_note = f"Stopped a gym session: {SYMPTOM_TEXT[symptom]}."
        athlete = await self._athletes.add(athlete)
        await self._audit.record(
            actor=actor,
            action="athlete.red_flag_raised",
            entity_type=ATHLETE_ENTITY,
            entity_id=athlete.id,
            payload={
                "before": before,
                "symptom": symptom.value,
                "kind": kind.value,
                "session_id": str(session_id),
            },
        )
        headline, because = follow_up_card(symptom)
        await self._cards.queue(
            actor=actor,
            kind=RED_FLAG_FOLLOW_UP,
            priority=CardPriority.SAFETY,
            headline=headline,
            because=because,
            payload={
                "symptom": symptom.value,
                "kind": kind.value,
                "session_id": str(session_id),
            },
        )
        return athlete

    async def resolve_red_flag(self, *, actor: Actor) -> None:
        """Lower the flag ("I am well again") and close its follow-ups."""
        athlete = await self._athletes.get()
        if athlete is not None and athlete.red_flag_active:
            kind = _value(athlete.red_flag_kind)
            athlete.red_flag_active = False
            athlete.red_flag_kind = None
            athlete.red_flag_severity = None
            athlete.red_flag_note = None
            await self._athletes.add(athlete)
            await self._audit.record(
                actor=actor,
                action="athlete.red_flag_resolved",
                entity_type=ATHLETE_ENTITY,
                entity_id=athlete.id,
                payload={"kind": kind},
            )
        await self.close_follow_ups(actor=actor)

    async def close_follow_ups(
        self, *, actor: Actor, now: dt.datetime | None = None
    ) -> None:
        """Dismiss every open follow-up: the flag it asks about is down."""
        moment = now or dt.datetime.now(dt.UTC)
        for card in await self._card_rows.open_of_kind(RED_FLAG_FOLLOW_UP):
            card.state = CardState.DISMISSED
            card.resolved_at = moment
            card.snoozed_until = None
            await self._card_rows.add(card)
            await self._audit.record(
                actor=actor,
                action="card.dismissed",
                entity_type=CARD_ENTITY,
                entity_id=card.id,
                payload={"kind": card.kind, "reason": "red flag lowered"},
            )


def _value(member: object) -> str | None:
    return None if member is None else str(getattr(member, "value", member))
