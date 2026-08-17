#!/usr/bin/env node
// Unit tests for the pure guard block in .claude/workflows/implement-plan.js.
//
// WHY THIS EXISTS. Every guard in that block refuses an entire run. A false
// negative is how two Alembic heads, an unreviewed push, or a mangled commit
// subject reach `main`; a false positive blocks legitimate work with a message
// that is simply wrong. Both failure modes were found by review rather than by
// use, because the guards only run when a plan is defective — which, in a
// working repo, is almost never. They are pure functions of a parsed plan, so
// they can be proven here without spawning a single agent or touching git.
//
// HOW. The workflow is a self-contained script the Workflow runtime evaluates;
// it cannot import a module. So the block is delimited by sentinels and this
// test extracts it verbatim and evaluates it. One source of truth, no copy to
// drift. If the sentinels move or the block stops being pure, extraction fails
// loudly rather than silently testing nothing.
//
// Run: node scripts/workflow-guards.test.mjs

import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const WORKFLOW = resolve(here, "../.claude/workflows/implement-plan.js");

const source = readFileSync(WORKFLOW, "utf8");
const begin = source.indexOf("// ── GUARDS-BEGIN");
const end = source.indexOf("// ── GUARDS-END");
if (begin === -1 || end === -1 || end < begin) {
  console.error(
    "FATAL: could not find the GUARDS-BEGIN/GUARDS-END sentinels in\n  " +
      WORKFLOW +
      "\nThe guards are no longer extractable, so nothing below tests anything.",
  );
  process.exit(2);
}
const block = source.slice(begin, end);
// Purity check over CODE only — the prose in here legitimately mentions `log()`
// and `await` while explaining why they must not appear.
const codeOnly = block
  .split("\n")
  .filter((line) => !line.trim().startsWith("//"))
  .join("\n");
for (const forbidden of ["await ", "log(", "agent("]) {
  if (codeOnly.includes(forbidden)) {
    console.error(`FATAL: guard block is no longer pure — its code contains \`${forbidden}\`.`);
    process.exit(2);
  }
}

const EXPORTS = [
  "TITLE_RE", "POSITIONAL_RE", "SHELL_UNSAFE_RE", "BRANCH_RE",
  "badTitles", "unsafeTitles", "positionalOffenders", "badBranches",
  "duplicateBranches", "acceptanceless", "unknownDependencies",
  "migrationCollisions", "groupsOf", "blocksSomething", "missingPrerequisites",
  "echoProblems",
];
const G = new Function(`${block}\nreturn {${EXPORTS.join(",")}};`)();

let pass = 0;
let fail = 0;
const ok = (name, cond, detail = "") => {
  if (cond) {
    pass++;
  } else {
    fail++;
    console.log(`  FAIL  ${name}${detail ? `\n        ${detail}` : ""}`);
  }
};
const group = (name) => console.log(`\n${name}`);

// A minimal valid PR, spread-and-override in each case.
const pr = (over = {}) => ({
  title: "feat(wellness): the daily series",
  branch: "feat/wellness-daily-series",
  depends: [],
  owns: [],
  acceptance: ["- [ ] **AC-1** something specific — *unit*, `test_x.py`"],
  ...over,
});

// ── titles ───────────────────────────────────────────────────────────────────
group("titles — must state the pr-title CI rule and nothing stricter");
{
  const accepted = [
    "feat(wellness): the daily series, on every surface",
    "build(devcontainer): worktrees on linux storage, api-types guard",
    "revert: the daily series",
    "fix: a one word",
    // CI's rule is `.+` after the colon: a one-character subject is legal, and
    // demanding a second character made the guard refuse titles CI accepts.
    "fix(a): x",
    // CI's rule is "does not START uppercase"; it does not demand a letter.
    // This repo's prose is full of backticked identifiers and these are legal.
    "feat(api): `GET /wellness/days` returns null, never 0",
    "fix(mcp): 2 tools were registered twice",
    "docs(readme): 'why' comes before 'what'",
  ];
  for (const t of accepted) {
    ok(`accepts ${JSON.stringify(t)}`, G.badTitles([pr({ title: t })]).length === 0);
  }
  const refused = [
    ["Feat(wellness): uppercase type", "type must be lowercase"],
    ["feat(wellness): Uppercase subject", "CI forbids an uppercase-initial subject"],
    ["feat(wellness): trailing period.", "CI forbids a trailing period"],
    ["chore: ", "empty subject"],
    ["not a conventional commit at all", "no type"],
    ["feat(wellness):no space after colon", "malformed"],
  ];
  for (const [t, why] of refused) {
    ok(`refuses ${JSON.stringify(t)} (${why})`, G.badTitles([pr({ title: t })]).length === 1);
  }
}

group("titles — shell-unsafe characters");
{
  // The title is interpolated into `gh pr create --title "…"`, so these are
  // command substitution and the mangled result becomes the subject on main.
  for (const t of [
    "feat(mcp): `get_coaching_context` gains a block",
    "feat(api): costs $5 per call",
    'feat(api): the "wellness" block',
    "feat(api): a\\backslash",
  ]) {
    ok(`flags ${JSON.stringify(t)}`, G.unsafeTitles([pr({ title: t })]).length === 1);
  }
  ok(
    "leaves an ordinary title alone",
    G.unsafeTitles([pr({ title: "feat(wellness): the daily series" })]).length === 0,
  );
}

group("positional labels");
{
  for (const [t, b] of [
    ["feat(wp-1): add the thing", "feat/thing"],
    ["feat(wellness): baselines", "feat/phase2-baselines"],
    ["feat(wellness): increment 1 capture", "feat/x"],
    ["feat(wellness): step 3 of the rollout", "feat/x"],
  ]) {
    ok(`flags ${JSON.stringify(t)} / ${b}`, G.positionalOffenders([pr({ title: t, branch: b })]).length === 1);
  }
  // Must not fire on a legitimate number that is not a position.
  for (const [t, b] of [
    ["feat(api): support http2 upgrades", "feat/http2"],
    ["fix(zones): zone 3 boundary is inclusive", "fix/zone-boundary"],
  ]) {
    ok(`allows ${JSON.stringify(t)}`, G.positionalOffenders([pr({ title: t, branch: b })]).length === 0);
  }
}

group("branch names");
{
  ok("accepts feat/wellness-daily-series", G.badBranches([pr()]).length === 0);
  ok("accepts fix/a.b_c-d", G.badBranches([pr({ branch: "fix/a.b_c-d" })]).length === 0);
  for (const b of ["wellness-daily-series", "feat/Wellness", "feat/", "feat/a b", "feat/a;rm -rf /"]) {
    ok(`refuses ${JSON.stringify(b)}`, G.badBranches([pr({ branch: b })]).length === 1);
  }
}

group("duplicate branches");
{
  ok(
    "flags two PRs sharing a branch",
    G.duplicateBranches([pr(), pr({ title: "feat(x): other" })]).length === 1,
  );
  ok(
    "allows distinct branches",
    G.duplicateBranches([pr(), pr({ branch: "feat/other", title: "feat(x): other" })]).length === 0,
  );
}

group("acceptance criteria — a PR with none is an unreviewed push");
{
  ok("flags an empty list", G.acceptanceless([pr({ acceptance: [] })]).length === 1);
  ok("flags a missing key", G.acceptanceless([pr({ acceptance: undefined })]).length === 1);
  ok("allows one criterion", G.acceptanceless([pr()]).length === 0);
}

group("unknown dependencies");
{
  const a = pr({ branch: "feat/a", title: "feat(a): a" });
  const b = pr({ branch: "feat/b", title: "feat(b): b", depends: ["feat/a"] });
  ok("allows a dependency inside the plan", G.unknownDependencies([a, b]).length === 0);
  const c = pr({ branch: "feat/c", title: "feat(c): c", depends: ["feat/nope"] });
  const found = G.unknownDependencies([a, c]);
  ok("flags a dependency outside the plan", found.length === 1 && found[0].includes("feat/nope"), JSON.stringify(found));
}

// ── the guard that plan-template.md promises and the code had lost ───────────
group("migration collisions — one owner per concurrent group");
{
  const mig = (branch) => pr({ branch, title: `feat(x): ${branch}`, owns: ["backend/app/persistence/alembic/versions/0014_x.py"] });
  const plain = (branch) => pr({ branch, title: `feat(x): ${branch}`, owns: ["app/domain/x.py"] });
  ok("flags two migration owners in one group", G.migrationCollisions([[mig("feat/a"), mig("feat/b")]]).length === 1);
  ok("allows one migration owner in a group", G.migrationCollisions([[mig("feat/a"), plain("feat/b")]]).length === 0);
  ok("allows none", G.migrationCollisions([[plain("feat/a"), plain("feat/b")]]).length === 0);
  ok(
    "allows one owner per group across two groups",
    G.migrationCollisions([[mig("feat/a")], [mig("feat/b")]]).length === 0,
  );
  ok(
    "matches the word 'migration' too",
    G.migrationCollisions([[
      pr({ branch: "feat/a", owns: ["a migration for wellness_prompts"] }),
      pr({ branch: "feat/b", owns: ["the migration chain"] }),
    ]]).length === 1,
  );
}

// ── grouping: the finding that an OPEN prerequisite is not a merged one ──────
group("groupsOf — only MERGED branches satisfy a dependency");
{
  const a = pr({ branch: "feat/a", title: "feat(a): a" });
  const b = pr({ branch: "feat/b", title: "feat(b): b", depends: ["feat/a"] });
  const c = pr({ branch: "feat/c", title: "feat(c): c" });

  let r = G.groupsOf([a, b], []);
  ok("layers a→b into two groups", !r.error && r.groups.length === 2, JSON.stringify(r.error || r.groups.map((g) => g.map((x) => x.branch))));
  ok("group 1 is just a", !r.error && r.groups[0].map((x) => x.branch).join() === "feat/a");

  r = G.groupsOf([a, c], []);
  ok("independent PRs share one group", !r.error && r.groups.length === 1 && r.groups[0].length === 2);

  // b alone, with a MERGED — buildable.
  r = G.groupsOf([b], ["feat/a"]);
  ok("a merged prerequisite unblocks a scoped run", !r.error && r.groups.length === 1);

  // b alone, with a merely OPEN (i.e. NOT in mergedBranches) — must NOT build.
  r = G.groupsOf([b], []);
  ok(
    "an unmerged prerequisite is refused, not silently satisfied",
    r.error === "unbuildable" && r.remaining.length === 1,
    JSON.stringify(r),
  );

  // cycle
  const x = pr({ branch: "feat/x", title: "feat(x): x", depends: ["feat/y"] });
  const y = pr({ branch: "feat/y", title: "feat(y): y", depends: ["feat/x"] });
  r = G.groupsOf([x, y], []);
  ok("detects a cycle", r.error === "unbuildable" && r.remaining.length === 2);

  // self-dependency is a cycle of one
  const s = pr({ branch: "feat/s", title: "feat(s): s", depends: ["feat/s"] });
  ok("detects a self-dependency", G.groupsOf([s], []).error === "unbuildable");

  ok("empty input yields no groups", (G.groupsOf([], []).groups || []).length === 0);
}

// ── the merge accounting that a real merge once failed to survive ────────────
// Run wf_a4a6a27e (16 Aug 2026) squash-merged PR #54, its merge seat returned
// `merged: true`, and the run still halted with "group 2 needs these merged
// first" and reported "Merged: none" — because the flag was assigned onto an
// object that had crossed a `parallel()` boundary. The fix is that merge state is
// only ever DATA, decided by these two pure functions.
group("blocksSomething — only merge what the DAG is actually waiting on");
{
  const a = pr({ branch: "feat/a", title: "feat(a): a" });
  const b = pr({ branch: "feat/b", title: "feat(b): b", depends: ["feat/a"] });
  ok("a prerequisite with an unmerged dependent blocks", G.blocksSomething([a, b], "feat/a") === true);
  ok("a leaf blocks nothing", G.blocksSomething([a, b], "feat/b") === false);
  ok(
    "a prerequisite whose only dependent already merged blocks nothing",
    G.blocksSomething([a, { ...b, merged: true }], "feat/a") === false,
  );
  ok("a branch nobody names blocks nothing", G.blocksSomething([a, b], "feat/zzz") === false);
  ok("no PRs, nothing blocked", G.blocksSomething([], "feat/a") === false);
  ok("a PR with no depends array is tolerated", G.blocksSomething([{ branch: "feat/a" }], "feat/a") === false);
}

group("missingPrerequisites — what the next group cannot start without");
{
  const next = [pr({ branch: "feat/c", title: "feat(c): c", depends: ["feat/a", "feat/b"] })];
  ok("both missing", G.missingPrerequisites(next, [], []).join() === "feat/a,feat/b");
  ok("one merged this run", G.missingPrerequisites(next, ["feat/a"], []).join() === "feat/b");
  ok("one already on main from the plan", G.missingPrerequisites(next, [], ["feat/b"]).join() === "feat/a");
  ok("both accounted for", G.missingPrerequisites(next, ["feat/a"], ["feat/b"]).length === 0);
  ok(
    "a prerequisite named by two PRs is reported once",
    G.missingPrerequisites(
      [next[0], pr({ branch: "feat/d", title: "feat(d): d", depends: ["feat/a"] })],
      [],
      [],
    ).join() === "feat/a,feat/b",
  );
  ok("a group with no dependencies needs nothing", G.missingPrerequisites([pr()], [], []).length === 0);
  ok("undefined lists are tolerated", G.missingPrerequisites(next, undefined, undefined).length === 2);
}

// ── the integrity check on the parse seat's echo ─────────────────────────────
group("echoProblems — a truncated copy of the plan JSON is detected");
{
  const good = { prCount: 2, planSnapshot: "/x/plan.md", prs: [
    { title: "feat(a): a", branch: "feat/a", why: "w", delivers: "d" },
    { title: "feat(b): b", branch: "feat/b", why: "w", delivers: "d" },
  ] };
  ok("a faithful echo has no problems", G.echoProblems(good).length === 0, JSON.stringify(G.echoProblems(good)));
  ok(
    "a dropped PR is caught by the count",
    G.echoProblems({ ...good, prs: good.prs.slice(0, 1) }).some((s) => s.includes("reported 2")),
  );
  ok("no PRs at all is caught", G.echoProblems({ ...good, prs: [] }).length >= 1);
  ok("a missing snapshot path is caught", G.echoProblems({ ...good, planSnapshot: "" }).some((s) => s.includes("planSnapshot")));
  ok(
    "a PR that lost its branch is caught",
    G.echoProblems({ ...good, prs: [{ ...good.prs[0], branch: "" }, good.prs[1]] }).some((s) => s.includes("title or branch")),
  );
  ok(
    "a PR that lost its why is caught",
    G.echoProblems({ ...good, prs: [{ ...good.prs[0], why: "" }, good.prs[1]] }).some((s) => s.includes("why/delivers")),
  );
  ok(
    "a missing prCount is not treated as a mismatch",
    G.echoProblems({ planSnapshot: "/x", prs: good.prs }).length === 0,
  );
}

// ── the whole script must be loadable by the runtime ─────────────────────────
// The Workflow runtime evaluates this file as an ASYNC FUNCTION BODY — which is
// why `node --check` cannot validate it (top-level `return` and `await` are legal
// there and illegal in a module). A syntax error is otherwise discovered only at
// launch, after the operator has been told the run started.
group("the workflow script itself");
{
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  let err = null;
  try {
    // The runtime lifts `export const meta` out before evaluating the body, so
    // the export keyword is stripped here for the same reason.
    new AsyncFunction(
      "args", "log", "phase", "agent", "parallel", "pipeline", "budget", "workflow",
      source.replace(/^export const meta/m, "const meta"),
    );
  } catch (e) {
    err = e;
  }
  ok("parses as an async function body, the way the runtime evaluates it", err === null, err && err.message);
  ok("declares its meta block as an export", /^export const meta = \{/m.test(source));

  // The mechanical steps must stay mechanical. Each of these replaced an agent's
  // judgement after it cost a real run; a prompt that stops invoking the script
  // silently hands the job back to a model.
  for (const [what, needle] of [
    ["the plan parser", "scripts/parse-plan.mjs"],
    ["the CI classifier", "scripts/ci-status.mjs"],
    ["the Docker lock", "scripts/docker-lock.sh"],
    ["the one-command gate", "just gate"],
  ]) {
    ok(`invokes ${what} (${needle})`, source.includes(needle));
  }
  for (const script of ["parse-plan.mjs", "ci-status.mjs", "docker-lock.sh"]) {
    ok(`${script} exists on disk`, existsSync(resolve(here, script)));
  }
  // `gh pr checks --watch` does not return: two seats blocked on it for 155
  // minutes on 16 Aug 2026 and only woke when the operator typed into the parent
  // session. Nothing in this workflow may use it again.
  const watchLines = source.split("\n").filter((l) => /pr checks[^\n]*--watch/.test(l));
  ok(
    "never blocks on `gh pr checks --watch` — it may only appear as a prohibition",
    watchLines.every((l) => /never/i.test(l)),
    JSON.stringify(watchLines),
  );
  ok("and the prohibition is actually stated", watchLines.length > 0);
}

console.log(`\n  ${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
