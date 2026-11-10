"""Suggestion cards: what arc tells the athlete outside training, and how rarely.

A card is one thing worth the athlete's attention that is not a plan change —
a swap offer after a pain note, an import that needs a look, a welcome back
after a break. Cards are deliberately scarce: arc is a training log, not a
notification feed, so this module is mostly the rules that keep them calm.

* **One card is visible at a time.** Never a list to triage.
* **The budget** (:class:`Budget`) bounds how many *new* budgeted cards the
  athlete meets: one a day, three a plan week.
* **Outside the budget**: priority-1 (safety) cards, and the welcome-back
  kind. Safety cannot wait for Thursday, and a welcome back after a break is
  the card the break itself earned.
* **Priority order** is safety > today > sunday > digest; equal priorities
  surface in queue order (uuid7 ids), never by a wall-clock stamp.
* **Expiry** is seven days after the card was *first shown*.
* **Never during an active session** (:func:`visible`).

Everything here is pure: the service loads the queue, asks :func:`visible`,
stamps what it returned with :func:`mark_shown`, and persists the result.
"""

import datetime as dt
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from enum import IntEnum, StrEnum

#: How long a card stays answerable once the athlete has seen it.
CARD_LIFETIME = dt.timedelta(days=7)

#: The kind a welcome back after a break carries. Outside the budget.
WELCOME_BACK = "welcome_back"

#: Kinds that never spend the budget, whatever their priority.
UNBUDGETED_KINDS: frozenset[str] = frozenset({WELCOME_BACK})

#: Longest a card's headline and its "because" sentence may be. Short on
#: purpose: a card is read in a glance between two things the athlete is doing.
MAX_HEADLINE_CHARS = 120
MAX_BECAUSE_CHARS = 280

#: Longest a kind name may be.
MAX_KIND_CHARS = 60


class CardPriority(IntEnum):
    """How urgent a card is; lower surfaces first.

    ``SAFETY`` is priority 1 and outside the budget. ``TODAY`` is about the
    session in front of the athlete, ``SUNDAY`` about the week ahead, and
    ``DIGEST`` can wait for whenever there is room.
    """

    SAFETY = 1
    TODAY = 2
    SUNDAY = 3
    DIGEST = 4


class CardState(StrEnum):
    """Where a card stands.

    ``QUEUED`` has never been shown; ``SHOWN`` and ``SNOOZED`` have, and stay
    answerable until they expire. The last three are terminal.
    """

    QUEUED = "queued"
    SHOWN = "shown"
    SNOOZED = "snoozed"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


#: States a card can still be answered in.
OPEN_STATES: frozenset[CardState] = frozenset(
    {CardState.QUEUED, CardState.SHOWN, CardState.SNOOZED}
)


class CardAction(StrEnum):
    """The three answers. "Not now" in the UI is ``SNOOZE``."""

    ACCEPT = "accept"
    DISMISS = "dismiss"
    SNOOZE = "snooze"


_RESULT: dict[CardAction, CardState] = {
    CardAction.ACCEPT: CardState.ACCEPTED,
    CardAction.DISMISS: CardState.DISMISSED,
    CardAction.SNOOZE: CardState.SNOOZED,
}


@dataclass(frozen=True, slots=True)
class Card:
    """The budget's view of one card: no text, no payload, only what it counts."""

    id: uuid.UUID
    kind: str
    priority: CardPriority
    state: CardState
    first_shown: dt.datetime | None = None
    snoozed_until: dt.datetime | None = None

    @property
    def budgeted(self) -> bool:
        """Whether showing this card for the first time spends the budget."""
        return (
            self.priority is not CardPriority.SAFETY
            and self.kind not in UNBUDGETED_KINDS
        )

    @property
    def expires_at(self) -> dt.datetime | None:
        """Seven days after first display; None while never shown."""
        return None if self.first_shown is None else expires_at(self.first_shown)


@dataclass(frozen=True, slots=True)
class Budget:
    """How many *new* budgeted cards the athlete may meet.

    Counted by **first display**, per athlete-local day and per **plan week**
    — Monday 00:00 to Sunday 23:59 on the athlete's clock
    (`MATCHING__TIMEZONE`), the same week the calendar and the plan use. A
    rolling seven days was the alternative and was turned down: it makes
    "three a week" mean a different window every morning, so the athlete
    cannot tell why Thursday was quiet, and it disagrees with every other
    week arc shows. Sunday 23:59 and Monday 00:00 are different weeks.

    Expiry runs from first display for the same reason the budget does: the
    budget is about attention, and a card nobody has seen has asked for none.
    Counting from creation would let a queue that waited out a busy week
    expire unseen.

    A card shown on an earlier day and still unanswered (or back from a
    snooze) is not new, and does not spend today's budget again.
    """

    per_day: int = 1
    per_week: int = 3

    def allows_new(
        self, cards: Iterable[Card], *, now: dt.datetime, zone: dt.tzinfo
    ) -> bool:
        """Whether one more budgeted card may be shown for the first time at ``now``."""
        today = now.astimezone(zone).date()
        week = plan_week_of(now, zone)
        spent_today = spent_week = 0
        for card in cards:
            if not card.budgeted or card.first_shown is None:
                continue
            shown_on = card.first_shown.astimezone(zone).date()
            if plan_week_of(card.first_shown, zone) == week:
                spent_week += 1
            if shown_on == today:
                spent_today += 1
        return spent_today < self.per_day and spent_week < self.per_week


def plan_week_of(moment: dt.datetime, zone: dt.tzinfo) -> dt.date:
    """The Monday of the plan week ``moment`` falls in, on the athlete's clock."""
    local = moment.astimezone(zone).date()
    return local - dt.timedelta(days=local.weekday())


def expires_at(first_shown: dt.datetime) -> dt.datetime:
    """When a card first shown at ``first_shown`` stops being answerable."""
    return first_shown + CARD_LIFETIME


def effective_state(card: Card, now: dt.datetime) -> CardState:
    """The card's state at ``now``, with expiry applied before any sweep runs.

    The sweep writes ``expired`` hourly; this is what makes a card expired the
    moment its seven days are up, so it is listed nowhere and cannot be
    answered in the hour between. Only the old side of the window is checked
    (``now >= expires_at``): a stamp ahead of the reader's clock is skew, and
    skew only makes a card live a little longer.
    """
    if (
        card.state in OPEN_STATES
        and card.expires_at is not None
        and now >= card.expires_at
    ):
        return CardState.EXPIRED
    return card.state


def _order(card: Card) -> tuple[int, uuid.UUID]:
    return (int(card.priority), card.id)


def visible(
    cards: Sequence[Card],
    *,
    now: dt.datetime,
    zone: dt.tzinfo,
    training_active: bool,
    budget: Budget = Budget(),  # noqa: B008 — frozen, so one shared default is safe
) -> list[Card]:
    """The card the athlete sees at ``now``: one, or none.

    **Nothing while a session is active.** Between Start and Finish the
    athlete is lifting, and a card then — however quiet its styling — is a
    tap that is not a set. A quieter style during training was the
    alternative; it still asks for attention at the one time arc promises
    not to, so the answer is no card at all, safety included: a safety card
    waits the length of one session, and the session screen owns its own
    warnings.

    Otherwise the highest-priority open card that is not snoozed and not
    expired, provided it was shown before, is outside the budget, or the
    budget has room for a new one. Deterministic: asked twice with the same
    queue and clock, it names the same card, so a read that does not stamp
    (the coach's) agrees with one that does (the athlete's).
    """
    if training_active:
        return []
    open_cards = [
        card
        for card in cards
        if effective_state(card, now) in OPEN_STATES
        and not (card.snoozed_until is not None and now < card.snoozed_until)
    ]
    room = budget.allows_new(cards, now=now, zone=zone)
    eligible = [
        card
        for card in open_cards
        if card.first_shown is not None or not card.budgeted or room
    ]
    if not eligible:
        return []
    return [min(eligible, key=_order)]


def mark_shown(card: Card, *, now: dt.datetime) -> Card:
    """Record that ``card`` was put in front of the athlete at ``now``.

    The first display is kept forever — re-showing (after a snooze, or the
    next morning) never moves it, so neither the budget nor the expiry can
    be reset by looking again.
    """
    return replace(
        card,
        state=CardState.SHOWN,
        first_shown=card.first_shown or now,
        snoozed_until=None,
    )


def next_local_midnight(now: dt.datetime, zone: dt.tzinfo) -> dt.datetime:
    """The start of tomorrow on the athlete's clock, as an aware instant."""
    tomorrow = now.astimezone(zone).date() + dt.timedelta(days=1)
    return dt.datetime.combine(tomorrow, dt.time(), tzinfo=zone)


def act(card: Card, action: CardAction, *, now: dt.datetime, zone: dt.tzinfo) -> Card:
    """Answer ``card`` with ``action`` at ``now``.

    A snooze lasts until the athlete's next midnight and keeps
    ``first_shown``, so "not now" every morning still expires on day seven.

    Raises:
        ValueError: When the card is already answered or has expired. The
            message names the state, because the caller answered something
            it believed was still open.
    """
    state = effective_state(card, now)
    if state not in OPEN_STATES:
        raise ValueError(f"This card is already {state.value}; it cannot be answered.")
    return replace(
        card,
        state=_RESULT[action],
        first_shown=card.first_shown or now,
        snoozed_until=next_local_midnight(now, zone)
        if action is CardAction.SNOOZE
        else None,
    )
