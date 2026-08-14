#!/usr/bin/env bash
# Validation harness for scripts/check-migration-required.sh
set -uo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/check-migration-required.sh"
ROOT="$(mktemp -d)"
PASS=0; FAIL=0

cd "$ROOT"
git init -q . && git config user.email t@t && git config user.name t
mkdir -p backend/app/persistence/alembic/versions

cat > backend/app/persistence/wellness.py <<'PY'
class WellnessDayRow(Base):
    __tablename__ = "wellness_days"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    local_date: Mapped[date] = mapped_column(Date, unique=True)

class WellnessRepository:
    async def get(self, d): ...
PY
cat > backend/app/persistence/alembic/versions/0013_wellness.py <<'PY'
revision = "0013"
def upgrade(): ...
PY
git add -A && git commit -qm base
BASE="$(git rev-parse HEAD)"

run() { # name expected_exit
  local name="$1" want="$2"
  set +e
  out="$(PRE_COMMIT_FROM_REF="$BASE" PRE_COMMIT_TO_REF=HEAD bash "$SCRIPT" 2>&1)"
  got=$?
  set -e
  if [ "$got" = "$want" ]; then
    printf '  PASS  %-52s (exit %s)\n' "$name" "$got"; PASS=$((PASS+1))
  else
    printf '  FAIL  %-52s (exit %s, wanted %s)\n' "$name" "$got" "$want"
    printf '%s\n' "$out" | sed 's/^/        | /'; FAIL=$((FAIL+1))
  fi
}

reset_tree() { git checkout -q "$BASE" -- . ; git clean -qfd ; git reset -q --hard "$BASE" ; }

echo "== migration-required =="

# 1 — added a column, no migration
reset_tree
printf '    weight_kg: Mapped[float] = mapped_column(Float, nullable=True)\n' >> backend/app/persistence/wellness.py
git add -A && git commit -qm c1
run "added mapped_column, no migration -> BLOCK" 1

# 2 — added a column WITH a new migration
reset_tree
printf '    weight_kg: Mapped[float] = mapped_column(Float, nullable=True)\n' >> backend/app/persistence/wellness.py
printf 'revision = "0014"\n' > backend/app/persistence/alembic/versions/0014_weight.py
git add -A && git commit -qm c2
run "added mapped_column + new migration -> allow" 0

# 3 — repository method only, no schema tokens
reset_tree
printf '    async def range(self, a, b): return []\n' >> backend/app/persistence/wellness.py
git add -A && git commit -qm c3
run "repository method only -> allow" 0

# 4 — docstring only inside a model file
reset_tree
printf '# one row per athlete-local date\n' >> backend/app/persistence/wellness.py
git add -A && git commit -qm c4
run "comment/docstring only -> allow" 0

# 5 — nothing under persistence
reset_tree
mkdir -p backend/app/services && printf 'x = 1\n' > backend/app/services/wellness.py
git add -A && git commit -qm c5
run "no persistence change -> allow" 0

# 6 — REMOVED a column, no migration
reset_tree
grep -v 'local_date' backend/app/persistence/wellness.py > t && mv t backend/app/persistence/wellness.py
git add -A && git commit -qm c6
run "removed mapped_column, no migration -> BLOCK" 1

# 7 — model change + AMENDED an existing migration
reset_tree
printf '    spo2: Mapped[float] = mapped_column(Float)\n' >> backend/app/persistence/wellness.py
printf 'def downgrade(): ...\n' >> backend/app/persistence/alembic/versions/0013_wellness.py
git add -A && git commit -qm c7
run "model change + amended migration -> allow" 0

# 8 — a whole new table, no migration
reset_tree
cat >> backend/app/persistence/wellness.py <<'PY'
class WellnessPromptRow(Base):
    __tablename__ = "wellness_prompts"
PY
git add -A && git commit -qm c8
run "new __tablename__, no migration -> BLOCK" 1

# 9 — a new constraint, no migration
reset_tree
printf '    __table_args__ = (UniqueConstraint("local_date"),)\n' >> backend/app/persistence/wellness.py
git add -A && git commit -qm c9
run "new constraint, no migration -> BLOCK" 1

# 10 — unresolvable range (zeros, no origin/main) must not block
reset_tree
printf '    x: Mapped[int] = mapped_column(Integer)\n' >> backend/app/persistence/wellness.py
git add -A && git commit -qm c10
set +e
PRE_COMMIT_FROM_REF="0000000000000000000000000000000000000000" \
  PRE_COMMIT_TO_REF=HEAD bash "$SCRIPT" >/dev/null 2>&1
got=$?; set -e
if [ "$got" = 0 ]; then
  printf '  PASS  %-52s (exit 0)\n' "no resolvable range -> allow, never crash"; PASS=$((PASS+1))
else
  printf '  FAIL  %-52s (exit %s, wanted 0)\n' "no resolvable range -> allow" "$got"; FAIL=$((FAIL+1))
fi

# 11 — path containing a space must not break the diff
reset_tree
cp backend/app/persistence/wellness.py "backend/app/persistence/odd name.py"
printf '    y: Mapped[int] = mapped_column(Integer)\n' >> "backend/app/persistence/odd name.py"
git add -A && git commit -qm c11
run "path with a space -> BLOCK, no crash" 1

echo
echo "  $PASS passed, $FAIL failed"
cd /; rm -rf "$ROOT"
[ "$FAIL" = 0 ]
