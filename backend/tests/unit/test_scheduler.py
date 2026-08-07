from pathlib import Path

from asgi_lifespan import LifespanManager

from app.core.config import get_settings
from app.ingest.inbox import INBOX_JOB_ID
from app.main import create_app


async def test_scheduler_runs_for_the_lifetime_of_the_app() -> None:
    app = create_app()

    async with LifespanManager(app):
        scheduler = app.state.scheduler
        assert scheduler is not None
        assert scheduler.running is True

    assert scheduler.running is False


async def test_the_inbox_sweep_is_registered_at_startup(data_root: Path) -> None:
    # The watched folder only exists if the lifespan wires it up; the job
    # function's own behaviour is tested in `test_ingest_inbox`.
    app = create_app()

    async with LifespanManager(app):
        job = app.state.scheduler.get_job(INBOX_JOB_ID)

        assert job is not None
        assert job.trigger.interval.total_seconds() == (
            get_settings().ingest.scan_interval_seconds
        )
        # One interval away, so a boot does not also sweep.
        assert job.next_run_time is not None
