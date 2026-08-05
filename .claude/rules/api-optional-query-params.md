---
paths: backend/app/api/routes/**
---

# Optional query parameters are optional by omission, never nullable

Type an optional query parameter `X | SkipJsonSchema[None]` (from
`pydantic.json_schema`), not `X | None`. A plain `X | None` makes the OpenAPI
contract advertise `null` as a legal value, but a query string delivers
`?param=null` as the four-letter string, which the parser rejects with a
422 — a schema/validation mismatch the Schemathesis CI job fails on
(`API rejected schema-compliant request`).

`SkipJsonSchema[None]` keeps the Python-side `= None` default while dropping
the `null` branch from the contract, so the schema promises exactly what the
parser accepts. Pinned by `test_optional_query_params_do_not_advertise_null`
in `backend/tests/unit/test_anchors_api.py`, which sweeps every query
parameter in the spec — a new offender fails that test locally before CI's
fuzzer finds it.
