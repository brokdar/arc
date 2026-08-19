"""What arc collects, where it comes from, and what the athlete configures.

A module of its own rather than an addition to `app.domain.connections`, and
the split is the point. That module is about **credential health for a storage
provider** — is the Dropbox token alive, has the folder delivered. This one is
about **what data the athlete asked arc to collect**. Folded together, one enum
would have to mean both "a service arc holds an OAuth token for" and "a source
of training data", and the two are not the same list: Dropbox is a transport
that carries Wahoo's rides and could carry Apple's, while Wahoo is a source
that could arrive over a folder, an API, or a cable.

The vocabulary here is deliberately small and closed:

* :class:`DataKind` — which of arc's two ingest destinations consumes it;
* :class:`TransportKind` — how the bytes get to arc;
* :class:`StorageProvider` — for a cloud folder, whose folder it is;
* :class:`IntegrationKind` and :data:`CATALOGUE` — what arc actually ships.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from app.domain.connections import ConnectionProvider, normalise_remote_path


class DataKind(StrEnum):
    """Which arc subsystem consumes what an integration brings in.

    **Two members, because arc has exactly two destinations**: a file becomes
    a recording (`IngestPipeline.ingest_file` → `recordings` → `sessions` and
    `logged_sets`), or a reading becomes a wellness day (`WellnessService`
    → `wellness_days`). Nothing else in arc eats ingested data.

    This is emphatically **not the sport**. "Apple Workouts provides rides and
    strength training" is one `DataKind` — `recordings` — not two, because arc
    classifies each file itself: `app.domain.activity.SessionDiscipline` exists
    because "a device file ... can hold a walk, a swim or a sport the head unit
    does not name", and a folder cannot tell you what wrote a file nor what
    sport it was. A vocabulary of `rides | strength | wellness` would put a
    guess about the sport into configuration, where nothing can correct it,
    and every disagreement between the guess and the file would be resolved in
    favour of the guess. `IngestSource` already makes this argument for where a
    file came from; this extends it rather than overturning it.
    """

    RECORDINGS = "recordings"
    WELLNESS = "wellness"


class TransportKind(StrEnum):
    """How the bytes reach arc.

    * ``local_folder`` — a directory on the arc server, swept on a timer. The
      `data/inbox/` sweep that has run since WP-4.3.
    * ``cloud_folder`` — a folder on a :class:`StorageProvider` arc holds a
      credential for, polled with a cursor. Dropbox today.
    * ``oauth_api`` — a per-vendor API arc calls with its own OAuth grant.
      Expressible, deliberately unimplemented: it is what makes Garmin and
      Strava statable in the model without pretending arc can deliver them.
    """

    LOCAL_FOLDER = "local_folder"
    CLOUD_FOLDER = "cloud_folder"
    OAUTH_API = "oauth_api"


#: Whose cloud folder a ``cloud_folder`` transport reads.
#:
#: The **same enum** as `app.domain.connections.ConnectionProvider`, aliased
#: rather than renamed or copied. A provider is an axis of one transport kind,
#: not a transport kind of its own, and a second enum spelling "dropbox" would
#: let a connection row and a transport spec disagree about what that word
#: means. A rename would churn every existing call site for no behaviour
#: change; the alias buys the vocabulary this module needs and costs nothing.
StorageProvider = ConnectionProvider


class IntegrationKind(StrEnum):
    """The sources arc ships. Exactly the keys of :data:`CATALOGUE`.

    Members are added the day arc can actually collect from that source, never
    ahead of it — see the :data:`CATALOGUE` docstring for why nothing is listed
    as "coming soon".
    """

    LOCAL_DROP = "local_drop"
    WAHOO = "wahoo"


@dataclass(frozen=True, slots=True)
class TransportSpec:
    """One way an integration can be collected.

    Frozen, and validated at construction, because these are read as facts by
    the service (which folder to propose), the API (what the add flow offers)
    and the migration's backfill (which stored path means which integration).
    A spec that broke one of the three rules below would fail silently at
    exactly one of those three sites.
    """

    kind: TransportKind
    #: Set **iff** :attr:`kind` is ``cloud_folder``; ``None`` otherwise.
    storage: StorageProvider | None = None
    #: The folder this integration writes to by default, in the spelling arc
    #: **stores** — already through `normalise_remote_path`. Only a
    #: ``cloud_folder`` transport has one.
    default_path: str | None = None

    def __post_init__(self) -> None:
        """Refuse a spec that breaks one of the three rules above."""
        cloud = self.kind is TransportKind.CLOUD_FOLDER
        if (self.storage is not None) is not cloud:
            raise ValueError(
                f"a {self.kind.value} transport must "
                f"{'name' if cloud else 'not name'} a storage provider"
            )
        if not cloud and self.default_path is not None:
            raise ValueError(
                f"a {self.kind.value} transport has no default_path — only a "
                "cloud folder is addressed by a remote path"
            )
        if self.default_path is not None and self.default_path != normalise_remote_path(
            self.default_path
        ):
            raise ValueError(
                f"default_path {self.default_path!r} is not normalised; store "
                f"{normalise_remote_path(self.default_path)!r} instead"
            )


@dataclass(frozen=True, slots=True)
class IntegrationSpec:
    """One source of training data, and everything the athlete decides about it."""

    kind: IntegrationKind
    #: What the athlete calls it — "Wahoo", never "Dropbox" and never a path.
    display_name: str
    #: Which of arc's destinations it feeds. Never a sport.
    provides: frozenset[DataKind] = field(default_factory=frozenset)
    #: Non-empty; the **first** is the default the add flow offers.
    transports: tuple[TransportSpec, ...] = ()

    def __post_init__(self) -> None:
        """Refuse a source that feeds nothing or cannot be reached."""
        if not self.provides:
            raise ValueError(
                f"{self.kind} provides nothing — an integration that feeds no "
                "destination is a source arc would collect and then drop"
            )
        if not self.transports:
            raise ValueError(
                f"{self.kind} declares no transport, so there is no way for "
                "its data to reach arc"
            )

    def transport(self, kind: TransportKind) -> TransportSpec | None:
        """This integration's spec for one transport kind, or ``None``."""
        return next((row for row in self.transports if row.kind is kind), None)


#: Every source arc can actually collect from, and nothing else.
#:
#: **The catalogue ships only what works.** No "coming soon" row, no greyed-out
#: Strava: an entry here is offered to the athlete in Settings, and an entry
#: the athlete can pick but arc cannot deliver is a promise the application
#: breaks at the one moment it asked for trust. That the model *can* carry
#: Garmin, Apple Workouts, Apple Health, Strava and Zwift is proven by
#: `tests/unit/test_integrations_domain.py::
#: test_the_model_carries_an_integration_arc_does_not_ship`, which constructs
#: those specs and adds none of them here.
#:
#: A `MappingProxyType` rather than a plain dict: this is read from the
#: service, the API and the panel, and a caller that mutated it would change
#: what arc offers for the life of the process with nothing recording it.
CATALOGUE: Mapping[IntegrationKind, IntegrationSpec] = MappingProxyType(
    {
        IntegrationKind.LOCAL_DROP: IntegrationSpec(
            kind=IntegrationKind.LOCAL_DROP,
            display_name="Local drop",
            provides=frozenset({DataKind.RECORDINGS}),
            transports=(TransportSpec(kind=TransportKind.LOCAL_FOLDER),),
        ),
        IntegrationKind.WAHOO: IntegrationSpec(
            kind=IntegrationKind.WAHOO,
            display_name="Wahoo",
            provides=frozenset({DataKind.RECORDINGS}),
            transports=(
                TransportSpec(
                    kind=TransportKind.CLOUD_FOLDER,
                    storage=StorageProvider.DROPBOX,
                    # Pre-normalised: a feed row stores `/apps/wahoofitness`,
                    # and the backfill and the folder-clash refusal both
                    # compare against this literal.
                    default_path="/apps/wahoofitness",
                ),
            ),
        ),
    }
)

#: The one integration that is **synthesized, never stored**.
#:
#: It always exists, cannot be added and cannot be removed: `data/inbox/` has
#: been swept since WP-4.3 and is configured by `DATA__ROOT`, not by a row. A
#: migration-created row would be one the athlete could delete and never get
#: back, leaving a running sweep with nothing in Settings describing it.
SYNTHESIZED_KINDS = frozenset({IntegrationKind.LOCAL_DROP})


def addable_kinds() -> tuple[IntegrationKind, ...]:
    """Catalogue entries the athlete may actually add, in catalogue order."""
    return tuple(kind for kind in CATALOGUE if kind not in SYNTHESIZED_KINDS)


def ordered_data_kinds(provides: frozenset[DataKind]) -> tuple[DataKind, ...]:
    """`provides` in a stable order — a frozenset has none, and JSON needs one."""
    return tuple(kind for kind in DataKind if kind in provides)


def kind_for_default_path(path: str) -> IntegrationKind | None:
    """The catalogue integration whose default folder is ``path``, if any.

    Compared against the **stored** spelling, which is why every
    :attr:`TransportSpec.default_path` is normalised at construction.
    """
    normalised = normalise_remote_path(path)
    for kind, spec in CATALOGUE.items():
        for transport in spec.transports:
            if transport.default_path is not None and (
                transport.default_path == normalised
            ):
                return kind
    return None
