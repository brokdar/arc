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

## D17 — Agent instructions live in `CLAUDE.md`; `AGENTS.md` dropped

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

The scaffold shipped the conventions in `AGENTS.md` with `CLAUDE.md` as a
one-line `@AGENTS.md` include, at both the repo root and in `frontend/`. The
content now lives directly in `CLAUDE.md` and the `AGENTS.md` files are gone;
`README.md` and `frontend/README.md` point at `CLAUDE.md` instead. Vendored
third-party skill packages under `.agents/skills/` keep their own `AGENTS.md`
(compiled output of those packages, not project scaffolding).

*Rationale:* this project is developed with Claude Code only, so the
vendor-neutral filename bought nothing and the include indirection meant every
edit touched a file whose name no longer described who reads it. Reinstating
`AGENTS.md` later is a `git mv` plus a one-line include if another agent tool is
ever added.

## D18 — Changelog stays hand-curated; git-cliff drafts, two lint layers protect it

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

`CHANGELOG.md` remains hand-written Keep a Changelog. `just changelog`
(git-cliff, `cliff.toml`) prints a *draft* from conventional commits — commit
bodies included, mapped onto Keep a Changelog headings — which is edited down
and pasted under `## [Unreleased]`. Nothing writes to the file. Conventional
Commit format is now enforced in two places: a `commit-msg` hook
(`conventional-pre-commit`) on branch commits, and
`.github/workflows/pr-title.yml` on the PR title.

*Displaces:* release-please and semantic-release, both of which were considered
and rejected; also the implicit status quo of writing every entry from scratch.

*Rationale:* release-please would own `CHANGELOG.md` in its own
`### Features` format and automate version bumping and tagging — real value,
but this repo has no package consumers, `release.yml` already publishes on a
manual `v*` tag, and its tag would be pushed with `GITHUB_TOKEN`, which does
not trigger `release.yml` (a PAT or a `workflow_call` refactor would be
needed). The cost outweighed the benefit pre-1.0. Fragment-based tools
(towncrier, changesets) solve merge conflicts between many contributors, which
a single-developer repo does not have.

The two lint layers are not redundant. `cliff.toml` sets
`filter_unconventional = true`, so an unparseable subject is dropped from the
draft with no error — the failure mode is a change silently missing from a
release. The `commit-msg` hook cannot cover the case that matters most: the
`protect-main` ruleset allows **squash merges only**, so what lands on `main`
is one commit per PR whose subject comes from the PR title, which is never seen
by a local hook. Hence the CI check on the title.

*Consequence:* changelog granularity is one entry per PR, not per commit. The
PR title and description are therefore the load-bearing artifacts; branch
commits remain the review unit and the source of that prose. The type list is
duplicated across `.pre-commit-config.yaml`, `cliff.toml` and `pr-title.yml`
and must be changed in all three.

## D19 — Squash-merge settings changed to `PR_TITLE` + `PR_BODY`

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

`brokdar/arc` now sets `squash_merge_commit_title = PR_TITLE` and
`squash_merge_commit_message = PR_BODY`, displacing `COMMIT_OR_PR_TITLE` +
`COMMIT_MESSAGES`.

*Rationale:* under `COMMIT_MESSAGES` the squashed commit body was a concatenated
bullet dump of every branch commit, which makes a poor changelog entry.
`PR_BODY` puts the curated PR description — the thing a human already wrote for
a reader — on `main`, where `just changelog` quotes it. GitHub rejects
`PR_BODY` unless the title is `PR_TITLE` (only four combinations are valid), and
that pairing is better anyway: under `COMMIT_OR_PR_TITLE` a single-commit PR
took its subject from the commit and bypassed `pr-title.yml` entirely, so the
lint governed only some merges. It now governs all of them.

The `protect-main` ruleset now also carries a `required_status_checks` rule
naming the `pr-title` check, so a non-conventional title blocks the merge rather
than merely annotating it. `strict_required_status_checks_policy` is off — with
one developer and squash-only merges, forcing every branch up to date before
merge buys nothing. The required context is the **job name** in
`.github/workflows/pr-title.yml` (`name: pr-title`); renaming that job silently
breaks the requirement, so the two must change together.

## D20 — Repo improvement is trigger-based, not a session-end ritual

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

`CLAUDE.md` gains an "Improving this repo" section telling agents to convert
recurring friction into a durable artifact — skill, `.claude/rules/` file,
hook, `justfile` recipe, CLAUDE.md line, or decision entry — displacing the
implicit status quo where such improvements happened only when a human thought
of them.

*Rationale:* the tooling this repo already leans on (import-linter contracts,
the `block_npm.py` hook, the `commit` skills) exists because someone noticed a
repeated mistake and made it mechanical. Naming that loop makes it every
agent's job. The guard against noise is the trigger: **the second occurrence**,
not session end. An unconditional "suggest improvements before you finish"
instruction produces a suggestion every time regardless of whether anything
recurred, which trains the reader to skip the section; a second-occurrence
trigger produces one only when there is evidence. Hence also: one proposal at
a time, build only on agreement, and no proposal whose effect is to widen
permissions or weaken a guard.

The section documents only what an agent cannot derive — the trigger, the
anti-noise rules, this repo's destinations, and the `paths:` frontmatter key
that scopes a `.claude/rules/**/*.md` file to matching files instead of every
session. Skill and hook mechanics are deliberately absent: general knowledge
in a project file is context an agent already has, paid for on every load.

## D21 — Language servers are the build's own checkers, run through uv and bun

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

Both language servers are the tool the build type-checks with, launched through
the project's package manager: `uv run --project backend pyrefly lsp` and `bun
run tsgo --lsp -stdio`, in the local plugins `pyrefly-lsp` and `tsgo-lsp`. The
official `typescript-lsp` and `pyright-lsp` plugins are uninstalled. This
displaces `typescript-lsp`, which was the enabled TypeScript server.

*Rationale:* `typescript-lsp` runs `typescript-language-server`, driving
**tsserver** from the TypeScript 5.9 workspace package, while `bun run
type-check`, the pre-push hook and CI all run **tsgo** (D2). Two compilers means
editor diagnostics that disagree with the build. It also could not work as
configured: the server starts at the repo root, which has no `node_modules`, and
exits with "Could not find a valid TypeScript installation" — TypeScript lives
in `frontend/node_modules`. Invoking through `uv run` and `bun run` rather than a
binary path means the server resolves from `uv.lock` and `bun.lock`, so the
language server cannot drift from the version CI installs.

Two mechanics were load-bearing and cost a debugging session to find:

- **`lspServers` belongs in the plugin's `plugin.json`, not the marketplace
  entry.** Under `strict: true` (the default) `plugin.json` is the authority for
  component definitions. `pyrefly-lsp` declared its server only in
  `marketplace.json`, which `claude plugin details` happily listed while
  `claude --debug` showed `Total LSP servers loaded: 1` — the Python server had
  never started, and `.py` files had no language server at all. The official
  plugins get away with the marketplace entry because they set `strict: false`.
- **The plugin cache is keyed by version.** Editing a local plugin in place
  changes nothing until the version is bumped and `claude plugin update` runs;
  otherwise the stale cached copy keeps loading.
- **`${CLAUDE_PROJECT_DIR}` is the launch directory, not the git root**, and
  `.claude/settings.json` is only applied when Claude Code starts at the repo
  root. Started from `frontend/`, the official `typescript-lsp` — enabled at
  user scope, where this repo's settings do not reach — claimed `.ts` before
  tsgo (first server registered for an extension wins) and answered hovers as
  `any` from a tsserver with no `tsconfig.json` in scope. Uninstalling it
  removes that contest; the launch-directory sensitivity remains, so both
  servers start through `bash -c` wrappers that `cd` first, making a
  subdirectory session fail at spawn instead of serving the wrong project.

Accepted limitation: tsgo implements LSP 3.17 *pull* diagnostics, and Claude
Code consumes *push* only, so TypeScript errors do not appear after an edit the
way pyrefly's do. Navigation is unaffected, and type errors are still caught by
`bun run type-check` in the pre-push hook and CI. Keeping a second compiler
in the editor to close that gap would cost exactly what this decision buys.

## D22 — Login runs bcrypt off the event loop and serializes attempts

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

`POST /api/v1/auth/login` now verifies the password in a worker thread
(`starlette.concurrency.run_in_threadpool`) and holds a module-level
`asyncio.Lock` across both the verification and the 0.3s failed-login delay,
displacing the original inline call plus a bare `await asyncio.sleep`.

*Rationale:* bcrypt at cost 12 is ~0.15s of CPU per attempt. Called inline it
blocks the single event loop, so a handful of unauthenticated POSTs freezes the
whole process — measured with five concurrent failed logins in-process, worst
event-loop lag was **0.807s**, during which nothing else is served, `/health`
(watched by the container healthcheck) included. With the threadpool call the
same storm leaves worst lag at **0.0012s** and `/health` round-trips at
**≤6.8ms**.

The lock is what makes the delay a throttle at all: unserialized, N guesses
each wait out the same 0.3s in parallel, so an attacker pays 0.3s in total
(measured 5 failures in 1.11s, 2.4x a single attempt). Serialized, the cost
accumulates: 2.31s, 4.98x a single attempt. A process-wide lock is acceptable
precisely because there is one user — no legitimate login ever queues behind
another — which is also why a rate-limit middleware or library was not added.

## D23 — Compose pins ENVIRONMENT=production and publishes app ports on loopback

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

The `api` and `mcp` services now set `ENVIRONMENT: production` in
`environment:` (which overrides `env_file`), the `api` and `frontend` ports
publish on `127.0.0.1` like `db` and `mcp` already did, and `backend/` and
`frontend/.dockerignore` exclude `.env`/`.env.*` (plus `data/` and `logs/` on
the backend, whose Dockerfile does `COPY . /app`).

*Rationale:* `environment` defaults to `development` and nothing in the shipped
stack set it, so the boot guard in `app/core/config.py` — the one that refuses
an empty `AUTH__PASSWORD_HASH` or `AUTH__SESSION__SECRET_KEY` and the default
`POSTGRES__PASSWORD` — never ran anywhere it mattered. A hand-written `.env`
missing the session secret signed cookies with `""` and the stack came up
looking healthy. Pinning it in compose rather than documenting it in
`.env.example` means the guard cannot be switched off by an incomplete `.env`;
the commented `ENVIRONMENT=development` line now says so, and only affects
host-run processes (`just dev-api`, tests). Publishing on `0.0.0.0` gave every
host on the LAN a direct line to the API and the Next.js server, bypassing
Caddy — the reverse proxy is the only intended ingress, so only it keeps
`80`/`443`. The `.dockerignore` entries close the matching leak in the other
direction: a developer's `backend/.env` was being baked into the image.

## D24 — MCP keys must be >= 32 chars, placeholder-free and distinct

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

`parse_api_keys` now rejects any key shorter than 32 characters, any key still
containing `change-me`, and any key that repeats an earlier entry's key —
joining the existing duplicate-label rule. All are hard `ValueError`s, so
`app/mcp/main.py` turns them into exit 1, and none of the messages quote the
key.

*Rationale:* the parser previously accepted anything non-empty, so
`.env.example`'s `coach:write:change-me-random-hex` was a working credential
for a copied example file, and a hand-typed stand-in was a brute-forceable
bearer token on a service whose whole auth story is that string. Duplicate key
material was worse than useless: `verify_key`'s loop deliberately runs to
completion and keeps the *last* match, so `readonly:read:K,coach:write:K`
resolved to whichever entry came last in the string — an identity and a scope
decided by ordering. Rejecting it at parse time keeps the constant-time loop
unchanged (an early return there would leak which key matched). 32 characters
is `openssl rand -hex 16`; the documented recipe, `openssl rand -hex 32`, gives
64.

## D25 — Settings anchor `.env` at the repo root; tests never read it

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

`Settings.model_config` now names two env files — `<repo root>/.env` (computed
from the package location, `REPO_ROOT` in `app/core/config.py`) then a `.env`
in the working directory — instead of the bare relative `.env`. The `dev-api`,
`dev-mcp`, `db-upgrade` and `db-revision` recipes prefix `POSTGRES__HOST=localhost`,
and `backend/tests/conftest.py` disables the dotenv source for the whole test
suite.

*Rationale:* the documented host dev loop could not read the only `.env` the
repo has. Every one of those recipes does `cd backend` first, so the relative
path resolved to `backend/.env`, which `just init` does not write: `just dev-api`
connected as `postgres`/`postgres` against a cluster initialized with the random
password (`InvalidPasswordError`), login was impossible with an empty
`AUTH__PASSWORD_HASH`, and `just dev-mcp` exited 1 for missing keys. Anchoring in
code rather than exporting the file from `just` (`set dotenv-load := true`) fixes
`uv run fastapi dev` and `uv run alembic` run by hand as well, and keeps the
`.env` out of unrelated recipes — dotenv-load would have injected a developer's
secrets into `just test` too. Keeping the CWD entry second preserves the escape
hatch of a local override, and the per-recipe `POSTGRES__HOST` covers the one
value that is genuinely container-specific (`db` is a compose network name). The
price of anchoring is that tests would inherit the developer's `.env`, so the
suite disables it in one place at collection time: a run's outcome must not
depend on whose machine it is on. In the image the anchor resolves to `/.env`,
which does not exist (WORKDIR is `/app` and `.dockerignore` excludes `.env`), so
containers still take configuration only from compose.

## D26 — The domain-purity deny-list is checked against the dependency list

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

`fastmcp`, `bcrypt` and `itsdangerous` join the forbidden modules of the
"Domain is pure" import-linter contract, and
`tests/unit/test_domain_purity_contract.py` now derives the expected list from
`[project].dependencies`: each distribution is mapped to the import names it
provides (`importlib.metadata.packages_distributions()`, inverted) and must be
either forbidden in `app.domain` or on a small in-test allowlist that records
why. Today the allowlist is `pydantic` alone.

*Rationale:* a `forbidden` contract only bites for what it names, so the
hand-written list silently rotted as dependencies were added — `import fastmcp`
or `import bcrypt` inside `app/domain` passed `lint-imports`, CI and pre-push
while the docs claimed the boundary was mechanical. A `layers` contract cannot
express "no third-party I/O", and inverting to an allow-list of permitted
imports is not something import-linter offers, so the deny-list stays and a test
guards its completeness: adding a dependency without classifying it now fails
the suite, with the failure message naming both places the decision can go. The
allowlist is also checked for staleness and for contradicting the contract, and
carries the note that WP-5's polars/pyarrow (metrics moving into the domain) are
the next expected entries.

## D27 — `pr-title` is the only required status check; CI is advisory

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

`scripts/setup-repo.sh`'s `protect-main` ruleset requires exactly one status
check context, `pr-title`, with `strict_required_status_checks_policy: false`
and repository auto-merge enabled. The lint, test, schema-sync and compose
jobs run on every PR but gate nothing: a PR whose CI is red is technically
mergeable, and auto-merge fires as soon as `pr-title` reports. This displaces
the obvious alternative of requiring the CI contexts too, which in turn would
have required an always-reporting gate job in every workflow.

*Rationale:* the CI workflows are path-filtered (a backend job does not run on
a docs-only PR), and GitHub treats a required context that never reports as
pending forever — so requiring them deadlocks exactly the small PRs this repo
makes most. The standard escape is a gate job per workflow that always runs and
succeeds-or-waits on the filtered jobs' results, i.e. a second layer of YAML to
maintain in every workflow, protecting a single-operator repo from a merge only
that operator can perform. `pr-title` is required because it is the one check
that is *not* advisory: its subject becomes the squash commit on `main` and the
changelog entry, and it is unfixable after the merge. Everything else is caught
before the push by pre-commit and pre-push hooks, and is visible on the PR.

**Revisit when:** a second contributor gains write access, or the first time a
red PR is merged by mistake. Either one turns "the operator reads the checks"
from a fact into an assumption, and the gate-job cost becomes worth paying.

## D28 — The service owns the commit; the session dependency only rolls back

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

`get_session` no longer commits. Each mutating service method ends with
`app.persistence.db.commit(session)`, and both the FastAPI dependency and the
new `session_scope()` context manager (for MCP tools, scheduler jobs and
ingest) only roll back on error and close. `commit` and the repositories'
`flush` translate `IntegrityError` into `ConflictError`, so a constraint
violation is a 409 with the documented `ErrorDetail` body. `set_session_factory`
is the matching test seam: it installs a factory (and drops the cached engine)
so non-HTTP code paths, which have no dependency to override, run against the
unit suite's in-memory SQLite.

*Rationale:* a commit in a yield-dependency's teardown runs *after* the
endpoint returns, outside the scope of the handlers `register_exception_handlers`
installs. Anything that fails only at COMMIT — a deferred constraint, a
serialization failure, a uniqueness race the service's pre-check missed — came
back as a bare plain-text 500 that appears in no OpenAPI response, no generated
frontend type and no MSW handler. Moving the commit inside the request boundary
is what makes the error envelope total; translating `IntegrityError` at the
persistence boundary is what makes the race and the pre-check agree on a status
code. The displaced alternative — keeping the commit in the dependency and
adding a Starlette middleware to catch it — would have re-implemented the
handler chain a second time, one layer further out, for the same result.

## D29 — Column and constraint conventions are fixed before the first migration

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

`app/persistence/types.py` holds the spellings every model uses:
`UtcDateTime` for timestamps (binds convert to UTC, naive values are rejected,
results always come back `tzinfo=UTC`), `JSONColumn` (`sa.JSON` with a
`JSONB` variant on Postgres), and `enum_column()` (`native_enum=False`).
Primary keys default to `uuid.uuid7` (Python 3.14 stdlib), not `uuid4`. `Base`
carries the standard SQLAlchemy `ix/uq/ck/fk/pk` naming convention, and the
initial migration was regenerated so its `pk_items` matches.

*Rationale:* the unit suite runs on SQLite and production runs on Postgres, so
any type that behaves differently on the two makes the cheap test layer test a
different application than the one that ships. `DateTime(timezone=True)` is
exactly that: verified to return a naive datetime on SQLite and an aware one on
Postgres, which turns every `now(UTC) - stamp` into code that works in one
place and raises in the other — and WP-1's versioning doctrine stamps `as_of`
on every derived artefact, so this would have been in every table. Native
Postgres enums need `ALTER TYPE` to gain a member and have no SQLite
equivalent; a `VARCHAR` + `CHECK` migrates like any other constraint. uuid7 is
time-ordered, so inserts land at the right-hand edge of the primary-key index
instead of scattering across it, and rows sort by creation with no second
column — free here, impossible to retrofit once ids exist. Unnamed constraints
get whatever the backend invents, which Alembic then cannot drop or alter by
name; naming them is only cheap before the first migration ships. In the same
spirit, `app.persistence.load_models()` sweeps the package instead of a
hand-written import list in `alembic/env.py` and the test conftest — a model
nobody remembered to import produced an *empty* autogenerated revision and
`no such table` in tests, both silent.

## D30 — Every mutating service takes an `Actor`

**Date:** 2026-08-04 · **Status:** accepted · **WP:** WP-0

`app/domain/actor.py` defines `Actor` — `athlete()`, `agent(label)`,
`system()`, stored as `athlete` / `agent:<key-label>` / `system` — and every
mutating service method takes `actor` as a keyword argument. `app/api/deps.py`
supplies `Actor.athlete()`; `app/mcp/identity.py` reads the label and scope
that `app/mcp/main.py` already puts on the request's `AccessToken` and offers
`current_actor()` and `require_scope()`. Nothing consumes the value yet.

*Rationale:* WP-1 adds `audit_log(actor, action, entity_type, entity_id,
payload_json, at)` and requires a row on *every* write path, and WP-8's
guardrails are stated in terms of `actor=agent:<key-label>`. Adding the
parameter later means touching every service signature and every call site at
once, in the work package that can least afford it; adding it now costs an
unused argument. The domain is the only home that works: `app.api` and
`app.mcp` may not import each other, and both plus `app.services` need the
type, so anything adapter-shaped would have violated the layering contract.
The string form is the storage format, which is why `agent` labels may not
contain `:` — `agent:my:key` would not survive `Actor.parse`.

## D31 — Domain values are frozen dataclasses, not pydantic models

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-1

Everything in `app/domain/` is a frozen, slotted `dataclass` plus `StrEnum`,
with invariants enforced in `__post_init__` and signalled as `ValueError`.
Pydantic is *permitted* there — `tests/unit/test_domain_purity_contract.py`
allowlists it as "pure data modelling, no I/O" (D26) — so this displaces the
available alternative of pydantic `BaseModel`s in the domain.

*Rationale:* the API layer already speaks pydantic, and the two jobs are
different. A schema's job is to accept and coerce whatever arrives on the
wire; a domain value's job is to be impossible to construct in an illegal
state. Using one type for both means the coercion rules (string-to-date,
int-to-float, extra-field handling) become domain semantics by accident, and
the domain inherits `model_config` decisions made for HTTP reasons. Frozen
dataclasses also give free structural sharing with `dataclasses.replace`,
hashability, and — the deciding factor — they cost nothing to construct, which
matters because WP-5 constructs them per data point.

The price is that domain violations arrive as `ValueError`, which is not an
`AppError` (the domain may not import `app.core`). `app.core.exceptions.
domain_rules()` is the one-line context manager services wrap construction in,
turning them into 422s with the domain's own message. That keeps the rule and
its wording in one place instead of restating every bound in a schema.

## D32 — Zone boundaries: Coggan 7 off FTP, a 5-zone Friel scheme off LTHR

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-1

`app/domain/zones.py` ships two schemes, as lower bounds in percent of the
anchor:

| Model | Zone lower bounds | Source |
|---|---|---|
| `coggan_7` (%FTP) | 0 / 55 / 75 / 90 / 105 / 120 / 150 | Allen & Coggan, *Training and Racing with a Power Meter* |
| `lthr_5` (%LTHR) | 0 / 81 / 90 / 94 / 100 | Friel's cycling HR zones, 5a/5b/5c merged into one Z5 |

Three conventions come with them. Bands are **half-open and contiguous**
(`lower <= x < upper`), so the zones partition `[0, ∞)` for any value; the
**top zone is open-ended** (`upper is None`); and a zone model is **paired with
one anchor type** (`ZONE_MODEL_ANCHOR`), so `zones_for` rejects %FTP
percentages applied to a heart rate instead of returning plausible nonsense.
`MAX_HR` deliberately has no scheme — it is stored for later use, not a zone
basis.

*Rationale:* published tables state inclusive integer bands with one-point gaps
(Friel runs 81–89 then 90–93), which leaves 89.4 %LTHR in no zone at all.
Closing the gaps upward onto the next lower bound is the only reading that
makes "time in zone" total to the recording's duration — a property WP-5's
metrics depend on and WP-7 scores against, so it is fixed now rather than
discovered later. Collapsing Friel's 5a/5b/5c is honest about the MVP: nothing
computed distinguishes them, and inventing three zones the scoring cannot use
would imply a precision the data does not carry. The pairing check exists
because the failure it prevents is silent: 90 %FTP of an LTHR value is a
number, just not anyone's threshold.

## D33 — One athlete, fixed primary key, bootstrapped on first access

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-1

The `athlete` table holds exactly one row whose primary key is the constant
`SINGLETON_ATHLETE_ID` (`00000000-...-0001`), and `AthleteService.get`
**creates it on first access** — read or write — writing an `athlete.created`
audit row. Every profile field is nullable. This displaces two alternatives:
seeding the row in the migration, and create-on-first-update with `GET`
returning 404 until then.

*Rationale:* a migration-seeded row is data, and data does not survive the
things that happen to data — the integration suite truncates every table
between tests, and a restore-from-dump or a `TRUNCATE` in support would leave
an application with no profile and no way to make one. Lazy bootstrap is
idempotent and self-healing. `GET` returning 404 until the first `PATCH` was
rejected because the frontend would have to treat "no profile yet" as a
distinct state everywhere, forever, to save one insert. The fixed primary key
is what makes "at most one athlete" a database fact rather than a convention:
two concurrent bootstraps collide on the key and the loser gets a 409 (proven
in `test_error_envelope.py`), where a generated id would have produced two
athletes and no error. It also means no layer needs a lookup to address the
row. A `GET` that writes is the one oddity; it is why the endpoint takes an
`ActorDep` like a mutation, and the write happens exactly once.

## D34 — `enum_column` stores the member value, not its name

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-1

`app/persistence/types.py:enum_column` now passes `values_callable`, so a
`StrEnum` column stores `max_hr`, not SQLAlchemy's default of the member *name*
`MAX_HR`. WP-1 is the first code to use the helper, so nothing is migrated.

*Rationale:* the API, the OpenAPI schema, the generated frontend types and
every JSON payload use the value. Storing the name means one vocabulary with
two spellings, and every hand-written query, `psql` session, backup grep and
future data migration has to know which side of the ORM it is standing on. The
divergence is invisible in tests that go through the ORM, which is most of
them — `tests/unit/test_persistence_types.py` therefore reads the column as raw
text, and the integration suite does the same on Postgres.

## D35 — Anchor legality is a domain rule, and "in force" is a computation

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-1

`AnchorVersion.__post_init__` enforces four rules: the unit must be the anchor
type's own (`ANCHOR_UNITS` — a mismatch is rejected, never converted), the
value must be within per-type plausibility bounds (`ANCHOR_BOUNDS`, e.g. FTP
30–700 W), `tested` provenance requires a non-empty `protocol`, and a
confidence interval must bracket the value. Which version is **in force** at a
moment is computed by `anchor_as_of`: effective on or before that day *and*
created on or before that instant, ties broken by `created_at`.

*Rationale:* the bounds are a typo guard, not medicine — an FTP entered as
25000 W poisons every zone, target, IF and TSS derived from it, and the cost of
catching it at the boundary is one comparison. Requiring a protocol for tested
values is WP-8's stated guardrail for the `append_anchor` MCP tool; putting it
in the domain now means the API and the future tool cannot disagree about it,
which is the entire reason the rule lives below both adapters. The two-clause
"in force" rule is what makes derived values reproducible under invariant 1: a
back-dated correction entered today must change what is current, and must
*not* change what a score computed last week was looking at. `ORDER BY
created_at DESC LIMIT 1` gets both of those wrong, and a future-dated version
(planned test result, seasonal reset) wrong as well.

## D36 — Append-only is enforced three times, and the 405s are real handlers

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-1

Invariant 3 is stated in three places: `AnchorRepository` has no update or
delete method, `AnchorService` has no use-case for one, and
`app/api/routes/anchors.py` answers PUT, PATCH and DELETE on an anchor version
with **405 and a sentence explaining what to do instead**. The three refusals
are three separate endpoint functions, not one function with three decorators.

*Rationale:* FastAPI answers an undefined method+path combination with **404**,
which reads as "wrong id" — the one message guaranteed to send a client
looking in the wrong place. A real handler is the only way to say "this
operation does not exist here, and here is the operation that does". The other
two enforcement points are what make the rule true rather than polite: a 405 is
a statement about one adapter, while a repository with no `delete` is a
statement about the whole application, including the MCP tools WP-8 adds. Three
handler functions because `generate_operation_id` (`app/main.py`) derives the
OpenAPI operation id from the endpoint's *name*, so one shared function would
have produced three colliding ids in the generated frontend client.

## D37 — `hypothesis` is a dev dependency, so the purity contract ignores it

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-1

`hypothesis` was added with `uv add --dev`, landing in `[dependency-groups].dev`
rather than `[project].dependencies`. `test_domain_purity_contract.py` reads
only the latter, so no entry in the import-linter deny-list or in
`DOMAIN_MAY_IMPORT` is needed — and none was added.

*Rationale:* recorded because the absence looks like an oversight against D26,
which says every new dependency must be classified. The rule is about what
ships: a test-only library cannot be imported by `app.domain` at runtime
because it is not installed at runtime, so classifying it would be a decision
about nothing. The distinction is worth stating once, since WP-5's polars
(a runtime dependency) is the next addition and does need an entry.

## D38 — Zones are addressed by what they derive from, in two endpoints

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-1

`GET /api/v1/zones?anchor_type=…` derives from the version in force;
`GET /api/v1/anchors/{id}/zones` derives from one pinned version. This
displaces the first implementation, a single `GET /zones` taking
`anchor_version_id` *or* `anchor_type` and answering 422 when given both or
neither.

*Rationale:* the single endpoint could express a request with no meaning, so
it had to reject one at runtime — and that rejection is invisible in the
OpenAPI contract, which advertises both parameters as independently optional.
Schemathesis found it immediately (a schema-compliant request refused with
422), which is the fuzzer doing exactly its job: the schema was lying. Two
endpoints make the contract true, make each selector required where it
belongs, and say something the query-parameter version could not — that zones
of a specific anchor version are a sub-resource of that version, which is why
a frozen prescription can keep pointing at them.

## D39 — What the fuzzer changed about WP-1's contract

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-1

Running Schemathesis against the new endpoints (a real Postgres, as CI does)
produced four fixes and one configuration file:

1. **422 has two shapes, so the contract says so.** A service raising
   `ValidationError` returns `{"detail": "<sentence>"}`; FastAPI's own request
   validation returns `{"detail": [<per-field errors>]}`. Both are 422s on the
   same operation, and declaring `ErrorDetail` (a string) made the second one
   a documented lie. `ValidationErrorDetail` (`str | list`) is now the declared
   422 model wherever domain rules can fire. The alternative — flattening
   FastAPI's list into a sentence in the handler — was rejected as a WP-0
   behaviour change made for a WP-1 reason.
2. **The append-only refusals take a `str` path parameter, not a `uuid.UUID`.**
   `PUT /api/v1/anchors/current` was answering 422 about UUID syntax: true,
   and beside the point, since the method is what does not exist there. The
   handlers never look at the id.
3. **The 405s carry `Allow: GET`,** which RFC 9110 requires and which turns
   "you cannot do that" into "you cannot do that, here is what you can".
   `AppError` grew an optional `headers` for it.
4. **Free-form JSON is validated as deeply as it is stored.** A lone surrogate
   or NUL byte in a `capabilities` *key*, three levels down, reached the driver
   as a 500 — the same class of bug `PostgresText` already covered for string
   columns, one level of nesting deeper. `PostgresJsonObject` walks the
   document. Unlike `PostgresText`, the restriction cannot be expressed in
   JSON Schema, so it is enforced without being documented.

The configuration is `backend/schemathesis.toml`: the `positive_data_acceptance`
check is narrowed **per operation** for the endpoints that refuse
schema-compliant input on purpose (the 405s; the domain-rule 422s on anchor
append, profile update and zones). Narrowing per operation rather than
excluding the check globally (as D13 does for two others) keeps it armed for
every endpoint added later, which is where the next unguarded 500 will be. All
five refusals are pinned by unit tests, which is the standing condition for
narrowing a check at all.

## D40 — Reserved anchor types cannot be appended

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-1

`cp` and `w_prime` exist in `AnchorType` so that WP-5's critical-power model
arrives as code, not a data migration (D32 spirit). But existence in the enum
had left them **appendable**: `POST /api/v1/anchors` with `anchor_type: "cp"`
succeeded, which the review of PR #2 flagged as unaddressed by D35. They are
now refused twice: the create schema's `anchor_type` is a `Literal` of the
MVP three, so the contract does not offer them, and
`AnchorService.append` rejects `RESERVED_ANCHOR_TYPES`
(`app/domain/anchors.py`) with a 422 naming the work package that will accept
them — the service check is what covers WP-8's MCP tools, which do not pass
through the schema. This displaces the alternative of accepting early CP
measurements as harmless storage.

*Rationale:* nothing can consume the value (zones reject CP, no model exists),
and the CP protocols that make one measurement comparable with the next are
exactly what WP-5 defines. Rows accepted before those rules exist would be
history the model must either trust unvetted or awkwardly disown. The domain
`AnchorVersion` itself still accepts CP — reserved-ness is MVP write policy,
not a timeless domain rule, so it lives in the write path.

## D41 — Bootstrap is race-tolerant, and never a side effect of a rejected write

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-1

Two refinements to D33's lazy bootstrap, both from the PR #2 review:

1. **The lost race self-heals.** Two concurrent first-ever accesses both saw
   no row and both inserted the fixed primary key; the loser surfaced a 409 —
   on a `GET`. `AthleteService.get` now catches the `ConflictError`, re-reads,
   and returns the winner's row; it re-raises only when the re-read finds
   nothing (a genuinely broken state that should be loud). D33's "the loser
   gets a 409" is superseded for reads; a conflicting concurrent `PATCH`
   still 409s, since that is a real concurrent-write signal.
2. **`update` validates before it bootstraps.** It previously called `get`,
   committing the bootstrap (plus audit row) before domain validation — so a
   422'd first-ever `PATCH` left a profile behind. It now merges the update
   into the not-yet-persisted defaults, validates, and only then creates and
   updates the row in **one** transaction with both audit rows
   (`athlete.created`, `athlete.updated`). A rejected `PATCH` on an empty
   database is a pure no-op, pinned by
   `test_a_rejected_update_does_not_bootstrap_the_profile`.

## D42 — The step tree flattens by expanding repeats and leaving ramps whole

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-2

`app.domain.workout.flatten` turns a `EnduranceWorkout` into a list of
`FlatStep`s. Two representation choices, both stated as properties in
`tests/unit/test_domain_workout.py` rather than left implicit:

1. **Repeat blocks expand.** The fifth rep of a 5x block becomes its own flat
   step. Each flat step carries `path` (its position in the *tree*) and
   `repetition` (the 1-based iteration of each enclosing block, outermost
   first).
2. **Ramps stay ramps.** A flat step carries `start_targets` and `end_targets`,
   equal for a steady step and different for a ramp. Ramps are *not* sliced
   into steady sub-steps.

The round trip this makes testable is `flatten(w) == flatten(expand(w))` step
for step, with `expand` idempotent — which is what the hypothesis property
test asserts over random trees.

*Rationale:* expansion is what the athlete actually does and what a recording
can be aligned against (WP-5), so a scorer that had to re-walk the tree would
be re-deriving the same list per axis. `repetition` exists because WP-7's
`pacing` axis is defined as "last rep versus first rep" and would otherwise
need the tree as well as the flat list. Slicing ramps was rejected because it
invents a step count nobody rode and a precision the prescription does not
have: a 20-minute ramp cut into 20 one-minute blocks looks like twenty
prescribed intervals in every UI and every alignment. Carrying both ends
instead lets a consumer that only understands steady blocks take the midpoint,
and one that understands ramps do better.

## D43 — Percentages are fractions everywhere, and a channel may only take its own anchor

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-2

Every percentage in the domain is stored as a **fraction**: `0.88` is 88 %.
That covers workout targets (`PercentOfAnchor.pct_low`), ceiling limits
(`PercentLimit.pct`), band bounds, `sets_completed(min_fraction)`,
`load_within(pct_tolerance)` and strength `percent_e1rm` loads. It matches
`app.domain.zones`, which was already written that way (D32).

Alongside it, `CHANNEL_ANCHORS` fixes which anchor each channel may be a
percentage of — power off FTP, heart rate off LTHR or MAX_HR, cadence off
nothing — and `CHANNEL_UNITS` fixes the unit of an absolute target. Both are
checked at construction.

*Rationale:* one convention, or every consumer has to know which of two
scales a given number is on, and the mistake is invisible: 88 and 0.88 are
both plausible-looking. The channel/anchor pairing is D32's rule one level
out — "80 % of FTP rpm" is not a quantity, and 90 %FTP applied to a heart
rate is a number, just not anyone's threshold. Cadence deliberately has no
anchor rather than a permissive one, so the API contract cannot offer a
target nothing could resolve.

## D44 — A band is a tolerance around the step's own target, not an absolute range

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-2

`app.domain.criteria.Band` states `low`/`high` as **fractions of the target
the step itself prescribes** for a channel: `low=0.95, high=1.05` means
"within ±5 % of what was asked for". It is not an absolute watt range and not
a percentage of an anchor.

*Rationale:* `time_in_band` has to be expressible in a **purpose template**,
which is written once for the athlete and knows nothing about a particular
session — an absolute band could not be. A relative band also stays
meaningful across a workout whose steps sit at different intensities, so one
criterion covers the whole session instead of one per step. The alternative,
a band expressed as a percentage of an anchor, was rejected because it
duplicates what the step's own target already says and would silently
disagree with it after an edit.

## D45 — Purpose templates: a JSON file, validated at boot, complete by construction

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-2

Templates live in `backend/app/resources/purpose_templates.json`, are parsed
by the pure `app.domain.templates.parse_templates`, and are read exactly once
by `app.services.templates.load_purpose_templates` — which `app.main`'s
lifespan calls through `verify_bundled_resources()`, so a bad file stops the
boot. Failure is a `ResourceError` (a `RuntimeError`), deliberately **not** an
`AppError`: no status code helps a client, and the only correct response is to
fail to start.

The parser refuses a file that omits any purpose, names an unknown purpose or
axis, gives a purpose an axis from the other discipline, omits `completion`,
or carries a criterion that discipline could never evaluate.

The MVP templates list **no deferred axis**: `response` and `fuelling` are in
`ScoringAxis` so WP-7's shape exists, but a template naming one would promise
a score the MVP cannot produce. WP-7 reports them as `not_assessed(deferred)`
independently of the templates.

*Rationale:* the build plan says "stored as data, not code", and the value of
that is only realised if the data is checked as hard as code would be —
otherwise the failure moves from a compile error to a session that cannot be
scored, discovered weeks later. Requiring completeness rather than defaulting
a missing purpose is the same argument: a silent default is a purpose whose
scoring nobody chose. Keeping the parser in `app.domain` and the file reading
in `app.services` is what the purity contract requires, and it means the
template format is testable without touching a filesystem.

## D46 — The exercise catalogue is a hand-curated JSON file, keyed by slug, seeded lazily

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-2

Three decisions the build plan's `DECIDE:` and its "startup or migration"
choice come down to:

1. **Source:** the plan's default — a hand-curated JSON file in the repo
   (`backend/app/resources/exercise_catalogue.json`, 98 movements across
   squat / hinge / lunge / press / pull / core / carry / mobility /
   conditioning). A wger import is a later phase.
2. **Key:** the `exercises` table's primary key is the **slug**
   (`back_squat`), not a `uuid.uuid7` — the one departure from the D29
   convention. Prescriptions reference an exercise from inside a workout's
   JSON structure, where a foreign key cannot reach, so the identifier has to
   be stable and readable on its own and identical in every deployment.
3. **Seeding:** **lazily and idempotently on first access of the catalogue**
   (`ExerciseService.ensure_seeded`), matched by slug — missing rows inserted,
   changed rows updated, nothing ever deleted. One audit row
   (`exercise_catalogue.seeded`, `actor=system`) and only when something
   changed.

*Displaces:* both options the plan offered. A **migration** cannot own it: the
integration suite truncates every table between tests and a restore from dump
or a support `TRUNCATE` would leave an application whose strength
prescriptions reference nothing — exactly D33's argument for not seeding the
athlete row. The **lifespan** cannot own it either without making a successful
boot depend on a writable, migrated database, which today it does not: the API
comes up and serves `/health` with the database down. What the lifespan does
own is *validating* the bundled file, so a malformed catalogue is a failed
deploy rather than a failed request.

Nothing is ever deleted by a reseed because a slug that leaves the file may
still be referenced by a stored prescription, and losing the row would make
that prescription unreadable. A lost seeding race is caught and treated as
success (the winner wrote the same rows), following D41.

## D47 — A planned session's intent is versioned; its date and status are not

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-2

`planned_sessions` holds identity (date, discipline, status);
`planned_session_intents` holds an append-only chain of everything the session
is *for* — purpose, intent text, coach notes, success criteria, pinned anchor
versions, and a **snapshot of the prescription itself**. The intent row
carries WP-1's vocabulary verbatim (`version`, `as_of`, `superseded_by`,
`recompute_reason`) plus `edited_post_hoc`, and satisfies
`app.domain.versioning.VersionRecord` structurally, so the domain's
`current_version` / `next_version` work on ORM rows unchanged.

The freeze rule (invariant 4) is implemented as three cases in
`PlannedSessionService`:

| Edit | New version | `edited_post_hoc` | Anchors |
|---|---|---|---|
| create | 1 | false | pinned to what is in force |
| intent edit, no match yet | n+1 | false | **re-pinned** |
| intent edit, match exists | n+1 | **true** | **kept**, rescore triggered |
| date or status only | none | — | unchanged |

*Rationale:* the plan says "frozen at creation or last pre-execution edit", so
a session re-planned after a new FTP test must use the new FTP — but a session
the athlete has already ridden must keep the numbers it was ridden against, or
the score is measured against a prescription that never existed. Putting the
workout **snapshot** in the intent version (rather than only a `workout_id`)
is what makes that true when the prescription came from the library: editing
the library entry afterwards would otherwise silently rewrite what was
prescribed on a past date. `workout_id` survives as provenance and is nulled
by `ON DELETE SET NULL` if the library entry goes.

Date and status are excluded from intent because they are facts about the
calendar, not about what the session is for; versioning them would make every
drag on WP-3's week view a new prescription.

## D48 — Matching and rescoring are explicit seams, not silence

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-2

The freeze rule needs two things that do not exist yet: "has this session been
matched?" (WP-6) and "rescore it" (WP-7). Both are module-level hooks in
`app.services.planned_sessions` with an MVP default and a setter —
`set_match_probe` / `set_rescore_trigger` — in the same idiom as
`app.persistence.db.set_session_factory`. The defaults are honest: nothing is
matched, because no activity can be linked to a session yet, and there is
nothing to rescore.

*Rationale:* the alternative is to build the `edited_post_hoc` machinery in
WP-6 or WP-7, in the work package that can least afford it, and to ship WP-2
with a freeze rule that is documented but never executed. With the seams, the
whole rule is written and *tested* now — `tests/unit/test_planned_sessions_api.py`
installs a probe that says "matched" and asserts the flag, the kept pins and
the rescore call — and the later work packages supply one function each. The
hooks are functions rather than a registry because there is exactly one of
each, and taking the session as a parameter means WP-6's implementation runs
inside the same transaction as the edit that triggered it.

## D49 — A prescription that refers to an anchor with no version in force is refused

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-2

`POST /api/v1/planned-sessions` answers **422** when the prescription — the
workout's targets *or* the success criteria — is expressed as a percentage of
an anchor that has no version in force, naming the anchor and what to do about
it. Anchors referenced by criteria count: the `endurance` template's ceiling
of 100 % FTP makes an FTP necessary even for an absolutely prescribed
endurance ride.

*Rationale:* invariant 4 says a planned session pins the anchor version its
targets derive from. There is no honest way to pin nothing: storing an empty
map would make the prescription unresolvable and the eventual score
irreproducible, and resolving it later against whatever anchor exists then is
exactly the retroactive reinterpretation the invariant forbids. Refusing is
loud, actionable (the message says "append one first, or prescribe absolute
targets"), and one-time — the athlete enters anchors once. The shape checks
(purpose/discipline agreement, criterion applicability) deliberately run
*before* pinning, so the anchor message cannot mask a more fundamental error.

## D50 — Workout tags get a table, folders get a column, labels get their own path

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-2

The workout library stores the prescription as one JSON document
(`structure`), tags in a `workout_tags` association table, and the folder as a
plain nullable string column. The folder and tag lists are served from
`GET /api/v1/workout-labels`, a sibling of the collection, **not**
`/workouts/folders` and `/workouts/tags`.

*Rationale:* a step tree is recursive and only ever read whole, so shredding
it into rows would be a join per nesting level for no query anyone makes;
JSONB keeps it queryable where it matters (the integration suite asserts
`structure->'steps'->1->>'times'`). Tags are the opposite: "which workouts are
tagged X" *is* a query, and array containment is spelled differently on SQLite
and Postgres — the divergence `app.persistence.types` exists to prevent.
Folders stay a column because an MVP folder is a label, not a hierarchy with
its own lifecycle.

The path move was found by Schemathesis and is the same class of defect as
D39's second item. Any single extra segment under `/workouts` also matches
`/workouts/{workout_id}`, so `PATCH /api/v1/workouts/tags` fell through to the
id route and answered 422 about uuid syntax where 405 is the true answer.
Moving the facet out of the id namespace removes the collision, rather than
papering over it with four more refusal handlers per path; the 405s are now
Starlette's own, and `tests/unit/test_workouts_api.py` pins them.

## D51 — The unit suite turns SQLite's foreign keys on

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-2

`tests/unit/conftest.py` attaches a `connect` listener to the in-memory SQLite
engine issuing `PRAGMA foreign_keys=ON`.

*Rationale:* SQLite ignores foreign keys unless asked, per connection. Without
the pragma, `ON DELETE CASCADE` and `ON DELETE SET NULL` are inert in the unit
suite and enforced in production — so the cheap test layer tests a different
application than the one that ships, which is exactly what D29 fixed for
column types, one level down in the schema. It was not hypothetical: the test
asserting that deleting a library workout nulls the intent's provenance link
(D47) passed against Postgres and failed silently on SQLite until the pragma
went in.

## D52 — Domain JSON is decoded by hand, with located error messages

**Date:** 2026-08-05 · **Status:** accepted · **WP:** WP-2

`app.domain.coding` holds a handful of decoding helpers (`as_int`, `as_enum`,
`no_extra_fields`, …) that every `*_from_json` in the domain uses. Unknown
fields are refused, not ignored, and every message names its position in the
document — `groups[0].items[0].sets: expected an integer, got str`. Services
wrap decoding in `domain_rules()`, so that text reaches the client verbatim as
a 422.

*Rationale:* the domain may not import pydantic-settings or a framework, and
D31 keeps it on frozen dataclasses, so there is no schema library to lean on
below the API layer — but the same documents arrive from three places (an HTTP
body, a stored row being read back, WP-8's MCP tools) and only one of them has
pydantic in front of it. Hand-written decoders otherwise rot into inconsistent
messages and bare `KeyError`s; the helpers make the failure mode uniform for
one afternoon's work. Refusing unknown fields matters more here than usual: a
silently ignored key in a prescription is a lost edit to a training plan, and
the client cannot tell a typo from an unsupported feature.
