"""Purpose templates: what each purpose means for criteria and scoring.

Build plan WP-2.4. A template answers two questions about a purpose, and only
those two:

1. **Which success criteria does a session of this purpose start with?** They
   are a *default*, pre-filled when a session is planned and editable
   afterwards — a template that could not be overridden would be a rule, and
   the plan says default.
2. **Which scoring axes apply?** WP-7 computes an axis only if the purpose's
   template lists it. `adherence` is meaningless for an `unstructured` ride,
   `pacing` for anything without intervals, and a scorer that reports 0.0
   instead of "not applicable" is worse than one that says nothing.

**Templates are data, not code** (the plan is explicit). They live in a JSON
file in the repository, are parsed by :func:`parse_templates` here, and are
loaded once at startup by `app.services.templates` — so a malformed file
stops the application rather than surfacing months later as a session that
cannot be scored. This module holds the types and the parser; it does no I/O,
like everything else in this layer.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.domain.athlete import Discipline
from app.domain.coding import (
    as_enum,
    as_mapping,
    as_sequence,
    as_str,
    field,
    no_extra_fields,
    optional,
)
from app.domain.criteria import (
    ENDURANCE_ONLY_KINDS,
    STRENGTH_ONLY_KINDS,
    SuccessCriterion,
    criteria_from_json,
    criteria_to_json,
    kind_of,
)
from app.domain.purpose import Purpose, discipline_of


class ScoringAxis(StrEnum):
    """The axes WP-7 scores a session on.

    Named here rather than in WP-7 because the templates reference them, and
    the templates are written now. ``RESPONSE`` and ``FUELLING`` are in the
    vocabulary but out of MVP scope: WP-7 returns ``not_assessed(deferred)``
    for them, so the shape exists without the behaviour.
    """

    COMPLETION = "completion"
    ADHERENCE = "adherence"
    DISCIPLINE = "discipline"
    PACING = "pacing"
    SETS_LOAD = "sets_load"
    #: Reserved (post-MVP): how the athlete responded physiologically.
    RESPONSE = "response"
    #: Reserved (post-MVP): whether fuelling matched the demand.
    FUELLING = "fuelling"


#: Axes that exist in the vocabulary but compute nothing in the MVP.
DEFERRED_AXES: frozenset[ScoringAxis] = frozenset(
    {ScoringAxis.RESPONSE, ScoringAxis.FUELLING}
)

#: The axis every purpose is scored on. "Did you do the thing" applies whatever
#: the thing was, so a template omitting it is a bug in the template.
UNIVERSAL_AXIS = ScoringAxis.COMPLETION

#: Axes that only mean something for one discipline. `sets_load` needs a set
#: list; `adherence` and `pacing` need a timeline of aligned work steps.
DISCIPLINE_AXES: dict[ScoringAxis, Discipline] = {
    ScoringAxis.ADHERENCE: Discipline.CYCLING,
    ScoringAxis.DISCIPLINE: Discipline.CYCLING,
    ScoringAxis.PACING: Discipline.CYCLING,
    ScoringAxis.SETS_LOAD: Discipline.STRENGTH,
}


@dataclass(frozen=True, slots=True)
class PurposeTemplate:
    """What one purpose starts with and is judged on.

    Args:
        purpose: The purpose this template is for.
        axes: The scoring axes that apply, in :class:`ScoringAxis` order.
        default_criteria: The criteria a session of this purpose is created
            with. Editable afterwards.
    """

    purpose: Purpose
    axes: tuple[ScoringAxis, ...]
    default_criteria: tuple[SuccessCriterion, ...]

    def __post_init__(self) -> None:
        """Reject templates that could not describe a scoreable session."""
        discipline = discipline_of(self.purpose)
        if len(set(self.axes)) != len(self.axes):
            raise ValueError(f"{self.purpose.value}: duplicate scoring axes")
        if UNIVERSAL_AXIS not in self.axes:
            raise ValueError(
                f"{self.purpose.value}: every purpose is scored on "
                f"{UNIVERSAL_AXIS.value}"
            )
        for axis in self.axes:
            owner = DISCIPLINE_AXES.get(axis)
            if owner is not None and owner is not discipline:
                raise ValueError(
                    f"{self.purpose.value} is a {discipline.value} purpose, so it "
                    f"cannot be scored on {axis.value} ({owner.value} only)"
                )
        forbidden = (
            STRENGTH_ONLY_KINDS
            if discipline is Discipline.CYCLING
            else ENDURANCE_ONLY_KINDS
        )
        for criterion in self.default_criteria:
            kind = kind_of(criterion)
            if kind in forbidden:
                raise ValueError(
                    f"{self.purpose.value} is a {discipline.value} purpose, so a "
                    f"{kind.value} criterion could never be evaluated for it"
                )

    @property
    def discipline(self) -> Discipline:
        """The discipline this purpose belongs to."""
        return discipline_of(self.purpose)

    @property
    def assessable_axes(self) -> tuple[ScoringAxis, ...]:
        """The axes that actually compute a score in the MVP."""
        return tuple(axis for axis in self.axes if axis not in DEFERRED_AXES)


#: The parsed template set, keyed by purpose. Total over :class:`Purpose`.
type TemplateSet = Mapping[Purpose, PurposeTemplate]

_TEMPLATE_FIELDS = frozenset({"axes", "default_criteria", "description"})


def parse_templates(document: Any) -> dict[Purpose, PurposeTemplate]:
    """Parse the bundled purpose-template file.

    Args:
        document: The decoded JSON — ``{"templates": {"<purpose>": {...}}}``.

    Returns:
        One template per purpose, keyed by purpose.

    Raises:
        ValueError: When the file is malformed, names a purpose that does not
            exist, or **omits one that does**. Completeness is required rather
            than defaulted: a purpose with no template would be plannable and
            unscoreable, and the failure would appear at scoring time, weeks
            after the file was edited.
    """
    body = as_mapping(document, "templates file")
    no_extra_fields(body, frozenset({"templates"}), "templates file")
    entries = as_mapping(
        field(body, "templates", "templates file"), "templates file.templates"
    )

    templates: dict[Purpose, PurposeTemplate] = {}
    for name, entry in entries.items():
        path = f"templates.{name}"
        purpose = as_enum(Purpose, name, path)
        content = as_mapping(entry, path)
        no_extra_fields(content, _TEMPLATE_FIELDS, path)
        axes = as_sequence(field(content, "axes", path), f"{path}.axes")
        criteria = optional(content, "default_criteria")
        description = optional(content, "description")
        if description is not None:
            as_str(description, f"{path}.description")
        templates[purpose] = PurposeTemplate(
            purpose=purpose,
            axes=tuple(
                as_enum(ScoringAxis, axis, f"{path}.axes[{index}]")
                for index, axis in enumerate(axes)
            ),
            default_criteria=(
                ()
                if criteria is None
                else criteria_from_json(criteria, f"{path}.default_criteria")
            ),
        )

    missing = sorted(purpose.value for purpose in Purpose if purpose not in templates)
    if missing:
        raise ValueError(
            f"templates: no template for purpose(s): {', '.join(missing)}. "
            "Every purpose needs one, or a session of that purpose cannot be "
            "scored."
        )
    return templates


def template_to_json(template: PurposeTemplate) -> dict[str, Any]:
    """Serialize a template back to the file's own shape."""
    return {
        "axes": [axis.value for axis in template.axes],
        "default_criteria": criteria_to_json(template.default_criteria),
    }


def default_criteria_for(
    templates: TemplateSet, purpose: Purpose
) -> tuple[SuccessCriterion, ...]:
    """Return the criteria a session of ``purpose`` starts with.

    Raises:
        KeyError: When the template set has no entry for ``purpose`` — which
            :func:`parse_templates` makes impossible for a loaded file.
    """
    return templates[purpose].default_criteria


def axes_for(templates: TemplateSet, purpose: Purpose) -> tuple[ScoringAxis, ...]:
    """Return the scoring axes that apply to ``purpose``."""
    return templates[purpose].axes


def sorted_templates(templates: TemplateSet) -> Sequence[PurposeTemplate]:
    """Return the templates in the purpose vocabulary's own order."""
    return [templates[purpose] for purpose in Purpose if purpose in templates]
