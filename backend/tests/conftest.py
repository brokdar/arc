"""Test-wide guard: the suite never reads the developer's `.env`.

`Settings` deliberately reads the repo-root `.env` so host processes started
from `backend/` see what `just init` wrote (see `app/core/config.py`). Tests
must not: a run's outcome would then depend on whose machine it is on — a
random POSTGRES__PASSWORD, POSTGRES__HOST=db, real MCP keys. Disabling the
dotenv source here (once, at collection time, before any fixture or test
module constructs `Settings`) leaves the process environment as the only
input, which tests set explicitly with `monkeypatch.setenv`.

A test that needs dotenv behaviour can still pass `_env_file=<path>` itself.
"""

from app.core.config import Settings

Settings.model_config["env_file"] = None
