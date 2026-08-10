"""Plan-change proposals: their lifecycle, their changes, and the safety rule.

Build-plan invariant 6. A proposal is the coaching agent's *suggestion*, and
the whole point of modelling it is that a suggestion is not a plan: the
committed plan stands until the athlete says otherwise, and it stands by
default — an unanswered proposal lapses rather than applying itself.

**One name, two things in this repository.** WP-6's "proposal" is a *match*
proposal (a recording the matcher thinks answers a planned session). This
module is about *plan-change* proposals, which share nothing with it but the
word, so the table is `plan_proposals` and every symbol here says `plan` or
`proposal` in full.

**The lifecycle is a state machine with one live state.** ``pending`` is the
only state anything can leave, and the five ways out are the five things that
can happen to a suggestion: the athlete accepts it, the athlete rejects it,
the clock runs out (``lapsed``), a newer proposal about the same session
replaces it (``superseded``), or the athlete simply *trains* — and what they
did settles the question the proposal was asking (``resolved_by_reality``).
Everything else is terminal, so a proposal is answered exactly once and the
audit trail says by what.

**Changes are a tagged union, not a patch document.** ``create``, ``update``,
``move`` and ``delete`` are the four things a plan change can be, and each
carries exactly the fields that operation needs — the three that address an
existing planned session also carry the intent version they were computed
against, which is the optimistic-concurrency token (WP-8.3). A patch document
would have to be reverse-engineered into one of these four at apply time, and
the version check has nowhere to live in it.
"""

import datetime as dt
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from app.domain.athlete import Discipline
from app.domain.purpose import Purpose
from app.domain.purpose import discipline_of as purpose_discipline

#: Longest rationale a proposal may carry. Generous: this is the agent
#: explaining itself to a human, and a truncated explanation is worse than a
#: long one.
MAX_RATIONALE_CHARS = 4_000

#: Longest rejection reason the athlete may write.
MAX_RESOLUTION_NOTE_CHARS = 1_000

#: Most changes one proposal may carry. A bound rather than a limit anyone
#: should reach: a proposal is something a human reads and answers in one
#: sitting, and one that rewrites forty sessions is not reviewable, which is
#: the same as not being a proposal.
MAX_CHANGES = 20


class ProposalStatus(StrEnum):
    """Where a plan-change proposal stands.

    ``PENDING`` is the only non-terminal member; see the module docstring for
    what each of the five exits means.
    """

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    LAPSED = "lapsed"
    SUPERSEDED = "superseded"
    RESOLVED_BY_REALITY = "resolved_by_reality"


#: The legal transitions, as a total map over :class:`ProposalStatus`. Total
#: rather than "everything absent is illegal" so that adding a member is a
#: mypy/pyrefly-visible decision about what it may become, not a silent
#: dead end (`test_transition_table_is_total` keeps it total).
LEGAL_TRANSITIONS: Mapping[ProposalStatus, frozenset[ProposalStatus]] = {
    ProposalStatus.PENDING: frozenset(
        {
            ProposalStatus.ACCEPTED,
            ProposalStatus.REJECTED,
            ProposalStatus.LAPSED,
            ProposalStatus.SUPERSEDED,
            ProposalStatus.RESOLVED_BY_REALITY,
        }
    ),
    ProposalStatus.ACCEPTED: frozenset(),
    ProposalStatus.REJECTED: frozenset(),
    ProposalStatus.LAPSED: frozenset(),
    ProposalStatus.SUPERSEDED: frozenset(),
    ProposalStatus.RESOLVED_BY_REALITY: frozenset(),
}

#: The statuses nothing leaves. Derived from the table above, so the two
#: cannot disagree.
TERMINAL_STATUSES: frozenset[ProposalStatus] = frozenset(
    status for status, exits in LEGAL_TRANSITIONS.items() if not exits
)


def can_transition(source: ProposalStatus, target: ProposalStatus) -> bool:
    """Whether a proposal may move from ``source`` to ``target``."""
    return target in LEGAL_TRANSITIONS[source]


def check_transition(source: ProposalStatus, target: ProposalStatus) -> None:
    """Raise unless ``source`` -> ``target`` is a legal transition.

    Raises:
        ValueError: When the transition is not in :data:`LEGAL_TRANSITIONS`.
            The message names both states, because every caller of this is
            answering a request that said what it wanted.
    """
    if not can_transition(source, target):
        if source in TERMINAL_STATUSES:
            raise ValueError(
                f"This proposal is already {source.value}; it cannot become "
                f"{target.value}."
            )
        raise ValueError(f"A {source.value} proposal cannot become {target.value}.")


# --- the changes --------------------------------------------------------------


class ChangeKind(StrEnum):
    """The four things a plan change can be."""

    CREATE = "create"
    UPDATE = "update"
    MOVE = "move"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class CreateChange:
    """Plan a session that does not exist yet.

    The field set `app.services.planned_sessions.PlannedSessionService.create`
    accepts, verbatim — a proposal that could ask for something the athlete's
    own create endpoint cannot express would be a second, weaker way to plan.

    Args:
        date: The athlete-local date to plan it on.
        purpose: What the session is for.
        workout_id: A library workout to prescribe, or ``None``.
        structure: An inline prescription, or ``None``. Exactly one of this
            and ``workout_id`` is given; the service enforces it, because
            "exactly one of these two" is checked there for every caller.
        intent_text: Free text about the intent.
        coach_notes: Free text for the athlete.
        success_criteria: Criteria to store, or ``None`` for the purpose
            template's.
    """

    date: dt.date
    purpose: Purpose
    workout_id: uuid.UUID | None = None
    structure: Mapping[str, Any] | None = None
    intent_text: str | None = None
    coach_notes: str | None = None
    success_criteria: Sequence[Mapping[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class UpdateChange:
    """Revise an existing planned session's intent or calendar fields.

    Args:
        planned_session_id: The session to revise.
        expected_intent_version: The intent version the agent computed this
            against. Checked when the proposal is written **and** again when
            it is accepted; a mismatch is a conflict, not a merge.
        updates: The fields to change, in the shape
            `PlannedSessionService.update` accepts — and in its **types**: see
            :meth:`__post_init__`.
    """

    planned_session_id: uuid.UUID
    expected_intent_version: int
    updates: Mapping[str, Any]

    def __post_init__(self) -> None:
        """Normalize the patch to the types the plan service works in.

        This is the one field in the union that is a document rather than a
        column, so it is the one place where "it came back from JSON" can
        quietly differ from "it was constructed in Python": a stored
        ``workout_id`` is a string, a stored ``date`` is a string, and a
        service handed either of those reaches the driver with a ``str`` where
        a ``UUID`` belongs. Normalizing here rather than in the service means
        an :class:`UpdateChange` has the same contents however it was made,
        and the accept path cannot behave differently from the dry run that
        previewed it.

        Raises:
            ValueError: When a field is not one the plan service accepts, or
                its value is not of the kind that field takes.
        """
        object.__setattr__(
            self, "updates", MappingProxyType(_parse_updates(self.updates))
        )


@dataclass(frozen=True, slots=True)
class MoveChange:
    """Move an existing planned session to another date.

    Its own kind rather than an :class:`UpdateChange` carrying ``date``, for
    the reason `POST /planned-sessions/{id}/move` is its own verb (D56):
    moving a session is one intention, and the diff the athlete reads should
    be able to say so instead of showing a field patch.
    """

    planned_session_id: uuid.UUID
    expected_intent_version: int
    date: dt.date


@dataclass(frozen=True, slots=True)
class DeleteChange:
    """Remove an existing planned session and its whole intent chain."""

    planned_session_id: uuid.UUID
    expected_intent_version: int


#: Fields an `update` change may carry — `PlannedSessionService.UPDATABLE_FIELDS`
#: (which the domain cannot import: services are an outer layer) **minus
#: `status`**. The service's own check still stands; this one exists so a
#: malformed patch is refused when the change is *built*, not when it is
#: applied hours later.
#:
#: `status` is deliberately absent (D174). A planned session's status is
#: derived from reality — WP-6 moves it between `planned`, `completed`,
#: `missed` and `displaced` as matches come and go — so it is not a statement
#: anyone can *suggest*: proposing `completed` would be proposing that the
#: athlete has already trained. The red-flag rule cannot catch it either
#: (`intensifies` reads purpose and load, and a status carries neither), so an
#: agent could mark an unridden session complete with the flag up. The
#: athlete's own `PATCH` still accepts it; only the proposal vocabulary does
#: not.
UPDATE_FIELDS: tuple[str, ...] = (
    "purpose",
    "intent_text",
    "coach_notes",
    "success_criteria",
    "workout_id",
    "structure",
    "date",
)


# --- typed reads of a loosely-typed document ------------------------------------
#
# Every field below arrives as "whatever was in the JSON": from an MCP tool
# call the model composed, or from a `changes` column written when the union
# looked different. The naive conversions these replace — `dict(value)`,
# `[dict(item) for item in value]` — raise `TypeError` on the wrong type, and a
# `TypeError` out of here is not a refusal anybody catches: the MCP adapter
# reports it as "the server failed", which tells the agent to retry the same
# malformed call forever. So every loosely-typed field is checked here, in the
# domain, and a wrong type leaves as a `ValueError` naming the field — which
# `changes_from_json` prefixes with the change's index and the services turn
# into a 422.
#
# `dict(value)` also *succeeds* on some wrong types: `dict(["ab"])` is
# `{"a": "b"}`. Silently accepting a two-character string as a prescription is
# worse than the TypeError, which is the other half of why these exist.


def _as_object(value: Any, label: str) -> dict[str, Any]:
    """Read a JSON object, or refuse by name."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object, got {type(value).__name__}")
    return dict(value)


def _as_object_list(value: Any, label: str) -> list[dict[str, Any]]:
    """Read a JSON array of objects, or refuse by name and position."""
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        raise ValueError(
            f"{label} must be a list of objects, got {type(value).__name__}"
        )
    return [_as_object(item, f"{label}[{index}]") for index, item in enumerate(value)]


def _as_text(value: Any, label: str) -> str:
    """Read a JSON string, or refuse by name."""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string, got {type(value).__name__}")
    return value


def _optional[T](
    document: Mapping[str, Any], key: str, read: Callable[[Any, str], T]
) -> T | None:
    """Apply ``read`` to a field unless it is absent or null."""
    raw = document.get(key)
    return None if raw is None else read(raw, key)


def _parse_updates(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Coerce a stored patch document to the types the plan service works in."""
    unknown = set(raw) - set(UPDATE_FIELDS)
    if "status" in unknown:
        # Named rather than lumped in with the typos: it *is* a planned-session
        # field, it is simply not one anybody proposes (see UPDATE_FIELDS).
        raise ValueError(
            "status is not a proposable field: a planned session's status is "
            "derived from what the athlete did, not from what anyone suggests"
        )
    if unknown:
        # `str(...)` before sorting: a JSON object always has string keys, but
        # this parses whatever a caller handed it, and a non-string key would
        # otherwise make the *refusal* raise a TypeError of its own.
        raise ValueError(
            f"unknown planned-session field(s): "
            f"{', '.join(sorted(str(name) for name in unknown))}"
        )
    if not raw:
        raise ValueError("an update change needs a non-empty updates object")
    parsed: dict[str, Any] = {}
    for key, value in raw.items():
        if value is None:
            parsed[key] = None
            continue
        try:
            parsed[key] = _parse_update(key, value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"updates.{key} is invalid: {exc}") from exc
    return parsed


def _parse_update(key: str, value: Any) -> Any:
    """Coerce one patch field."""
    match key:
        case "purpose":
            return value if isinstance(value, Purpose) else Purpose(value)
        case "date":
            return value if isinstance(value, dt.date) else dt.date.fromisoformat(value)
        case "workout_id":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        case "structure":
            return _as_object(value, key)
        case "success_criteria":
            return _as_object_list(value, key)
        case "intent_text" | "coach_notes":
            return _as_text(value, key)
        case _:
            return value


def _updates_to_json(updates: Mapping[str, Any]) -> dict[str, Any]:
    """Render a patch document back to its stored wire form."""
    rendered: dict[str, Any] = {}
    for key, value in updates.items():
        match value:
            case None:
                rendered[key] = None
            case StrEnum():
                rendered[key] = value.value
            case uuid.UUID():
                rendered[key] = str(value)
            case dt.date():
                rendered[key] = value.isoformat()
            case _:
                rendered[key] = value
    return rendered


type PlanChange = CreateChange | UpdateChange | MoveChange | DeleteChange

#: The kinds that address a session that already exists — the three carrying a
#: concurrency token.
TARGETED_KINDS: frozenset[ChangeKind] = frozenset(
    {ChangeKind.UPDATE, ChangeKind.MOVE, ChangeKind.DELETE}
)


def kind_of(change: PlanChange) -> ChangeKind:
    """Return the tag of one change."""
    match change:
        case CreateChange():
            return ChangeKind.CREATE
        case UpdateChange():
            return ChangeKind.UPDATE
        case MoveChange():
            return ChangeKind.MOVE
        case DeleteChange():
            return ChangeKind.DELETE


def target_of(change: PlanChange) -> uuid.UUID | None:
    """The planned session a change addresses, or ``None`` for a ``create``."""
    if isinstance(change, CreateChange):
        return None
    return change.planned_session_id


def expected_version_of(change: PlanChange) -> int | None:
    """The concurrency token a change carries, or ``None`` for a ``create``."""
    if isinstance(change, CreateChange):
        return None
    return change.expected_intent_version


def change_to_json(change: PlanChange) -> dict[str, Any]:
    """Serialize one change to its stored wire form."""
    kind = kind_of(change)
    match change:
        case CreateChange():
            return {
                "kind": kind.value,
                "date": change.date.isoformat(),
                "purpose": change.purpose.value,
                "workout_id": (
                    None if change.workout_id is None else str(change.workout_id)
                ),
                "structure": (
                    None if change.structure is None else dict(change.structure)
                ),
                "intent_text": change.intent_text,
                "coach_notes": change.coach_notes,
                "success_criteria": (
                    None
                    if change.success_criteria is None
                    else [dict(criterion) for criterion in change.success_criteria]
                ),
            }
        case UpdateChange():
            return {
                "kind": kind.value,
                "planned_session_id": str(change.planned_session_id),
                "expected_intent_version": change.expected_intent_version,
                "updates": _updates_to_json(change.updates),
            }
        case MoveChange():
            return {
                "kind": kind.value,
                "planned_session_id": str(change.planned_session_id),
                "expected_intent_version": change.expected_intent_version,
                "date": change.date.isoformat(),
            }
        case DeleteChange():
            return {
                "kind": kind.value,
                "planned_session_id": str(change.planned_session_id),
                "expected_intent_version": change.expected_intent_version,
            }


def changes_to_json(changes: Sequence[PlanChange]) -> list[dict[str, Any]]:
    """Serialize a proposal's changes, in order."""
    return [change_to_json(change) for change in changes]


def _uuid(document: Mapping[str, Any], key: str, *, required: bool) -> uuid.UUID | None:
    """Read a uuid field out of a stored change document."""
    raw = document.get(key)
    if raw is None:
        if required:
            raise ValueError(f"a change of this kind needs {key}")
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError as exc:
        raise ValueError(f"{key} is not a uuid: {raw!r}") from exc


def _date(document: Mapping[str, Any], key: str) -> dt.date:
    """Read a date field out of a stored change document."""
    raw = document.get(key)
    if isinstance(raw, dt.datetime):
        return raw.date()
    if isinstance(raw, dt.date):
        return raw
    if isinstance(raw, str):
        try:
            return dt.date.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"{key} is not an ISO date: {raw!r}") from exc
    if raw is None:
        raise ValueError(f"a change of this kind needs {key}")
    raise ValueError(f"{key} must be an ISO date, got {type(raw).__name__}")


def _version(document: Mapping[str, Any]) -> int:
    """Read the concurrency token out of a stored change document."""
    raw = document.get("expected_intent_version")
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        raise ValueError(
            "expected_intent_version must be the intent version this change "
            "was computed against (a positive integer)"
        )
    return raw


def change_from_json(document: Mapping[str, Any]) -> PlanChange:
    """Parse one stored change document back into the domain.

    Raises:
        ValueError: When the document is not an object, the tag is unknown, or
            a field the kind needs is missing or of the wrong type. Always a
            ``ValueError`` naming the field — see the note above
            :func:`_as_object`.
    """
    if not isinstance(document, Mapping):
        raise ValueError(f"a change must be an object, got {type(document).__name__}")
    raw_kind = document.get("kind")
    try:
        kind = ChangeKind(raw_kind)
    except ValueError as exc:
        legal = ", ".join(member.value for member in ChangeKind)
        raise ValueError(
            f"unknown change kind {raw_kind!r}; expected one of {legal}"
        ) from exc

    if kind is ChangeKind.CREATE:
        raw_purpose = document.get("purpose")
        try:
            purpose = Purpose(raw_purpose)
        except ValueError as exc:
            raise ValueError(f"unknown purpose {raw_purpose!r}") from exc
        return CreateChange(
            date=_date(document, "date"),
            purpose=purpose,
            workout_id=_uuid(document, "workout_id", required=False),
            structure=_optional(document, "structure", _as_object),
            intent_text=_optional(document, "intent_text", _as_text),
            coach_notes=_optional(document, "coach_notes", _as_text),
            success_criteria=_optional(document, "success_criteria", _as_object_list),
        )

    # The three targeted kinds. `_uuid(..., required=True)` never returns None.
    target = _uuid(document, "planned_session_id", required=True)
    assert target is not None  # noqa: S101 — narrowing, guaranteed above
    version = _version(document)
    if kind is ChangeKind.UPDATE:
        updates = document.get("updates")
        if not isinstance(updates, Mapping) or not updates:
            raise ValueError("an update change needs a non-empty updates object")
        return UpdateChange(
            planned_session_id=target,
            expected_intent_version=version,
            updates=dict(updates),
        )
    if kind is ChangeKind.MOVE:
        return MoveChange(
            planned_session_id=target,
            expected_intent_version=version,
            date=_date(document, "date"),
        )
    return DeleteChange(planned_session_id=target, expected_intent_version=version)


def changes_from_json(documents: Sequence[Mapping[str, Any]]) -> tuple[PlanChange, ...]:
    """Parse a proposal's stored changes, in order.

    Raises:
        ValueError: When the list is empty, longer than :data:`MAX_CHANGES`,
            or any document is malformed. The message names the position, so
            a caller that sent five changes is told which one is wrong.
    """
    if not documents:
        raise ValueError("a proposal must carry at least one change")
    if len(documents) > MAX_CHANGES:
        raise ValueError(
            f"a proposal may carry at most {MAX_CHANGES} changes, got {len(documents)}"
        )
    parsed: list[PlanChange] = []
    for index, document in enumerate(documents):
        try:
            parsed.append(change_from_json(document))
        except ValueError as exc:
            raise ValueError(f"change {index}: {exc}") from exc
    return tuple(parsed)


# --- the red-flag rule ----------------------------------------------------------


#: Purpose intensity, ranked **within a discipline**. Higher is harder.
#:
#: An explicit ordering rather than a property derived from the prescription,
#: because the rule it serves has to be answerable about a change that carries
#: no structure at all ("make Tuesday a vo2max session"). The two halves are
#: numbered independently and are never compared with each other — a rank of 5
#: in cycling and a rank of 5 in strength are two different scales, which is
#: why :func:`intensifies` refuses to compare across disciplines rather than
#: subtracting them.
#:
#: The endurance order is the physiological one the zone model already
#: implies (recovery < endurance < tempo < sweet spot < threshold < VO2max <
#: anaerobic < neuromuscular), with the three non-prescriptive purposes placed
#: below tempo: `technique` and `unstructured` are not efforts, and a
#: `test` sits at the top because a maximal test is the hardest thing on the
#: calendar and is exactly what an ill athlete must not be talked into.
#:
#: The strength order runs from restorative to maximal: mobility < core <
#: conditioning < strength-endurance < hypertrophy < power < max strength.
PURPOSE_INTENSITY: Mapping[Purpose, int] = {
    # --- endurance (cycling) ---
    Purpose.RECOVERY: 0,
    Purpose.TECHNIQUE: 1,
    Purpose.UNSTRUCTURED: 1,
    Purpose.ENDURANCE: 2,
    Purpose.TEMPO: 3,
    Purpose.SWEET_SPOT: 4,
    Purpose.THRESHOLD: 5,
    Purpose.VO2MAX: 6,
    Purpose.ANAEROBIC: 7,
    Purpose.NEUROMUSCULAR: 8,
    Purpose.TEST: 9,
    # --- strength ---
    Purpose.MOBILITY: 0,
    Purpose.CORE: 1,
    Purpose.CONDITIONING: 2,
    Purpose.STRENGTH_ENDURANCE: 3,
    Purpose.HYPERTROPHY: 4,
    Purpose.POWER: 5,
    Purpose.MAX_STRENGTH: 6,
}


def purpose_intensity(purpose: Purpose) -> int:
    """Return the intensity rank of a purpose within its own discipline."""
    return PURPOSE_INTENSITY[purpose]


def intensifies(
    *,
    before_purpose: Purpose,
    after_purpose: Purpose,
    before_load: float | None = None,
    after_load: float | None = None,
) -> str | None:
    """Say whether a revision makes a planned session harder, and why.

    The deterministic half of WP-8.4. While the athlete's red flag stands, the
    coaching agent may not propose anything that **adds or intensifies** work;
    "adds" is answered by the change's kind (a ``create`` adds, full stop) and
    "intensifies" is answered here, for a revision of a session that already
    exists.

    Two signals, in this order:

    1. **Purpose rank.** :data:`PURPOSE_INTENSITY` orders the vocabulary
       within each discipline, and raising the rank intensifies. This is the
       signal that always works: a revision naming only a purpose still has
       one, which is why the rule cannot rest on predicted load alone.
    2. **Predicted load.** When *both* sides can be predicted — the change
       carries a structure and the pinned anchors resolve, on both sides of
       the same axis — an increase intensifies. When either side is
       unpredictable the signal is simply absent and rank decides alone,
       because "we could not compute it" is not evidence of a reduction.

    A revision that changes **discipline** intensifies by default: the two
    rank scales are unrelated (see :data:`PURPOSE_INTENSITY`), so swapping a
    ride for a lift cannot be *shown* to be a reduction, and the rule refuses
    what it cannot show while the athlete is ill.

    Moves, deletes and reductions return ``None`` — the athlete stays free to
    have the plan lightened, rearranged or cleared while unwell, which is
    what the flag is for.

    Args:
        before_purpose: The purpose in force.
        after_purpose: The purpose the change would leave in force.
        before_load: The predicted cost in force, on whichever axis the
            discipline uses (TSS-equivalent or kilograms), or ``None``.
        after_load: The predicted cost the change would leave, on the **same**
            axis, or ``None``.

    Returns:
        A sentence naming the increase, or ``None`` when the change does not
        add intensity. The sentence is the refusal the agent is given: a rule
        it cannot read is a rule it cannot plan around.
    """
    before_discipline = purpose_discipline(before_purpose)
    after_discipline = purpose_discipline(after_purpose)
    if before_discipline is not after_discipline:
        return (
            f"it changes the discipline from {before_discipline.value} to "
            f"{after_discipline.value}, and the two intensity scales are not "
            "comparable"
        )
    if purpose_intensity(after_purpose) > purpose_intensity(before_purpose):
        return (
            f"it raises the purpose from {before_purpose.value} to "
            f"{after_purpose.value}"
        )
    if before_load is not None and after_load is not None and after_load > before_load:
        unit = "kg" if after_discipline is Discipline.STRENGTH else "TSS"
        return (
            f"it raises the predicted load from {before_load:.1f} to "
            f"{after_load:.1f} {unit}"
        )
    return None
