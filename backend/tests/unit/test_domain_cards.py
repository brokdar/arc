"""The card budget, as pure rules (AC-5, AC-6).

Every test drives `visible()` the way the service does: ask, stamp what was
shown (`mark_shown`), answer it (`act`), ask again on a later clock. The
budget is a promise about what the athlete *sees*, so the assertions are on
what `visible()` returns, never on a counter.
"""

import datetime as dt
import uuid
from dataclasses import replace
from zoneinfo import ZoneInfo

import pytest

from app.domain.cards import (
    CARD_LIFETIME,
    WELCOME_BACK,
    Budget,
    Card,
    CardAction,
    CardPriority,
    CardState,
    act,
    effective_state,
    expires_at,
    mark_shown,
    plan_week_of,
    visible,
)

ZONE = ZoneInfo("Europe/Berlin")

#: A Monday on the athlete's clock.
MONDAY = dt.date(2026, 11, 9)


def at(day: dt.date, hour: int = 8, minute: int = 0) -> dt.datetime:
    """An aware instant at ``hour:minute`` on ``day``, athlete-local."""
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZONE)


def day(offset: int) -> dt.date:
    """``offset`` days after `MONDAY`."""
    return MONDAY + dt.timedelta(days=offset)


def card(
    priority: CardPriority = CardPriority.DIGEST,
    kind: str = "note",
    **overrides: object,
) -> Card:
    """A queued card, never shown."""
    fields: dict[str, object] = {
        "id": uuid.uuid7(),
        "kind": kind,
        "priority": priority,
        "state": CardState.QUEUED,
    }
    fields.update(overrides)
    return Card(**fields)  # type: ignore[arg-type]


class Queue:
    """The service's loop over the domain, without the database."""

    def __init__(self, *cards: Card) -> None:
        self.cards = {c.id: c for c in cards}

    def show(self, now: dt.datetime, *, training_active: bool = False) -> list[Card]:
        """What the athlete sees at ``now``, stamped as shown."""
        shown = visible(
            list(self.cards.values()),
            now=now,
            zone=ZONE,
            training_active=training_active,
        )
        for c in shown:
            self.cards[c.id] = mark_shown(c, now=now)
        return [self.cards[c.id] for c in shown]

    def answer(self, target: Card, action: CardAction, now: dt.datetime) -> Card:
        """Act on one card at ``now``."""
        self.cards[target.id] = act(self.cards[target.id], action, now=now, zone=ZONE)
        return self.cards[target.id]


# --- AC-5: one visible, one new a day, three a plan week ----------------------


def test_five_budgeted_cards_surface_one_a_day_and_three_a_week() -> None:
    queue = Queue(*(card() for _ in range(5)))

    monday = queue.show(at(day(0), 9))
    assert len(monday) == 1
    queue.answer(monday[0], CardAction.ACCEPT, at(day(0), 9, 5))
    assert queue.show(at(day(0), 18)) == [], "one new budgeted card per day"

    tuesday = queue.show(at(day(1), 9))
    assert len(tuesday) == 1
    assert tuesday[0].id != monday[0].id
    queue.answer(tuesday[0], CardAction.DISMISS, at(day(1), 9, 5))

    wednesday = queue.show(at(day(2), 9))
    assert len(wednesday) == 1
    assert wednesday[0].id not in {monday[0].id, tuesday[0].id}
    queue.answer(wednesday[0], CardAction.ACCEPT, at(day(2), 9, 5))

    assert queue.show(at(day(3), 9)) == [], "three this plan week"
    assert queue.show(at(day(6), 23, 59)) == [], "still the same plan week"
    assert len(queue.show(at(day(7), 0, 1))) == 1, "a new plan week, a new budget"


def test_only_one_card_is_ever_visible() -> None:
    queue = Queue(card(CardPriority.SAFETY), card(CardPriority.SAFETY), card())

    assert len(queue.show(at(day(0)))) == 1


def test_an_unanswered_card_stays_the_visible_one_without_spending_budget() -> None:
    queue = Queue(card(), card())

    [first] = queue.show(at(day(0)))
    [again] = queue.show(at(day(1)))

    assert again.id == first.id
    assert again.first_shown == first.first_shown


def test_a_priority_one_card_is_visible_although_the_week_is_spent() -> None:
    queue = Queue(*(card() for _ in range(3)))
    for offset in range(3):
        [shown] = queue.show(at(day(offset)))
        queue.answer(shown, CardAction.ACCEPT, at(day(offset), 9))
    assert queue.show(at(day(3))) == []

    safety = card(CardPriority.SAFETY)
    queue.cards[safety.id] = safety

    assert [c.id for c in queue.show(at(day(3), 11))] == [safety.id]


def test_a_welcome_back_card_is_outside_the_budget() -> None:
    queue = Queue(card())
    [shown] = queue.show(at(day(0)))
    queue.answer(shown, CardAction.ACCEPT, at(day(0), 9))
    assert queue.show(at(day(0), 10)) == []

    welcome = card(CardPriority.TODAY, kind=WELCOME_BACK)
    queue.cards[welcome.id] = welcome

    assert [c.id for c in queue.show(at(day(0), 11))] == [welcome.id]
    assert not welcome.budgeted


def test_an_unbudgeted_card_does_not_spend_the_days_budget() -> None:
    welcome = card(CardPriority.TODAY, kind=WELCOME_BACK)
    budgeted = card()
    queue = Queue(welcome, budgeted)

    assert [c.id for c in queue.show(at(day(0)))] == [welcome.id]
    queue.answer(welcome, CardAction.ACCEPT, at(day(0), 9))

    assert [c.id for c in queue.show(at(day(0), 10))] == [budgeted.id]


def test_sunday_2359_and_monday_0000_are_different_plan_weeks() -> None:
    sunday = at(day(6), 23, 59)
    monday = at(day(7), 0, 0)

    assert plan_week_of(sunday, ZONE) == MONDAY
    assert plan_week_of(monday, ZONE) == day(7)
    # In UTC both instants are Sunday: the week is the athlete's, not the server's.
    assert monday.astimezone(dt.UTC).date() == day(6)


def test_the_week_boundary_resets_the_budget_in_the_athlete_zone() -> None:
    queue = Queue(*(card() for _ in range(4)))
    # Spend the week Friday to Sunday 23:59.
    for offset, (hour, minute) in zip(
        (4, 5, 6), ((9, 0), (9, 0), (23, 58)), strict=True
    ):
        [shown] = queue.show(at(day(offset), hour, minute))
        queue.answer(shown, CardAction.ACCEPT, at(day(offset), hour, minute))

    assert queue.show(at(day(6), 23, 59)) == [], "Sunday 23:59 is the spent week"
    assert len(queue.show(at(day(7), 0, 0))) == 1, "Monday 00:00 is a new one"


def test_priority_order_is_safety_today_sunday_digest() -> None:
    digest = card(CardPriority.DIGEST)
    sunday = card(CardPriority.SUNDAY)
    today = card(CardPriority.TODAY)
    safety = card(CardPriority.SAFETY)
    cards = [digest, sunday, today, safety]

    order = []
    remaining = list(cards)
    while remaining:
        [top] = visible(remaining, now=at(day(0)), zone=ZONE, training_active=False)
        order.append(top.id)
        remaining = [c for c in remaining if c.id != top.id]

    assert order == [safety.id, today.id, sunday.id, digest.id]


def test_equal_priorities_surface_in_queue_order() -> None:
    first, second = card(), card()

    [top] = visible([second, first], now=at(day(0)), zone=ZONE, training_active=False)

    assert top.id == first.id, "uuid7 order, not the wall clock"


def test_no_card_is_visible_during_an_active_session() -> None:
    queue = Queue(card(CardPriority.SAFETY), card(kind=WELCOME_BACK))

    assert queue.show(at(day(0)), training_active=True) == []
    assert len(queue.show(at(day(0)), training_active=False)) == 1


def test_the_budget_is_configurable() -> None:
    cards = [card(), card()]
    shown = mark_shown(cards[0], now=at(day(0)))
    answered = act(shown, CardAction.ACCEPT, now=at(day(0)), zone=ZONE)

    generous = visible(
        [answered, cards[1]],
        now=at(day(0), 10),
        zone=ZONE,
        training_active=False,
        budget=Budget(per_day=2, per_week=3),
    )

    assert [c.id for c in generous] == [cards[1].id]


# --- AC-6: expiry from first display ------------------------------------------


def test_a_card_first_shown_on_day_0_is_expired_on_day_7_and_listed_nowhere() -> None:
    queue = Queue(card())
    [shown] = queue.show(at(day(0), 9))
    assert shown.expires_at == at(day(0), 9) + CARD_LIFETIME

    assert effective_state(queue.cards[shown.id], at(day(6), 23)) is CardState.SHOWN
    assert effective_state(queue.cards[shown.id], at(day(7), 9)) is CardState.EXPIRED
    assert queue.show(at(day(7), 9)) == []


def test_a_card_queued_for_ten_days_and_never_shown_is_not_expired() -> None:
    queued = card()

    assert effective_state(queued, at(day(10))) is CardState.QUEUED
    assert queued.expires_at is None
    assert [c.id for c in Queue(queued).show(at(day(10)))] == [queued.id]


def test_snoozing_does_not_reset_first_shown() -> None:
    queue = Queue(card())
    [shown] = queue.show(at(day(0), 9))

    snoozed = queue.answer(shown, CardAction.SNOOZE, at(day(0), 9, 1))
    assert snoozed.state is CardState.SNOOZED
    assert queue.show(at(day(0), 20)) == [], "snoozed until tomorrow"

    [back] = queue.show(at(day(1), 9))
    assert back.id == shown.id
    assert back.first_shown == at(day(0), 9)
    assert back.expires_at == at(day(7), 9)
    assert effective_state(back, at(day(7), 9)) is CardState.EXPIRED


def test_an_expired_card_cannot_be_acted_on() -> None:
    shown = mark_shown(card(), now=at(day(0)))

    with pytest.raises(ValueError, match="expired"):
        act(shown, CardAction.ACCEPT, now=at(day(7)), zone=ZONE)


@pytest.mark.parametrize("terminal", [CardState.ACCEPTED, CardState.DISMISSED])
def test_an_answered_card_cannot_be_acted_on_again(terminal: CardState) -> None:
    answered = replace(mark_shown(card(), now=at(day(0))), state=terminal)

    with pytest.raises(ValueError, match=terminal.value):
        act(answered, CardAction.SNOOZE, now=at(day(0), 9), zone=ZONE)


def test_expires_at_is_seven_days_after_first_shown() -> None:
    assert expires_at(at(day(0), 9)) == at(day(7), 9)


def test_marking_an_already_shown_card_keeps_its_first_display() -> None:
    shown = mark_shown(card(), now=at(day(0)))

    assert mark_shown(shown, now=at(day(2))).first_shown == at(day(0))
