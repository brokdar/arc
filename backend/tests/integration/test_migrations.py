"""Migration-chain guards.

These catch the two classic failure modes:
- model changed but no migration written (drift)
- migration written but downgrade path broken (irreversible chain)
"""

from alembic import command
from alembic.config import Config


def test_no_model_migration_drift(alembic_config: Config) -> None:
    """`alembic check` fails if autogenerate would produce a new revision."""
    command.check(alembic_config)


def test_migration_chain_roundtrips(alembic_config: Config) -> None:
    """head -> base -> head must work; broken downgrades block rollbacks."""
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
