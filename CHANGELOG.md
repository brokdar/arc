# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### WP-0 — scaffold + infrastructure

The scaffold was built by adapting the full-stack template rather than
scaffolding the build plan's `apps/`+`packages/` workspace monorepo; the
reasoning for this and every other departure is in `docs/decisions.md`
(D1–D19).

**Backend architecture**

- Restructured the backend from `app/domains/<domain>/` into layered modules:
  `app/domain` (pure business rules, filled in by WP-1), `app/persistence`
  (db, ORM models, repositories, Alembic), `app/services`, `app/ingest` and
  `app/mcp` skeletons, and `app/api` (routes, schemas, pagination,
  validation), with `app/core` reduced to genuinely cross-cutting code.
  Boundaries — domain purity, `api`/`mcp` independence, and the layer stack
  `api|mcp → ingest → services → persistence → domain` — are enforced by
  import-linter contracts (`uv run lint-imports`) wired into CI, `just lint`
  and pre-push. The OpenAPI contract is unchanged.
- Upgraded the backend from Python 3.13 to 3.14 (D4) across `pyproject.toml`,
  `.python-version`, `pyrefly.toml`, both `Dockerfile` stages and the
  devcontainer image, and relocked `uv.lock`.
- Removed the ARQ worker and its Redis service (D5), replacing them with an
  in-process APScheduler started by the API lifespan
  (`backend/app/core/scheduler.py`); dropped the `redis` and `worker` compose
  services, the `dev-worker` recipe and the `REDIS__URL` setting.

**Authentication**

- Added single-user session-cookie authentication end to end (D6). The
  credential store is one setting, `AUTH__PASSWORD_HASH` (a bcrypt hash —
  there is no user table); `POST /api/v1/auth/login` swaps it for a signed
  session cookie issued by Starlette's `SessionMiddleware` (`arc_session`,
  `SameSite=Lax`, 14 days, `Secure` once `AUTH__SESSION__HTTPS_ONLY` is on),
  with `POST /api/v1/auth/logout` and an always-open
  `GET /api/v1/auth/session` alongside it. Everything else under `/api/v1` is
  mounted on a router carrying `Depends(require_session)` and a declared 401,
  so new routers are protected by default (D12); `/health` stays open. Failed
  logins sleep ~0.3s to blunt guessing, and production refuses to boot without
  `AUTH__PASSWORD_HASH` and `AUTH__SESSION__SECRET_KEY`. The unused
  `AUTH__JWT__*` shell is gone.
- On the frontend, the API client sends credentials, a `/login` page posts the
  password, and an `AuthGuard` client component bounces unauthenticated
  visitors off the protected pages.
- Schemathesis found an undocumented 400 on login (unparseable body); the
  contract now declares it and a unit test pins it. The fuzz job supplies a
  session cookie and excludes the `ignored_auth` check, which cannot strip a
  raw header (D13).

**MCP server**

- Added the MCP server skeleton (`backend/app/mcp/`): a FastMCP 3 server run
  from the backend image as the `mcp` compose service
  (`python -m app.mcp.main`, streamable HTTP on :8001, behind Caddy's
  `/mcp*`). Every request must present a bearer key from `MCP__API_KEYS`
  (`label:scope:key,...`, scope `read` or `write`); keys are parsed by the
  framework-free `app/mcp/auth.py` and compared with `secrets.compare_digest`
  in a `TokenVerifier` subclass (D10), which puts the caller's label and scope
  on the request identity for per-tool scope checks in WP-8. The server
  refuses to start (exit 1) with no keys, so `MCP__API_KEYS` is required for
  `docker compose up`. The surface is one `ping` tool plus an unauthenticated
  `/health` route for the container healthcheck.

**Infrastructure**

- Added a Caddy reverse proxy (`caddy/Caddyfile`, `caddy` service on :80/:443)
  fronting the whole stack from one origin: `/api/*` and `/health` to the API,
  `/mcp*` to the MCP server, everything else to the frontend.
  `CADDY_SITE_ADDRESS` defaults to `:80` (plain HTTP); set a hostname for
  automatic HTTPS. The frontend is built with an empty
  `NEXT_PUBLIC_API_BASE_URL`, so the browser calls the API same-origin through
  the proxy, and the `@fullstack` smoke suite runs against `http://localhost`.
  Caddy deliberately does not depend on `mcp`, so a missing MCP key set cannot
  take the site down (D9).
- Upgraded Postgres from 17 to 18 (dev stack and the integration-test
  database). Postgres 18 moved the image's `VOLUME` to `/var/lib/postgresql`
  (`PGDATA` is now `/var/lib/postgresql/18/docker`), so the `postgres-data`
  volume and the test tmpfs mount that path instead of `.../data`. **Existing
  local volumes hold a 17 cluster and must be recreated**
  (`docker compose down -v`).
- Added the runtime data tree: `DATA__ROOT` (default `data`) with `inbox/`,
  `originals/`, `streams/`, `quarantine/` created on API startup and
  bind-mounted into the api container at `/app/data`; a one-shot `data-init`
  service hands the root-owned bind mount to the api's non-root user first
  (D8).

**Developer workflow**

- Made `just init` real (`scripts/bootstrap-env.sh`): it copies `.env.example`
  to a mode-600 `.env`, generates `POSTGRES__PASSWORD`,
  `AUTH__SESSION__SECRET_KEY` and both `MCP__API_KEYS`, and prompts (hidden,
  twice) for the login password it bcrypt-hashes into `AUTH__PASSWORD_HASH`.
  Values are substituted with Python rather than `sed`, so the `$` and `/` in
  bcrypt hashes survive; the script is idempotent and degrades to a
  placeholder hash plus instructions when there is no terminal to prompt on
  (D15). `just hash-password` prints a ready-to-paste single-quoted hash for
  rotating the password later.
- `just check` now also runs `api-check`, so API-contract drift is part of the
  one local gate, and the devcontainer installs `just` itself
  (`uv tool install rust-just`, D16) — the whole workflow depended on a tool
  that was not in the image.
- The Playwright `@fullstack` suite logs in once in a `setup` project and
  replays the session via `storageState` (D14).
- Repo hygiene: tracked `docs/`, removed orphaned build artifacts
  (`packages/`, root `node_modules/`), ignored `/data/` and `.schemathesis/`,
  and bumped the `ruff-pre-commit` hook to v0.16.1 to match the backend
  lockfile.

- Added changelog tooling (D18). `just changelog` runs git-cliff (`cliff.toml`)
  over the conventional commits since the last tag and prints a **draft** —
  commit bodies included, grouped under Keep a Changelog headings — whose
  entries are edited down by hand into the `## [Unreleased]` section above;
  `just changelog-range main..HEAD` does the same for a branch. Nothing writes
  to `CHANGELOG.md`. Conventional Commit format is now enforced rather than
  documented, in two deliberately unequal layers: a `commit-msg` hook
  (`conventional-pre-commit`, plus the `commit-msg` prek shim) rejects branch
  subjects it cannot parse, and `.github/workflows/pr-title.yml` additionally
  requires the PR title to start lowercase and not end in a period. The title
  is the one that matters — the `protect-main` ruleset allows only squash
  merges, so it becomes the commit subject on `main`, where
  `filter_unconventional` would otherwise drop an unparseable subject from
  every draft with no error. git-cliff installs in the devcontainer via
  `uv tool install git-cliff`.
- Switched the repository's squash-merge settings to `PR_TITLE` + `PR_BODY`
  (D19), so a merged PR's description — not a bullet dump of its commits —
  becomes the commit body on `main` and the raw material for a changelog entry.
  The `protect-main` ruleset gained a `required_status_checks` rule naming the
  `pr-title` check, so a non-conventional title now blocks the merge instead of
  only annotating it.
- Replaced the `commit-commands` plugin with project skills (`.claude/skills/`:
  `commit`, `commit-push-pr`, `clean-gone`), disabling the plugin in the repo's
  `.claude/settings.json` so the swap travels with the checkout. The plugin's
  generic "create a commit with an appropriate message" knew nothing of this
  repo's conventional format, work-package scopes, hooks that rewrite files
  mid-commit, or the squash-only PR title rule; its `clean_gone` also grepped
  `git branch -v` for `[gone]`, which that command never prints (only `-vv`
  does), so it matched nothing.

**Documentation**

- Seeded `CHANGELOG.md` and the decision log `docs/decisions.md` (D1–D18).
- Aligned `docs/mvp-build-plan.md` (stack, repository layout, WP-0),
  `docs/tech-stack.md`, `README.md`, `AGENTS.md`, `backend/README.md` and
  `frontend/README.md` with what was actually built, and corrected the stale
  references in WP-1…WP-9 (`packages/*` paths, `make` targets) so later work
  packages execute against the real repository.
- Folded `AGENTS.md` and `frontend/AGENTS.md` into `CLAUDE.md` and
  `frontend/CLAUDE.md`, which previously only `@`-included them, and dropped the
  `AGENTS.md` files — this project is worked on with Claude Code only (D17).
- Re-verified the WP-0 scaffold against the running stack and corrected the
  drift it exposed. `scripts/setup-repo.sh` did not reproduce the repository
  configuration D19 describes — it left `squash_merge_commit_title`/`_message`
  at their defaults and created a `protect-main` ruleset with no
  `required_status_checks` rule, so a fresh clone of this template got neither
  `PR_TITLE`+`PR_BODY` squash commits nor a blocking `pr-title` check; both are
  now applied, with a note that an existing ruleset is skipped rather than
  updated. In the docs: the Python pin is the *minor* (`3.14`, patch floats —
  3.14.4 in the devcontainer, 3.14.6 in the runtime image), not the "3.14.6"
  the plan and tech stack claimed; the release publishes three images
  (`api`, `mcp`, `frontend`), not two; WP-0's CI summary described the
  integration job's throwaway Postgres as a service container and the
  full-stack smoke job as "Docker Compose validation"; and WP-0 gained the
  repo-governance/dev-workflow item (devcontainer, prek hooks, squash-only
  ruleset, changelog tooling) that D16–D19 recorded but the plan never listed.
  Documented the first-run trap that `just init` + a pre-existing
  `postgres-data` volume produces: a new random `POSTGRES__PASSWORD` that
  Postgres ignores, surfacing as an api crash-loop on `InvalidPasswordError`.
