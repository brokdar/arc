"""The runtime data tree is created on application startup."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point DATA__ROOT at a throwaway directory (settings are cached)."""
    root = tmp_path / "runtime-data"
    monkeypatch.setenv("DATA__ROOT", str(root))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


async def test_startup_creates_the_data_tree(data_root: Path) -> None:
    app = create_app()

    async with LifespanManager(app):
        assert (data_root / "inbox").is_dir()
        assert (data_root / "originals").is_dir()
        assert (data_root / "streams").is_dir()
        assert (data_root / "quarantine").is_dir()
