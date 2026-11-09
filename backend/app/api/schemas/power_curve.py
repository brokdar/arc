"""Response schema for a session's power curve.

Two shapes behind one ``status`` discriminator, because a curve and its
absence are different answers rather than one answer with holes in it. A
session with no power meter is ``not_assessed`` with a reason that names the
missing input — never a curve of zeros, which would read as a ride the athlete
rode at nothing.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class PowerCurvePointRead(BaseModel):
    """The best window of one duration."""

    #: Window length in seconds — a point of the grid `grid_version` names.
    duration_s: int
    #: The window's mean power, in watts, to 0.1 W.
    watts: float
    #: First row of the window on the session's 1 Hz stream grid; the window
    #: is `[start_index, start_index + duration_s)`.
    start_index: int
    #: False when the window contains seconds arc filled in (a gap too long to
    #: bridge) or a region the cleaner repaired.
    clean: bool


class AssessedPowerCurveRead(BaseModel):
    """A session's mean-maximal power at every grid duration it is long enough for."""

    status: Literal["assessed"] = "assessed"
    #: Which duration grid the points are drawn from (`GRID_VERSION`).
    grid_version: int
    #: Ascending by duration. A duration longer than the ride is absent.
    points: list[PowerCurvePointRead]


class NotAssessedPowerCurveRead(BaseModel):
    """Why a session has no power curve, naming the missing input."""

    status: Literal["not_assessed"] = "not_assessed"
    reason: str


PowerCurveRead = Annotated[
    AssessedPowerCurveRead | NotAssessedPowerCurveRead,
    Field(discriminator="status"),
]
