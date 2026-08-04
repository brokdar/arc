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
