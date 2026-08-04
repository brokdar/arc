# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### WP-0

Scaffolding is in progress. Later phases append to this section.

- Repo hygiene: tracked `docs/`, removed orphaned build artifacts
  (`packages/`, root `node_modules/`), ignored `/data/` and `.schemathesis/`,
  bumped the `ruff-pre-commit` hook to v0.16.1 to match the backend lockfile,
  and seeded this changelog plus `docs/decisions.md`.
- Upgraded the backend from Python 3.13 to 3.14 (per D4) across
  `pyproject.toml`, `.python-version`, `pyrefly.toml`, both `Dockerfile`
  stages, and the devcontainer image, and relocked `uv.lock`.
- Removed the ARQ worker and its Redis service (per D5) and replaced them with
  an in-process APScheduler started by the API lifespan
  (`backend/app/core/scheduler.py`); dropped the `redis` and `worker` services
  from Compose, the `dev-worker` recipe, and the `REDIS__URL` setting.
