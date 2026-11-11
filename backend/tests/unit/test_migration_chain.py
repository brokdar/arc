"""The migration history is one straight chain with one head.

Two PRs built in parallel each wrote a migration `0028` off `0027`; both
merged, and `alembic upgrade head` on main failed with "Multiple head
revisions" (PR #138 after #142, 11 Nov 2026). The integration tier caught it
only after the merge, because it runs in CI, not in `just gate`. Reading the
script directory needs no database, so this runs with the unit tests and
fails the gate before a push instead.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[2]


def _scripts() -> ScriptDirectory:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option(
        "script_location", str(BACKEND / "app" / "persistence" / "alembic")
    )
    return ScriptDirectory.from_config(config)


def test_there_is_exactly_one_head() -> None:
    heads = _scripts().get_heads()
    assert len(heads) == 1, (
        f"migration heads: {sorted(heads)} — renumber one onto the other"
    )


def test_every_revision_id_is_used_once() -> None:
    # A duplicate id is silently shadowed when the script directory loads
    # (alembic only warns), so count the files, not the loaded revisions.
    versions = BACKEND / "app" / "persistence" / "alembic" / "versions"
    ids = [
        line.split("=", 1)[1].strip().strip("\"'")
        for path in sorted(versions.glob("*.py"))
        for line in path.read_text().splitlines()
        if line.startswith("revision: str =") or line.startswith("revision =")
    ]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    assert duplicates == [], f"revision ids used more than once: {duplicates}"


def test_each_file_number_matches_its_revision_id() -> None:
    # `0029_sync_outbox.py` must say revision "0029": the file name is what a
    # reader and the plan's migration table go by.
    versions = BACKEND / "app" / "persistence" / "alembic" / "versions"
    for path in sorted(versions.glob("[0-9][0-9][0-9][0-9]_*.py")):
        number = path.name.split("_", 1)[0]
        text = path.read_text()
        assert f'revision: str = "{number}"' in text, (
            f"{path.name} does not declare revision {number}"
        )
