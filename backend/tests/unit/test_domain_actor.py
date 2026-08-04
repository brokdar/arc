"""The Actor value object: the identity every write is credited to."""

import dataclasses
from collections.abc import Callable

import pytest

from app.domain.actor import Actor, ActorKind


def test_the_three_constructors_produce_the_stored_string_form() -> None:
    assert str(Actor.athlete()) == "athlete"
    assert str(Actor.system()) == "system"
    assert str(Actor.agent("coach")) == "agent:coach"


def test_actors_are_frozen_value_objects() -> None:
    assert Actor.agent("coach") == Actor.agent("coach")
    assert Actor.agent("coach") != Actor.agent("readonly")
    assert Actor.athlete() != Actor.system()

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(Actor.athlete(), "kind", ActorKind.SYSTEM)  # noqa: B010


@pytest.mark.parametrize(
    "actor",
    [Actor.athlete(), Actor.system(), Actor.agent("coach")],
    ids=["athlete", "system", "agent"],
)
def test_the_string_form_round_trips(actor: Actor) -> None:
    assert Actor.parse(str(actor)) == actor


def test_agent_labels_are_stripped() -> None:
    assert Actor.agent("  coach  ") == Actor.agent("coach")


@pytest.mark.parametrize(
    ("build", "expected"),
    [
        (lambda: Actor.agent(""), "needs the label"),
        (lambda: Actor.agent("   "), "needs the label"),
        # `agent:my:key` would parse back as label "my:key" — allowed here it
        # would still be a different label than the one written.
        (lambda: Actor.agent("my:key"), "may not contain"),
        (lambda: Actor(ActorKind.ATHLETE, "someone"), "carries no label"),
        (lambda: Actor(ActorKind.SYSTEM, "cron"), "carries no label"),
    ],
    ids=["empty", "whitespace", "colon", "labelled-athlete", "labelled-system"],
)
def test_invalid_shapes_are_rejected(build: Callable[[], Actor], expected: str) -> None:
    with pytest.raises(ValueError, match=expected):
        build()


@pytest.mark.parametrize(
    "raw",
    ["", "nobody", "Agent:coach", "agent", ":coach"],
    ids=["empty", "unknown", "wrong-case", "agent-without-label", "no-kind"],
)
def test_parsing_a_non_actor_raises(raw: str) -> None:
    with pytest.raises(ValueError, match="Unknown actor|needs the label"):
        Actor.parse(raw)
