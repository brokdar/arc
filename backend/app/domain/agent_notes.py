"""Agent notes: the interpretive half of the record, kept apart from the rest.

Build-plan invariant 7. Everything else this application stores is *computed*
— a metric, an axis score, a match, a verdict — and is reproducible from the
recording and the plan. A note is not: it is what a language model said about
them, and no amount of re-running produces it again. Mixing the two would make
the deterministic record unfalsifiable, because a reader could no longer tell
which parts of it a rule derived and which parts a model asserted.

So notes live in their own table, and three columns keep them honest:

* ``model_id`` — **who said it**, required and never defaulted. An
  unattributed note is an opinion the system appears to hold.
* ``cites`` — **what it looked at**, as artefact ids. A note citing nothing is
  allowed (some commentary is about the week, not about a row), but a note
  citing something can be checked against it.
* ``dispute`` — **what the athlete thought of it**, one tap, overwritable.
  This is the seed of the coach-quality loop, so it must cost nothing to give
  and nothing to change.

**One target, never two.** A note is about a session or about a plan week, and
the table refuses both-or-neither (``ck_agent_notes_one_target``). The
alternative — a nullable pair nobody constrains — makes every reader guess
what a row with two targets means, and they will not all guess the same.

Nothing here does I/O; `app.services.agent_notes` is the use-case layer.
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from enum import StrEnum

#: Longest note a model may write. Generous, for the reason a rationale is
#: (`app.domain.proposals.MAX_RATIONALE_CHARS`): this is the coach explaining
#: itself to a human, and a truncated explanation is worse than a long one.
MAX_NOTE_CHARS = 8_000

#: Longest ``model_id`` the attribution column holds. Model identifiers are
#: short strings; this is a column bound, not a naming rule.
MAX_MODEL_ID_CHARS = 120

#: Most artefact ids one note may cite. A note is a paragraph about a handful
#: of things; a hundred citations is a query result, not a citation list.
MAX_CITES = 50


class NoteKind(StrEnum):
    """What kind of interpretive text this is.

    The two differ in what they are *about*, not in how much they are trusted:

    ``EVALUATION`` is the coach's read of one session — what the athlete did
    and what it means — and is the autonomy tier the build plan calls Tier 1,
    written only against a session.

    ``ANNOTATION`` is free commentary (Tier 0), and may hang off a session or
    a plan week: "this block has been three weeks of threshold" is about the
    week, not about any one ride.
    """

    EVALUATION = "evaluation"
    ANNOTATION = "annotation"


class DisputeRating(StrEnum):
    """The athlete's one-tap answer to a note.

    Deliberately two values and no scale. This is a signal about coach
    quality, gathered from someone who is reading their training log rather
    than filling in a survey, and a five-point scale asked of a passer-by
    collects noise rather than resolution.
    """

    UP = "up"
    DOWN = "down"


def clean_text(text: str) -> str:
    """Validate the body of a note.

    Raises:
        ValueError: When it is blank or too long. Blank rather than missing,
            because whitespace is the shape a required field takes when
            nothing was there to say — and a note with nothing in it is
            attribution without an assertion.
    """
    body = text.strip()
    if not body:
        raise ValueError("A note needs text: an empty note asserts nothing.")
    if len(body) > MAX_NOTE_CHARS:
        raise ValueError(f"A note may be at most {MAX_NOTE_CHARS} characters")
    return body


def clean_model_id(model_id: str) -> str:
    """Validate the attribution a note must carry.

    Raises:
        ValueError: When it is blank or too long. Required with no default:
            a note whose author the system invented is worse than one with no
            author, because it reads as a fact about who said it.
    """
    identifier = model_id.strip()
    if not identifier:
        raise ValueError(
            "A note needs a model_id: interpretive text is attributed to the "
            "model that wrote it, and an unattributed note reads as something "
            "the application itself believes."
        )
    if len(identifier) > MAX_MODEL_ID_CHARS:
        raise ValueError(f"model_id may be at most {MAX_MODEL_ID_CHARS} characters")
    return identifier


def parse_cites(raw: Sequence[str | uuid.UUID]) -> tuple[uuid.UUID, ...]:
    """Parse the artefact ids a note cites, preserving order.

    Validated as uuids and stored as strings, because the things a note may
    cite live in several tables — a session, a planned session, an anchor, a
    score — and a foreign key can only point at one of them. Checking the
    *shape* is what can be checked without inventing a polymorphic key; that
    a cited id still resolves is a question for the reader, and a note about a
    session that was later deleted is still a true record of what was said.

    Empty is legal: commentary about a week cites nothing in particular.

    Raises:
        ValueError: When there are too many, or one is not a uuid. The message
            names the position, so a caller that sent five is told which.
    """
    if len(raw) > MAX_CITES:
        raise ValueError(
            f"A note may cite at most {MAX_CITES} artefacts, got {len(raw)}"
        )
    parsed: list[uuid.UUID] = []
    for index, value in enumerate(raw):
        if isinstance(value, uuid.UUID):
            parsed.append(value)
            continue
        try:
            parsed.append(uuid.UUID(str(value)))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(
                f"cites[{index}] is not an artefact id: {value!r} is not a uuid"
            ) from exc
    return tuple(parsed)


def check_plan_week(start: dt.date) -> None:
    """Refuse a plan-week key that is not the Monday of a week.

    A plan week runs Monday to Sunday (`app.domain.plan`), so "the week of the
    9th" and "the week of the 11th" are the same week — and a column that
    accepted both would file two notes about one week under two keys, so
    neither read would find the other's. The Monday is snapped **by the
    caller**, not here: an agent that meant a different week should be told
    so rather than quietly filed elsewhere, and the message names the Monday
    it was reaching for.

    Raises:
        ValueError: When ``start`` is not a Monday.
    """
    if start.weekday() != 0:
        monday = start - dt.timedelta(days=start.weekday())
        raise ValueError(
            f"plan_week must be the Monday a plan week starts on; "
            f"{start.isoformat()} is a {start.strftime('%A')}. Did you mean "
            f"{monday.isoformat()}?"
        )
