"""The planned session: a prescription plus the intent behind it.

Build plan WP-2.5 and invariant 4 — *prescriptions freeze at planning time*.
Two ideas carry that here:

**Intent is versioned, the session is not.** A planned session is an identity
(a date, a discipline, a status). Everything that says what it is *for* — the
purpose, the free-text intent, the coach notes, the success criteria, the
workout as prescribed, and the anchor versions the targets resolve against —
is an :class:`SessionIntent`, and intents are append-only. Editing intent
writes version n+1 and leaves version n retrievable, because a score computed
against version n has to stay explicable.

**Anchors are pinned, not looked up.** The intent stores which anchor *version*
each percentage target derives from, chosen when the intent was written. A new
FTP appended tomorrow does not silently reinterpret what was prescribed today
(`app.domain.anchors.anchor_as_of` is the rule for "which version", and the
pin is what freezes the answer).

The edit rules themselves — when a new version is `edited_post_hoc`, when
anchors are re-pinned — live in `app.services.planned_sessions`, because
"has this session been matched yet" is a question about stored state.
"""

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import StrEnum
from types import MappingProxyType

from app.domain.anchors import AnchorType
from app.domain.athlete import Discipline
from app.domain.criteria import (
    ENDURANCE_ONLY_KINDS,
    STRENGTH_ONLY_KINDS,
    SuccessCriterion,
    kind_of,
)
from app.domain.criteria import referenced_anchor_types as criteria_anchors
from app.domain.purpose import Purpose
from app.domain.purpose import discipline_of as purpose_discipline
from app.domain.workout import WorkoutBody
from app.domain.workout import discipline_of as body_discipline
from app.domain.workout import referenced_anchor_types as body_anchors


class SessionStatus(StrEnum):
    """Where a planned session ended up.

    ``DISPLACED`` is not ``MISSED``: the athlete trained, just not this. WP-6
    sets it when a low-similarity activity is deliberately linked; the member
    exists now so the column does not need a migration then.
    """

    PLANNED = "planned"
    COMPLETED = "completed"
    MISSED = "missed"
    DISPLACED = "displaced"


#: Statuses that mean the session is still ahead of the athlete.
OPEN_STATUSES: frozenset[SessionStatus] = frozenset({SessionStatus.PLANNED})

#: Longest an intent or note may be. Generous — these are the athlete's own
#: words — but bounded, because they are stored and rendered.
MAX_INTENT_CHARS = 4_000


def required_anchor_types(
    body: WorkoutBody, criteria: Sequence[SuccessCriterion]
) -> frozenset[AnchorType]:
    """Return every anchor type this prescription has to pin.

    The union of what the workout's targets reference and what the success
    criteria reference: both are part of the frozen prescription, and a
    ceiling of "75 % FTP" is as unresolvable without a pinned FTP as a target
    of "85-95 % FTP" is.
    """
    return body_anchors(body) | criteria_anchors(criteria)


def check_prescription(
    purpose: Purpose, body: WorkoutBody, criteria: Sequence[SuccessCriterion]
) -> None:
    """Check a prescription's shape, ignoring its anchor pins.

    The half of :class:`SessionIntent`'s rules that can be checked *before*
    anchors are resolved: the purpose and the workout must belong to the same
    discipline, and every criterion must be one that discipline can evaluate.

    Separate from ``__post_init__`` so a service can run it first. Pinning
    anchors is a database round-trip per anchor, and the error it can raise
    ("no FTP is in force") would otherwise mask the more fundamental one
    ("that is a strength purpose on a bike workout").

    Raises:
        ValueError: When the purpose and the workout disagree, or a criterion
            could never be evaluated for the discipline.
    """
    discipline = purpose_discipline(purpose)
    if body_discipline(body) is not discipline:
        raise ValueError(
            f"{purpose.value} is a {discipline.value} purpose, but the "
            f"workout is a {body_discipline(body).value} workout"
        )
    forbidden = (
        STRENGTH_ONLY_KINDS
        if discipline is Discipline.CYCLING
        else ENDURANCE_ONLY_KINDS
    )
    for criterion in criteria:
        kind = kind_of(criterion)
        if kind in forbidden:
            raise ValueError(
                f"a {kind.value} criterion cannot be evaluated for a "
                f"{discipline.value} session"
            )


@dataclass(frozen=True, slots=True)
class SessionIntent:
    """One immutable version of what a planned session is for.

    Args:
        purpose: Why the session exists; selects the template that seeded
            ``success_criteria``.
        body: The prescription as frozen at this version — a snapshot, even
            when it came from the library, so a later edit to the library
            workout cannot change what was prescribed.
        success_criteria: Machine-checkable criteria, derived from the
            purpose template and then editable.
        pinned_anchor_versions: Anchor type -> the id of the anchor version
            this intent's percentages resolve against.
        intent_text: The athlete's or coach's own words. Never interpreted by
            anything computed (invariant 7).
        coach_notes: Additional free text.
    """

    purpose: Purpose
    body: WorkoutBody
    success_criteria: tuple[SuccessCriterion, ...] = ()
    pinned_anchor_versions: Mapping[AnchorType, uuid.UUID] = dataclass_field(
        default_factory=lambda: MappingProxyType({})
    )
    intent_text: str | None = None
    coach_notes: str | None = None

    def __post_init__(self) -> None:
        """Reject intents that could not be executed or scored."""
        check_prescription(self.purpose, self.body, self.success_criteria)
        required = required_anchor_types(self.body, self.success_criteria)
        unpinned = sorted(
            anchor.value for anchor in required - set(self.pinned_anchor_versions)
        )
        if unpinned:
            raise ValueError(
                "every anchor a prescription refers to must be pinned at planning "
                f"time; missing: {', '.join(unpinned)}"
            )
        extra = sorted(
            anchor.value for anchor in set(self.pinned_anchor_versions) - required
        )
        if extra:
            raise ValueError(
                f"pinned anchor(s) the prescription does not refer to: "
                f"{', '.join(extra)}"
            )
        for name, text in (
            ("intent_text", self.intent_text),
            ("coach_notes", self.coach_notes),
        ):
            if text is None:
                continue
            if not text.strip():
                raise ValueError(f"{name} must not be blank; omit it instead")
            if len(text) > MAX_INTENT_CHARS:
                raise ValueError(
                    f"{name} must be at most {MAX_INTENT_CHARS} characters, "
                    f"got {len(text)}"
                )

    @property
    def discipline(self) -> Discipline:
        """The discipline this intent prescribes for."""
        return purpose_discipline(self.purpose)


@dataclass(frozen=True, slots=True)
class PlannedSession:
    """A session on the calendar, with the intent version in force.

    The date is an athlete-local *date*, not an instant: a session belongs to
    a day, and WP-4 assigns recordings to days by the athlete's local
    timezone at the start of the activity.
    """

    id: uuid.UUID
    date: dt.date
    discipline: Discipline
    status: SessionStatus
    intent: SessionIntent

    def __post_init__(self) -> None:
        """Reject a session whose intent prescribes another discipline."""
        if self.intent.discipline is not self.discipline:
            raise ValueError(
                f"a {self.discipline.value} session cannot carry a "
                f"{self.intent.discipline.value} intent"
            )
