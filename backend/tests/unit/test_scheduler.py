from asgi_lifespan import LifespanManager

from app.main import create_app


async def test_scheduler_runs_for_the_lifetime_of_the_app() -> None:
    app = create_app()

    async with LifespanManager(app):
        scheduler = app.state.scheduler
        assert scheduler is not None
        assert scheduler.running is True

    assert scheduler.running is False
