"""A 405 names every method its path answers, not the first route's.

Found by Schemathesis (`allow_header_conformance`, new in 4.30): FastAPI
registers one Starlette route per method, and Starlette answers a method no
route on the path takes with the ``Allow`` header of the *first* route whose
path matched. ``OPTIONS /api/v1/anchors`` said ``Allow: GET`` while POST was
right there in the contract — RFC 9110 requires the full set.
"""

import re

from httpx import AsyncClient

ANCHORS = "/api/v1/anchors"


def _allowed(response_header: str) -> set[str]:
    return {method.strip() for method in response_header.split(",")}


async def test_every_path_advertises_every_method_it_serves(
    client: AsyncClient,
) -> None:
    spec = (await client.get("/openapi.json")).json()

    for path, operations in spec["paths"].items():
        # An operation with no 2xx response is a documented refusal (the
        # append-only anchor handlers): the method is answered, not served.
        served = {
            method.upper()
            for method, operation in operations.items()
            if any(code.startswith("2") for code in operation["responses"])
        }
        # A placeholder id is enough: Starlette refuses the method before
        # any path parameter is validated.
        concrete = re.sub(r"\{[^}]+\}", "x", path)

        response = await client.options(concrete)

        assert response.status_code == 405, (path, response.text)
        assert _allowed(response.headers["allow"]) - {"HEAD"} == served, path


async def test_an_anchor_version_advertises_only_get(client: AsyncClient) -> None:
    # PUT, PATCH and DELETE are routed, but only to refuse; advertising them
    # would contradict the `Allow: GET` those very handlers send.
    response = await client.options(f"{ANCHORS}/x")

    assert response.status_code == 405
    assert _allowed(response.headers["allow"]) - {"HEAD"} == {"GET"}
