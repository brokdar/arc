"""Offset/limit pagination primitives shared by all list endpoints."""

from typing import Annotated

from fastapi import Depends, Query
from pydantic import BaseModel


class PageParams(BaseModel):
    """Query parameters for paginated list endpoints."""

    offset: int = 0
    limit: int = 50


def page_params(
    # The upper bound keeps arbitrary-precision ints from overflowing the
    # database's bigint OFFSET (a 500 found by Schemathesis fuzzing).
    offset: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PageParams:
    """FastAPI dependency parsing pagination query parameters."""
    return PageParams(offset=offset, limit=limit)


PageParamsDep = Annotated[PageParams, Depends(page_params)]


class Page[T](BaseModel):
    """A single page of results plus the total count."""

    items: list[T]
    total: int
    offset: int
    limit: int
