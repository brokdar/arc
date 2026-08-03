from app.worker.tasks import example_task


async def test_example_task_returns_greeting() -> None:
    result = await example_task({"job_id": "test-job"}, "world")

    assert result == "hello world"
