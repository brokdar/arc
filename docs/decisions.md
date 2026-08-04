# Decision log

Running record of every `DECIDE:` taken from `docs/mvp-build-plan.md` and every
ambiguity resolved while executing it (see mvp-build-plan §4). Each entry states
the choice, the alternative it displaced, and why. The operator reviews these.

Entries are append-only: supersede a decision with a new entry rather than
rewriting history.

---

## D1 — Repo layout: keep the template's `backend/` + `frontend/` split

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

Keep the template's two standalone projects, `backend/` and `frontend/`, instead
of the build plan's `apps/` + `packages/` monorepo driven by a uv workspace and
pnpm workspaces. The layer boundaries the plan calls for are preserved, but as
modules inside `backend/app/` — `domain` (pure, no I/O), `persistence`,
`ingest`, `services`, `api`, and `mcp` — with the dependency direction enforced
mechanically by import-linter contracts in CI rather than by physical package
splits. The MCP server ships as a second Compose service built from the same
backend image, differing only in its entrypoint.

*Rationale:* the template arrives with a fully verified CI, Docker, devcontainer,
and typed API-contract chain end to end; a workspace re-layout would invalidate
all of it for no behavioural gain. A previous workspace attempt already exists in
this repo's history and was abandoned (its orphaned bytecode under `packages/`
was removed in WP-0). Import-linter gives the same architectural guarantee that
separate distributions would, at a fraction of the packaging and tooling cost, so
the boundaries stay enforceable without paying for the split.

## D2 — Frontend toolchain: bun + Biome + TypeScript 5.9 with `tsgo`

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

Keep the template's bun package manager, Biome for lint and format, and
TypeScript 5.9 type-checked by `tsgo`, instead of the plan's pnpm + ESLint +
TypeScript 7.

*Rationale:* this is a naming difference more than a substantive one. `tsgo` *is*
the TypeScript 7 line — the native Go port of the compiler — so type-checking
already runs on the compiler generation the plan asks for. TypeScript 5.9 is
retained alongside it because the JavaScript API that `openapi-typescript` and
Next's own compiler call into is not yet available from the native build; keeping
both means the contract generator and the framework stay on supported paths while
checking happens on the fast native compiler. bun and Biome are already wired into
CI, pre-commit, and the devcontainer, and swapping them would buy nothing.

## D3 — Type checker: pyrefly, not pyright strict

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

Use pyrefly as the backend type checker instead of pyright in strict mode.

*Rationale:* pyrefly is already wired into CI, the pre-push hook, and the
devcontainer's editor extensions, so it is enforced at every layer a type error
could slip through. It provides equivalent strictness for this codebase, and
switching would mean re-tuning configuration and suppressions across the whole
backend for no additional signal.

## D4 — Python 3.14

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

Adopt Python 3.14 as pinned by the build plan, upgraded from the template's 3.13.
Applied to `backend/pyproject.toml` (`requires-python`, ruff `target-version`),
`backend/.python-version`, `backend/pyrefly.toml`, both stages of
`backend/Dockerfile`, and the devcontainer image.

*Rationale:* the plan pins 3.14 and nothing in the template depends on 3.13-only
behaviour, so taking the pin now avoids a disruptive migration later, once
migrations, the ingest pipeline, and the MCP server are all in place. The
`mcr.microsoft.com/devcontainers/python:3-3.14-trixie` tag was verified to exist
before the devcontainer was moved to it, so the container, the Docker images, and
the uv-managed local interpreter all resolve to the same minor version.

## D5 — Scheduling: in-process APScheduler, not ARQ + Redis

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

Replace the template's ARQ worker and its Redis dependency with APScheduler 3.11
running in-process inside the API service.

*Rationale:* the build plan explicitly forbids Redis for what is a single-user
application, and the workload — periodic ingest and maintenance jobs — has no
need for a distributed queue, multiple consumers, or cross-process durability. An
in-process scheduler removes an entire stateful service from Compose, from
deployment, and from the failure surface, while covering every job this
application actually schedules.

## D6 — Auth: single-user session cookie + static MCP bearer keys

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

Authenticate the human via a single-user bcrypt-hashed password establishing a
signed session cookie through Starlette's `SessionMiddleware`, and authenticate
MCP clients via static, scoped bearer keys. The template's unused JWT settings
shell is removed rather than left dormant.

*Rationale:* with exactly one human user there is no token-distribution or
multi-tenant problem for JWTs to solve, and a signed session cookie is both
simpler to reason about and easier to revoke — invalidating the session secret
logs the one user out. MCP clients are non-interactive and cannot perform a login
flow, so static scoped keys fit them directly. Deleting the JWT scaffolding keeps
one authentication path in the codebase, so there is no unused, unreviewed
credential code for a future reader to mistake for something live.

## D7 — Conventions kept from the template over the plan's

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

Where the build plan's stated conventions differ cosmetically from the
template's, the template wins:

| Concern | Kept (template) | Plan proposed |
|---|---|---|
| Logging | `structlog` | stdlib logging with JSON formatter |
| Task runner | `justfile` | `Makefile` |
| Ruff line length | 88 | 100 |
| Health endpoint | `/health` | `/healthz` |
| Env var style | unprefixed, nested `__` | `APP_`-prefixed |

*Rationale:* each of these is already threaded through CI workflows, Docker
health checks, `.env.example`, the devcontainer, and the existing test suite.
None affects behaviour or architecture, so changing them would mean touching
every one of those call sites and re-verifying the pipeline to end up somewhere
equivalent. Consistency with what is already verified is worth more than matching
the plan's incidental preferences.
