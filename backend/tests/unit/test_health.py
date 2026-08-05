from httpx import AsyncClient


async def test_health_returns_ok(anon_client: AsyncClient) -> None:
    """`/health` is served without a session — every healthcheck depends on it."""
    response = await anon_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
