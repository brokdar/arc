"""Pure domain layer — entities, value objects, and business rules.

This package must stay free of I/O and framework code: no SQLAlchemy, no
FastAPI, no database or HTTP access — the standard library and pydantic (for
data modelling only) — so the rules are trivially testable and reusable from
any adapter (API, MCP, ingest, scheduler).

The purity rule is enforced by the import-linter contracts in
``backend/pyproject.toml`` — CI fails if this package grows a dependency on
any outer layer.

**Domain values are frozen, slotted dataclasses plus ``StrEnum``, not pydantic
models**, with invariants enforced in ``__post_init__`` and signalled as
``ValueError``. Pydantic is *permitted* here and the purity contract allowlists
it, so this is a choice rather than a constraint: a schema's job is to accept
and coerce whatever arrives on the wire, and a domain value's job is to be
impossible to construct in an illegal state. One type for both makes the
coercion rules — string-to-date, int-to-float, extra-field handling — into
domain semantics by accident, and leaves the domain inheriting ``model_config``
decisions taken for HTTP reasons. Frozen dataclasses also give free structural
sharing via ``dataclasses.replace``, hashability, and — the deciding factor —
they cost nothing to construct, which matters because the metric path
constructs them per data point.

The price is that domain violations arrive as ``ValueError``, which is not an
``AppError`` (the domain may not import ``app.core``).
``app.core.exceptions.domain_rules()`` is the one-line context manager services
wrap construction in, turning them into 422s carrying the domain's own message
— so the rule and its wording stay here rather than being restated in a schema.
"""
