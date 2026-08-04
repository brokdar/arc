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

## D8 — A one-shot `data-init` service owns the `./data` bind mount

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

Compose runs a one-shot `data-init` service (`alpine:3`, `chown 1001:1001 /data`)
that the `api` service waits on via `condition: service_completed_successfully`.
The api creates the `inbox/`, `originals/`, `streams/`, `quarantine/`
subdirectories itself at startup.

*Rationale:* the Docker daemon creates a missing bind-mount source root-owned,
and the api image runs as a non-root user (uid 1001), so a fresh clone would
otherwise fail to write into `./data` on first boot. The alternatives are worse:
running the api as root defeats the image's hardening, and documenting a manual
`chown` makes "clone and `just up`" conditional on reading the README. One
short-lived container removes a whole class of first-run failure.

## D9 — Caddy deliberately does not depend on the `mcp` service

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

`caddy` declares `depends_on` for `api` and `frontend`, but not for `mcp`.

*Rationale:* Caddy resolves proxy upstreams per request, so the `/mcp*` route
starts working the moment the MCP server is up, with no restart. Waiting on it
would mean the entire site — UI included — stays down whenever `MCP__API_KEYS`
is unset and the MCP server exits (D10). With no dependency, a missing MCP
configuration costs a 502 on one route instead of the whole application.

## D10 — MCP auth: constant-time verification, and no keys means no server

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

`app/mcp/main.py` subclasses FastMCP's `TokenVerifier` rather than using the
shipped `StaticTokenVerifier`, delegating to `app/mcp/auth.verify_key`, which
compares every configured key with `secrets.compare_digest` and never returns
early. The server logs an error and exits 1 when `MCP__API_KEYS` is empty or
malformed.

*Rationale:* `StaticTokenVerifier` looks tokens up in a dict — a
non-constant-time comparison against secret material, and the timing signal is
reachable by anyone who can reach the port. Subclassing keeps all of FastMCP's
plumbing (`RequireAuthMiddleware`, the 401 with `WWW-Authenticate`) and replaces
only the comparison. Exiting on an empty key set is the safer failure: a server
that starts with no keys either rejects everything while looking healthy, or —
if a future refactor got auth wrong — serves an unauthenticated tool surface.
Refusing to boot makes the misconfiguration loud and immediate.

## D11 — The MCP server starts with FastMCP's banner disabled

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

`run(..., show_banner=False)`.

*Rationale:* the banner triggers FastMCP's PyPI update check, so leaving it on
means the service makes an outbound network call on every boot. A self-hosted
application should not phone home unprompted, and the banner itself is noise in
container logs that are otherwise structured JSON.

## D12 — The API is protected by default, not by remembering to protect it

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

Everything under `/api/v1` is mounted on a router that carries
`Depends(require_session)` and a declared 401 response. The exceptions —
`/api/v1/auth/*` and `/health` — are mounted separately and deliberately.

*Rationale:* with per-route dependencies, a new endpoint is public until someone
remembers to guard it, and the failure is silent, easy to miss in review, and
invisible in the OpenAPI schema. Inverting the default makes exposing an
endpoint an explicit act that shows up in the diff. It also keeps the security
scheme accurate in the generated schema, which the frontend types and the
fuzzer both read.

## D13 — Schemathesis: exclude `ignored_auth`, document the 400 it found

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

The fuzz job runs with `--exclude-checks negative_data_rejection,ignored_auth`.
Separately, the fuzzer found an undocumented 400 on `POST /api/v1/auth/login`
(unparseable request body); the contract now declares that response and
`tests/unit/test_auth.py` pins it.

*Rationale:* the session cookie has to be passed as a raw `--header` because
schemathesis has no CLI flag for an `apiKey` scheme, so the `ignored_auth` check
cannot strip it before probing and reads every protected operation as
"unauthenticated but accepted" — false positives on a property that
`tests/unit/test_auth.py` already tests directly. `negative_data_rejection` was
already excluded because FastAPI ignores unknown query parameters by design.
Excluding a check is only acceptable with the property covered elsewhere, which
is the case for both. The 400 is the counter-example that justifies keeping the
fuzzer at all: it was a real gap between the implementation and the published
contract, and it is now fixed at the contract, not silenced.

## D14 — Fullstack E2E logs in once through the UI and replays `storageState`

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

In `E2E_FULLSTACK=1` mode Playwright runs a `setup` project that fills in the
real login form, saves the browser context to `e2e/.auth/state.json`, and hands
it to the test project via `storageState`. The password comes from
`E2E_PASSWORD`. UI-only mode keeps its single-project layout.

*Rationale:* going through the form (rather than posting to `/auth/login` or
forging a cookie) makes the login page itself part of what the smoke suite
verifies — exactly the wiring that layer exists to check — while paying the
login cost once instead of per test. Forging a cookie would require the session
secret in the test process and would prove nothing about the frontend.

## D15 — `just init` degrades to a placeholder hash when there is no TTY

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

`scripts/bootstrap-env.sh` takes the password from `ARC_INIT_PASSWORD` when set,
prompts (hidden, twice) when stdin is a terminal, and otherwise writes `.env`
with every other secret generated but `AUTH__PASSWORD_HASH` left as the
placeholder, printing the two steps needed to finish. An existing `.env` is
never touched.

*Rationale:* the three sensible behaviours without a terminal are: fail, invent
a password, or finish everything that can be finished and say what is missing.
Failing makes the script unusable in provisioning; inventing a password creates
a credential nobody knows and that looks valid. The third leaves the stack one
explicit, documented step from running, and the placeholder is inert — the API
refuses to boot on it in production.

## D16 — `just` is installed through uv (`rust-just`) in the devcontainer

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

`.devcontainer/startup.sh` runs `uv tool install rust-just`.

*Rationale:* every documented workflow in this repo goes through the justfile,
and the task runner was not in the image — so the documentation described a tool
the container did not have. `rust-just` is the just project's own PyPI
distribution, so it needs no new package manager or feature, and uv puts the
binary on a directory already in `PATH`.
