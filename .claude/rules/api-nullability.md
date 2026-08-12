---
paths: backend/app/api/routes/**, backend/app/api/schemas/**
---

# `null` appears in the contract only where `null` is a thing a client may say

Both halves use the same idiom — `X | SkipJsonSchema[None]` from
`pydantic.json_schema`, which keeps the Python-side `= None` default while
dropping the `null` branch from the published schema — for two different
reasons.

## Optional query parameters are optional by omission, never nullable

Type an optional query parameter `X | SkipJsonSchema[None]`, not `X | None`. A
plain `X | None` makes the OpenAPI contract advertise `null` as a legal value,
but a query string delivers `?param=null` as the four-letter string, which the
parser rejects with a 422 — a schema/validation mismatch the Schemathesis CI
job fails on (`API rejected schema-compliant request`).

Pinned by `test_optional_query_params_do_not_advertise_null` in
`backend/tests/unit/test_anchors_api.py`, which sweeps every query parameter in
the spec — a new offender fails that test locally before CI's fuzzer finds it.

## An update payload is nullable only where `null` means "clear this"

In a PATCH body the two states are *omitted* ("leave it alone") and *present*
("set it to this"). `null` is a third thing and it means "clear this", so a
field only takes it when clearing is an operation the resource allows. A
session always has a discipline and always has a timezone, so `SessionUpdate`
types both `X | SkipJsonSchema[None]`; `rpe` and `temperature_c` are genuinely
clearable and take `X | None`.

One trap, which reaches the client as a 500 rather than a 422: a
`Field(max_length=...)` beside a `X | SkipJsonSchema[None]` default is applied
to the **whole union**, and `len(None)` is a `TypeError` inside pydantic's
validator. Constrain the member, not the union — `Annotated[str, Field(...)] |
SkipJsonSchema[None]`.
