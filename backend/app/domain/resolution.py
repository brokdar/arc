"""Prescribed targets resolved against the anchor versions a session pinned.

A target is written once and read two ways. `85-95 % FTP` is what the plan
*says* — it survives an FTP change, and it is what a purpose template can
express — while `212-238 W` is what the athlete rides. Both are the prescription;
neither replaces the other, so resolution returns them together
(:class:`ResolvedTarget`) rather than substituting one for the other.

**Against the pins, never against "now".** Resolution takes the anchor
versions the intent froze (`SessionIntent.pinned_anchor_versions`), so
appending a new FTP anchor changes nothing about a session already planned.
That is build-plan invariant 4 made visible: the pin is the product's most
distinctive guarantee and it is worth nothing if the screen quietly resolves
against the current value instead.

**Nothing resolves is a legal answer.** An absolute target passes through with
no anchor; a percentage of an anchor the session did not pin resolves to
``None`` on both bounds. A step with no target for a channel simply has no
entry. Missing means "not resolved", never zero.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.anchors import AnchorType
from app.domain.prediction import PinnedAnchor
from app.domain.strength import StrengthWorkout
from app.domain.workout import (
    CHANNEL_UNITS,
    AbsoluteRange,
    Channel,
    ChannelUnit,
    EnduranceWorkout,
    PercentOfAnchor,
    StepRole,
    Target,
    Targets,
    WorkoutBody,
    flatten,
)

#: Decimal places resolved bounds are rounded to. Enough to keep a percentage
#: of an anchor honest (``0.93 x 250`` is 232.5 W, not 232) and few enough
#: that binary floating point cannot leak ``232.50000000000003`` into a
#: response body.
RESOLVED_PRECISION = 1

#: The en dash a range is rendered with. A hyphen reads as a minus sign next
#: to numbers, and this string ends up on a phone in someone's jersey pocket.
RANGE_DASH = "–"


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    """One channel's target, said both ways.

    Args:
        channel: Which channel this prescribes.
        prescribed: The target as written, render-ready — ``"88-93 % FTP"``,
            ``"250 W"``. Built here rather than in a client so the API, the
            MCP tools and the UI quote the prescription identically.
        resolved_low: Lower bound in the channel's unit, or ``None`` when
            nothing resolves it.
        resolved_high: Upper bound, equal to :attr:`resolved_low` for a point
            target.
        unit: The channel's unit; the resolved bounds are in it.
        anchor_version_id: The anchor version the percentage resolved against,
            or ``None`` for an absolute target and for a percentage of an
            anchor this session did not pin.
    """

    channel: Channel
    prescribed: str
    resolved_low: float | None
    resolved_high: float | None
    unit: ChannelUnit
    anchor_version_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class ResolvedStep:
    """One flattened step with its targets resolved.

    Args:
        index: 0-based position in the flattened sequence — the same index
            :func:`app.domain.workout.flatten` assigns, so a client can join
            this onto the step tree it already drew.
        role: What the step is for.
        name: The step's display label, if it has one.
        duration_s: How long, when the step is time-based.
        distance_m: How far, when it is distance-based.
        is_ramp: Whether the targets move across the step. When false,
            :attr:`end_targets` repeats :attr:`start_targets`.
        start_targets: Targets at the start of the step, channel order stable.
        end_targets: Targets at the end.
    """

    index: int
    role: StepRole
    name: str | None
    duration_s: int | None
    distance_m: float | None
    is_ramp: bool
    start_targets: tuple[ResolvedTarget, ...]
    end_targets: tuple[ResolvedTarget, ...]


def _percentage(fraction: float) -> str:
    """Render a fraction as a percentage without trailing zeros."""
    return f"{fraction * 100:g}"


def _quantity(value: float) -> str:
    """Render an absolute bound without trailing zeros."""
    return f"{value:g}"


def _anchor_name(anchor_type: AnchorType) -> str:
    """Render an anchor type the way an athlete writes it: ``FTP``, ``MAX HR``."""
    return anchor_type.value.replace("_", " ").upper()


def render_target(target: Target) -> str:
    """Render a target as the prescription reads it.

    ``"88-93 % FTP"``, ``"88 % FTP"``, ``"250-270 W"``, ``"250 W"`` — a point
    target renders as one number rather than as a range of itself.
    """
    if isinstance(target, PercentOfAnchor):
        name = _anchor_name(target.anchor_type)
        if target.pct_low == target.pct_high:
            return f"{_percentage(target.pct_low)} % {name}"
        low = _percentage(target.pct_low)
        high = _percentage(target.pct_high)
        return f"{low}{RANGE_DASH}{high} % {name}"
    if target.low == target.high:
        return f"{_quantity(target.low)} {target.unit.value}"
    low = _quantity(target.low)
    high = _quantity(target.high)
    return f"{low}{RANGE_DASH}{high} {target.unit.value}"


def resolve_target(
    channel: Channel,
    target: Target,
    anchors: Mapping[AnchorType, PinnedAnchor],
) -> ResolvedTarget:
    """Resolve one channel target against the versions a session pinned.

    Args:
        channel: The channel the target is prescribed on.
        target: The target as frozen on the intent.
        anchors: The pinned anchor versions, by type.

    Returns:
        The target said both ways. The resolved bounds are ``None`` when the
        target is a percentage of an anchor absent from ``anchors``.
    """
    prescribed = render_target(target)
    unit = CHANNEL_UNITS[channel]
    if isinstance(target, AbsoluteRange):
        return ResolvedTarget(
            channel=channel,
            prescribed=prescribed,
            resolved_low=target.low,
            resolved_high=target.high,
            unit=unit,
            anchor_version_id=None,
        )
    pinned = anchors.get(target.anchor_type)
    if pinned is None:
        return ResolvedTarget(
            channel=channel,
            prescribed=prescribed,
            resolved_low=None,
            resolved_high=None,
            unit=unit,
            anchor_version_id=None,
        )
    value = pinned.version.value
    return ResolvedTarget(
        channel=channel,
        prescribed=prescribed,
        resolved_low=round(target.pct_low * value, RESOLVED_PRECISION),
        resolved_high=round(target.pct_high * value, RESOLVED_PRECISION),
        unit=unit,
        anchor_version_id=pinned.version_id,
    )


def _resolve_all(
    targets: Targets, anchors: Mapping[AnchorType, PinnedAnchor]
) -> tuple[ResolvedTarget, ...]:
    """Resolve a step's per-channel targets, in the channel enum's order."""
    return tuple(
        resolve_target(channel, targets[channel], anchors)
        for channel in Channel
        if channel in targets
    )


def resolve_steps(
    body: WorkoutBody, anchors: Mapping[AnchorType, PinnedAnchor]
) -> tuple[ResolvedStep, ...]:
    """Flatten a prescription and resolve every step's targets.

    A strength prescription resolves to nothing: its loads are kilograms,
    reps and RPE, none of which is an anchor percentage. An empty tuple rather
    than a refusal, so a caller can hand over whichever body it has.
    """
    if isinstance(body, StrengthWorkout):
        return ()
    return tuple(_resolved_step(body, anchors))


def _resolved_step(
    workout: EnduranceWorkout, anchors: Mapping[AnchorType, PinnedAnchor]
) -> list[ResolvedStep]:
    """Resolve each flattened step of an endurance prescription."""
    return [
        ResolvedStep(
            index=flat.index,
            role=flat.role,
            name=flat.step.name,
            duration_s=flat.duration_s,
            distance_m=flat.distance_m,
            is_ramp=flat.is_ramp,
            start_targets=_resolve_all(flat.start_targets, anchors),
            end_targets=_resolve_all(flat.end_targets, anchors),
        )
        for flat in flatten(workout)
    ]
