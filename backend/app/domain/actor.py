"""Who performed a write.

Every mutating service takes an :class:`Actor` and (from WP-1) writes it to
`audit_log`. It lives in the domain because all three callers need it and none
of them may import each other: `app.api` supplies the athlete, `app.mcp`
supplies the agent behind the presented key, and the scheduler supplies
`system`. Framework-free, like everything in this layer.

The string form is what the audit column stores: ``athlete``, ``system``, or
``agent:<key-label>``. It round-trips through :meth:`Actor.parse`.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Self

#: Separates the kind from the label in the string form.
LABEL_SEPARATOR = ":"


class ActorKind(StrEnum):
    """The three kinds of writer the system recognises."""

    ATHLETE = "athlete"
    AGENT = "agent"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class Actor:
    """The identity credited with a write.

    Build one through :meth:`athlete`, :meth:`agent` or :meth:`system` rather
    than the constructor — they are the only three shapes that are valid.
    """

    kind: ActorKind
    #: The MCP key label, for agents only.
    label: str | None = None

    def __post_init__(self) -> None:
        """Reject the shapes the three constructors cannot produce."""
        if self.kind is ActorKind.AGENT:
            if not self.label:
                raise ValueError("An agent actor needs the label of its key")
            if LABEL_SEPARATOR in self.label:
                raise ValueError(
                    f"Agent label {self.label!r} may not contain "
                    f"{LABEL_SEPARATOR!r}: it would not survive the round-trip "
                    "through the stored string form"
                )
        elif self.label is not None:
            raise ValueError(f"A {self.kind.value} actor carries no label")

    @classmethod
    def athlete(cls) -> Self:
        """The single human user, acting through the web UI."""
        return cls(ActorKind.ATHLETE)

    @classmethod
    def agent(cls, label: str) -> Self:
        """The coaching agent, identified by its MCP key label."""
        return cls(ActorKind.AGENT, label.strip())

    @classmethod
    def system(cls) -> Self:
        """The application itself: scheduled jobs, ingest, migrations of data."""
        return cls(ActorKind.SYSTEM)

    @classmethod
    def parse(cls, raw: str) -> Self:
        """Rebuild an actor from its stored string form.

        Raises:
            ValueError: When ``raw`` is not a form :meth:`__str__` produces.
        """
        kind_name, separator, label = raw.partition(LABEL_SEPARATOR)
        try:
            kind = ActorKind(kind_name)
        except ValueError as exc:
            valid = ", ".join(sorted(member.value for member in ActorKind))
            raise ValueError(
                f"Unknown actor {raw!r}; expected one of: {valid}"
            ) from exc
        return cls(kind, label if separator else None)

    def __str__(self) -> str:
        """The stored form: ``athlete``, ``system`` or ``agent:<label>``."""
        if self.label is None:
            return self.kind.value
        return f"{self.kind.value}{LABEL_SEPARATOR}{self.label}"
