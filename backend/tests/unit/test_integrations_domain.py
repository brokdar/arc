"""AC-1 and AC-2: the integration vocabulary, and what the model can carry.

Every assertion here iterates :data:`CATALOGUE` rather than naming members, so
the invariants hold for the integration added next as well as for the two arc
ships today. The one deliberate exception is the pair of literals AC-1 calls
for — the catalogue's membership and Wahoo's default path — which exist
precisely because they are the things a later edit would get wrong silently.
"""

from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from app.domain.connections import ConnectionProvider, normalise_remote_path
from app.domain.integrations import (
    CATALOGUE,
    DataKind,
    IntegrationKind,
    IntegrationSpec,
    StorageProvider,
    TransportKind,
    TransportSpec,
)

# --- AC-1: the invariants every spec in the catalogue holds ------------------


def test_every_catalogue_spec_declares_at_least_one_transport() -> None:
    for kind, spec in CATALOGUE.items():
        assert spec.transports, f"{kind} declares no transport"


def test_storage_is_set_exactly_for_the_cloud_folder_transports() -> None:
    for kind, spec in CATALOGUE.items():
        for transport in spec.transports:
            assert (transport.storage is not None) is (
                transport.kind is TransportKind.CLOUD_FOLDER
            ), f"{kind}/{transport.kind} storage={transport.storage!r}"


def test_only_a_cloud_folder_transport_carries_a_default_path() -> None:
    for kind, spec in CATALOGUE.items():
        for transport in spec.transports:
            if transport.kind is not TransportKind.CLOUD_FOLDER:
                assert transport.default_path is None, f"{kind}/{transport.kind}"


def test_every_default_path_is_already_normalised() -> None:
    # The whole point of storing them pre-normalised: the backfill and the
    # 409 both compare a *stored* path against these, and `/Apps/WahooFitness`
    # would never match either.
    for kind, spec in CATALOGUE.items():
        for transport in spec.transports:
            if transport.default_path is None:
                continue
            assert transport.default_path == normalise_remote_path(
                transport.default_path
            ), f"{kind} default_path is not the spelling arc stores"


def test_the_catalogue_is_exactly_the_local_drop_and_wahoo() -> None:
    # Locked decision: the catalogue ships only what works. Anything else the
    # model can express is proven by the test below, not by an entry here.
    assert set(CATALOGUE) == {IntegrationKind.LOCAL_DROP, IntegrationKind.WAHOO}


def test_wahoo_defaults_to_the_stored_spelling_of_the_wahoo_folder() -> None:
    # Asserted as a literal because `/Apps/WahooFitness` is the natural thing
    # to write and fails silently at comparison time — a feed row stores
    # `/apps/wahoofitness`, so an un-normalised default would classify nothing.
    (transport,) = CATALOGUE[IntegrationKind.WAHOO].transports
    assert transport.default_path == "/apps/wahoofitness"
    assert transport.storage is StorageProvider.DROPBOX


def test_the_storage_provider_is_the_connection_provider_enum() -> None:
    # Reused rather than renamed: a second enum would let a connection row and
    # a transport spec disagree about what "dropbox" is.
    assert StorageProvider is ConnectionProvider


def test_an_integration_spec_is_frozen() -> None:
    spec = CATALOGUE[IntegrationKind.WAHOO]

    with pytest.raises(FrozenInstanceError):
        spec.display_name = "Something else"  # type: ignore[misc]


def test_a_transport_spec_is_frozen() -> None:
    (transport,) = CATALOGUE[IntegrationKind.WAHOO].transports

    with pytest.raises(FrozenInstanceError):
        transport.default_path = "/elsewhere"  # type: ignore[misc]


# --- AC-2: the model carries an integration arc does not ship ----------------


def future_spec(
    kind: str,
    display_name: str,
    provides: frozenset[DataKind],
    transports: tuple[TransportSpec, ...],
) -> IntegrationSpec:
    """Build a spec for an integration arc does not ship.

    `kind` is cast rather than added to :class:`IntegrationKind`: that enum
    names what arc *ships*, and the point of these tests is that the shape
    carries an integration which is deliberately not in it. A `StrEnum` member
    is a `str`, so the cast instructs the type checker and lies to nobody at
    runtime.
    """
    return IntegrationSpec(
        kind=cast(IntegrationKind, kind),
        display_name=display_name,
        provides=provides,
        transports=transports,
    )


def assert_holds_ac1_invariants(spec: IntegrationSpec) -> None:
    """The AC-1 invariants, applied to a spec that is not in the catalogue."""
    assert spec.transports
    assert spec.provides
    for transport in spec.transports:
        assert (transport.storage is not None) is (
            transport.kind is TransportKind.CLOUD_FOLDER
        )
        if transport.kind is not TransportKind.CLOUD_FOLDER:
            assert transport.default_path is None
        if transport.default_path is not None:
            assert transport.default_path == normalise_remote_path(
                transport.default_path
            )


def test_the_model_carries_an_integration_arc_does_not_ship() -> None:
    before = dict(CATALOGUE)

    apple_workouts = future_spec(
        "apple_workouts",
        "Apple Workouts",
        # One data kind, not two. "Apple Workouts provides rides *and*
        # strength" is a statement about the files, and `SessionDiscipline`
        # reads that out of each one — see the `DataKind` docstring.
        frozenset({DataKind.RECORDINGS}),
        (
            TransportSpec(
                kind=TransportKind.CLOUD_FOLDER,
                storage=StorageProvider.DROPBOX,
                default_path="/apps/healthfit",
            ),
        ),
    )
    apple_health = future_spec(
        "apple_health",
        "Apple Health",
        frozenset({DataKind.WELLNESS}),
        (TransportSpec(kind=TransportKind.OAUTH_API),),
    )
    garmin = future_spec(
        "garmin",
        "Garmin",
        frozenset({DataKind.RECORDINGS}),
        (
            TransportSpec(kind=TransportKind.OAUTH_API),
            TransportSpec(
                kind=TransportKind.CLOUD_FOLDER,
                storage=StorageProvider.DROPBOX,
                default_path="/apps/garmin",
            ),
        ),
    )

    for spec in (apple_workouts, apple_health, garmin):
        assert isinstance(spec, IntegrationSpec)
        assert_holds_ac1_invariants(spec)

    assert apple_workouts.provides == frozenset({DataKind.RECORDINGS})
    assert apple_health.provides == frozenset({DataKind.WELLNESS})
    # Constructing them added nothing: the catalogue is what the athlete is
    # offered, and it still holds exactly what arc can deliver.
    assert dict(CATALOGUE) == before
    assert set(CATALOGUE) == {IntegrationKind.LOCAL_DROP, IntegrationKind.WAHOO}


def test_a_spec_that_provides_nothing_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="provides"):
        future_spec(
            "garmin",
            "Garmin",
            frozenset(),
            (TransportSpec(kind=TransportKind.OAUTH_API),),
        )


def test_a_spec_with_no_transport_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="transport"):
        future_spec("garmin", "Garmin", frozenset({DataKind.RECORDINGS}), ())


def test_a_cloud_folder_transport_without_a_storage_provider_is_refused() -> None:
    with pytest.raises(ValueError, match="storage"):
        TransportSpec(kind=TransportKind.CLOUD_FOLDER, default_path="/apps/x")


def test_a_non_cloud_transport_carrying_storage_is_refused() -> None:
    with pytest.raises(ValueError, match="storage"):
        TransportSpec(kind=TransportKind.OAUTH_API, storage=StorageProvider.DROPBOX)


def test_a_non_cloud_transport_carrying_a_default_path_is_refused() -> None:
    with pytest.raises(ValueError, match="default_path"):
        TransportSpec(kind=TransportKind.LOCAL_FOLDER, default_path="/inbox")


def test_an_unnormalised_default_path_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="normalised"):
        TransportSpec(
            kind=TransportKind.CLOUD_FOLDER,
            storage=StorageProvider.DROPBOX,
            default_path="/Apps/WahooFitness/",
        )


# --- the locked decision the DataKind docstring carries ----------------------


def test_discipline_is_never_declared_by_an_integration() -> None:
    """`DataKind` names arc's destination subsystem, never the sport.

    The enum having exactly two members *is* the assertion: a vocabulary with
    `rides` and `strength` in it would be a configuration claiming to know
    what wrote a file, which `SessionDiscipline` says a folder cannot.
    """
    assert {kind.value for kind in DataKind} == {"recordings", "wellness"}
    docstring = DataKind.__doc__ or ""
    assert "SessionDiscipline" in docstring
    for spec in CATALOGUE.values():
        assert spec.provides <= frozenset(DataKind)
