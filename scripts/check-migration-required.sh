#!/usr/bin/env bash
# Fail a push that changes a model's schema without shipping an Alembic
# migration in the same range.
#
# WHY THIS IS A HOOK AND NOT A TEST: model/migration drift is caught by
# `alembic check`, which runs in exactly one place — the Postgres integration
# suite (`just test-int`, and `backend-integration.yml` in CI). That is a
# service container plus the full suite, and discovering the drift there costs
# the run that found it AND the run that re-checks the fix. This check needs no
# database and no Docker, so it moves the dominant case — "changed the model,
# forgot the migration" — to the last free moment before CI is billed.
#
# It is deliberately a HEURISTIC, not a proof. `alembic check` remains the
# authority; this only refuses the obvious omission. The escape hatch is named
# in the failure message, because a heuristic that cannot be overridden is a
# heuristic that gets uninstalled.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PERSISTENCE="backend/app/persistence"
VERSIONS="backend/app/persistence/alembic/versions"

# No usable range (no remote, shallow clone, detached oddity) — never block on
# a range we could not determine. `alembic check` in CI is still the authority.
# Invoked through `bash`, not as an executable: this repo has `core.fileMode =
# false`, so a lost +x bit is invisible in `git status` and this script exited 126
# — "Permission denied" — for every push that touched a model. A guard that
# cannot run is worse than no guard, because the push goes green.
range="$(bash "$here/git-push-range.sh")"
[ -n "$range" ] || exit 0
read -r from_ref to_ref <<<"$range"

mapfile -t changed < <(git diff --name-only "$from_ref" "$to_ref" -- "$PERSISTENCE" 2>/dev/null || true)
[ "${#changed[@]}" -gt 0 ] || exit 0

# A migration added OR amended in this range satisfies the requirement. Amended
# counts because the common follow-up is "fix the migration I just wrote", and
# failing that push would be noise.
mapfile -t migrations < <(
  git diff --name-only --diff-filter=AM "$from_ref" "$to_ref" -- "$VERSIONS" 2>/dev/null || true
)
[ "${#migrations[@]}" -eq 0 ] || exit 0

# Only non-migration persistence files can carry a model change.
mapfile -t model_files < <(printf '%s\n' "${changed[@]}" | grep -v "/alembic/" || true)
[ "${#model_files[@]}" -gt 0 ] || exit 0

# Look at the changed LINES, not the changed files: `app/persistence/*.py`
# holds the repository next to the ORM model, so a file-level trigger would
# fire on every query change. These tokens are the ones that move DDL —
# `enum_column`/`JSONColumn`/`UtcDateTime` are omitted deliberately because they
# appear inside `mapped_column(...)`, which is already listed.
SCHEMA_TOKENS='__tablename__|__table_args__|mapped_column\(|Mapped\[|UniqueConstraint\(|CheckConstraint\(|PrimaryKeyConstraint\(|ForeignKey\(|Index\('

changed_lines="$(
  git diff -U0 "$from_ref" "$to_ref" -- "${model_files[@]}" 2>/dev/null |
    grep -E '^[+-][^+-]' || true
)"
[ -n "$changed_lines" ] || exit 0

if ! printf '%s\n' "$changed_lines" | grep -qE "$SCHEMA_TOKENS"; then
  exit 0
fi

offenders="$(printf '  - %s\n' "${model_files[@]}")"
cat >&2 <<EOF

migration-required: a model's schema changed and no migration ships with it.

Changed under ${PERSISTENCE}, with schema-shaped edits (a table name, a
mapped_column, a Mapped[...] annotation, or a constraint/index):
${offenders}
No file was added or amended under ${VERSIONS} in this push.

Every model change ships with its Alembic migration in the same PR — otherwise
\`alembic check\` fails in the Postgres integration suite, which is the most
expensive place in this repo to learn it. Write one:

  just db-revision "describe the change"

then review it (batch/move-and-copy style, so one file runs on SQLite and
Postgres) and include it in this push.

If this change genuinely needs no migration — a relationship, a property, a
type-only annotation — say so explicitly:

  SKIP=migration-required git push

EOF
exit 1
