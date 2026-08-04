"""Pure domain layer — entities, value objects, and business rules.

This package must stay free of I/O and framework code: no SQLAlchemy, no
FastAPI, no database or HTTP access. Pydantic models, dataclasses, enums and
the standard library only, so the rules are trivially testable and reusable
from any adapter (API, MCP, ingest, scheduler).

The purity rule is enforced by the import-linter contracts in
``backend/pyproject.toml`` — CI fails if this package grows a dependency on
any outer layer.

Filled in by WP-1 (athlete, anchors, zones, versioning primitives).
"""
