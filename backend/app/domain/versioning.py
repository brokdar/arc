"""Versioning primitives shared by every derived artefact.

Invariant 1 of the build plan: originals are immutable, recomputation creates
a *new* version and never overwrites, and every derived artefact (score,
metric, alignment) carries a version chain plus the timestamp it was computed
at. The vocabulary is fixed here once so that every later work package spells
it the same way:

``artefact_id``
    Stable identity of the thing being versioned — constant across every
    version of it. Two rows with the same ``artefact_id`` are two versions of
    one artefact, not two artefacts.
``version``
    1-based, strictly increasing within an ``artefact_id``.
``as_of``
    Aware-UTC instant the version was computed. This is the axis
    :func:`version_as_seen_at` searches; it is not the ingestion time of the
    underlying data.
``superseded_by``
    Id of the version that replaced this one, or ``None`` for the tip of the
    chain. Set on the *old* version when a new one is written — the chain is
    a linked list, so a reader holding an old id can walk forward.
``recompute_reason``
    Free text on the *new* version saying why the recomputation happened
    (``"intent edited"``, ``"anchor changed"``). ``None`` on version 1.

Two shapes are provided because both are needed. :class:`VersionRecord` is a
structural protocol, so the helpers below work on anything carrying the five
fields — ORM rows included, without the domain knowing SQLAlchemy exists.
:class:`Versioned` is a concrete generic envelope for pure in-memory use.
"""

import datetime as dt
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Protocol, Self, runtime_checkable

#: Version number of the first version of any artefact.
FIRST_VERSION = 1


@runtime_checkable
class VersionRecord(Protocol):
    """One version of a derived artefact.

    Structural on purpose: persistence rows satisfy it by having the columns,
    so the helpers in this module are usable from every layer without the
    domain importing any of them.

    The members are read-only *properties* rather than mutable attributes.
    A protocol declaring `version: int` demands a settable attribute, which no
    frozen dataclass has — so the immutable values this layer is made of would
    be the one thing that could not satisfy it.
    """

    @property
    def artefact_id(self) -> uuid.UUID:
        """Stable identity of the artefact, constant across its versions."""

    @property
    def version(self) -> int:
        """1-based position in the chain."""

    @property
    def as_of(self) -> dt.datetime:
        """Aware-UTC instant this version was computed."""

    @property
    def superseded_by(self) -> uuid.UUID | None:
        """Id of the version that replaced this one, if any."""

    @property
    def recompute_reason(self) -> str | None:
        """Why this version was computed, or ``None`` for version 1."""


@dataclass(frozen=True, slots=True)
class Versioned[T]:
    """A payload plus its position in a version chain.

    The envelope for pure computations that have no database row yet. It
    satisfies :class:`VersionRecord`, so it can be passed to the helpers below
    interchangeably with persisted versions.
    """

    #: Identity of *this* version (not of the artefact).
    id: uuid.UUID
    artefact_id: uuid.UUID
    version: int
    as_of: dt.datetime
    payload: T
    superseded_by: uuid.UUID | None = None
    recompute_reason: str | None = None

    def __post_init__(self) -> None:
        """Reject version chains that could not have been produced legally."""
        if self.version < FIRST_VERSION:
            raise ValueError(f"version must be >= {FIRST_VERSION}, got {self.version}")
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware UTC")
        if self.version == FIRST_VERSION and self.recompute_reason is not None:
            raise ValueError(
                "version 1 is the original computation and has no recompute_reason"
            )
        if self.superseded_by == self.id:
            raise ValueError("a version cannot supersede itself")

    @classmethod
    def first(cls, payload: T, *, as_of: dt.datetime) -> Self:
        """Start a new chain: version 1 of a brand-new artefact."""
        return cls(
            id=uuid.uuid7(),
            artefact_id=uuid.uuid7(),
            version=FIRST_VERSION,
            as_of=as_of,
            payload=payload,
        )

    def recomputed(
        self, payload: T, *, as_of: dt.datetime, reason: str
    ) -> tuple[Self, Self]:
        """Return ``(superseded_self, next_version)``.

        Both halves are returned because both change: the caller must persist
        the closed-off old version as well as the new tip, or a reader walking
        the chain stops one link early.
        """
        if not reason:
            raise ValueError("a recomputation must state its reason")
        successor = replace(
            self,
            id=uuid.uuid7(),
            version=self.version + 1,
            as_of=as_of,
            payload=payload,
            superseded_by=None,
            recompute_reason=reason,
        )
        return replace(self, superseded_by=successor.id), successor


def current_version[V: VersionRecord](versions: Iterable[V]) -> V | None:
    """Return the tip of the chain, or ``None`` when there is nothing.

    The tip is the highest ``version`` that nothing supersedes. Falling back
    to the highest version outright would hide a broken chain; a chain whose
    every link is superseded is a bug, and this returns ``None`` for it rather
    than inventing an answer.
    """
    unsuperseded = [version for version in versions if version.superseded_by is None]
    if not unsuperseded:
        return None
    return max(unsuperseded, key=lambda version: version.version)


def version_as_seen_at[V: VersionRecord](
    versions: Iterable[V], moment: dt.datetime
) -> V | None:
    """Return the version a reader would have seen at ``moment``.

    That is the newest version computed at or before ``moment`` — what a
    verdict, a score or an audit entry from that instant was actually looking
    at. Returns ``None`` when the artefact did not exist yet.

    Raises:
        ValueError: When ``moment`` is naive; comparing it against aware
            ``as_of`` values would raise deep inside `max`.
    """
    if moment.tzinfo is None:
        raise ValueError("moment must be timezone-aware UTC")
    visible = [version for version in versions if version.as_of <= moment]
    if not visible:
        return None
    return max(visible, key=lambda version: (version.as_of, version.version))


def next_version[V: VersionRecord](versions: Iterable[V]) -> int:
    """Return the version number the next link in the chain should carry."""
    return max((version.version for version in versions), default=0) + 1
