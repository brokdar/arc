#!/usr/bin/env node
// Simulate whole `implement-plan` runs with stubbed agents, and assert the
// orchestration.
//
// WHY THIS EXISTS. `scripts/workflow-guards.test.mjs` proves the pure guards, but
// every bug that actually cost a run on 16 Aug 2026 lived in the ORCHESTRATION —
// the wiring between seats, which no test could reach because reaching it meant
// spawning 12 agents and touching a real remote:
//
//   · a prerequisite really merged, and the run reported "Merged: none" and
//     halted, because the merge flag was written onto an object that had crossed
//     a `parallel()` boundary;
//   · the same run's closing line said "Stopped: none" while `halted` was set and
//     listed the merged PR under "open";
//   · a re-review after a CI fix re-judged every criterion from scratch;
//   · a local verification that skipped four tiers still fed the merge decision.
//
// The Workflow runtime evaluates the script as an async function body with
// `agent`, `parallel`, `log` and `phase` injected. So does this file — with an
// `agent` that returns canned structured output per seat label. That makes the
// whole pipeline testable in milliseconds: seat order, loop bounds, stop reasons,
// what merges, what halts, and what each prompt actually tells its agent.
//
// `parallel` here deep-clones what its thunks return, deliberately: the runtime
// hands back values that do not preserve object identity, which is what made the
// merge-flag bug possible. Cloning makes this harness STRICTER than the runtime,
// so anything that passes here cannot depend on cross-boundary mutation.
//
// Run: node scripts/implement-plan.sim.test.mjs

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const SOURCE = readFileSync(resolve(here, "../.claude/workflows/implement-plan.js"), "utf8").replace(
  /^export const meta/m,
  "const meta",
);
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const RUN = new AsyncFunction(
  "args", "log", "phase", "agent", "parallel", "pipeline", "budget", "workflow", SOURCE,
);

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

// ── the fake plan ────────────────────────────────────────────────────────────

const AC = "- [ ] **AC-1** something specific happens — *unit*, `test_x.py`\n- Edge: nothing at all";
const prOf = (over = {}) => ({
  title: `feat(x): the ${(over.branch || "feat/a").split("/")[1]} thing`,
  branch: "feat/a",
  depends: [],
  why: "because the coach cannot see it",
  delivers: "the thing",
  reuses: "the other thing",
  owns: [],
  needsDocker: false,
  triggers: [],
  decisions: ["chose X | displaces Y | lands in `mod.py` docstring"],
  acceptance: [AC],
  prExists: false,
  merged: false,
  prNumber: null,
  ...over,
});

const planOf = (prs, over = {}) => ({
  ok: true,
  exitCode: 0,
  feature: "The thing the coach cannot see",
  source: "issue #99",
  why: "x".repeat(120),
  openQuestions: [],
  featureAcceptance: ["- [ ] **AC-9** integrated — *verified against `main`*"],
  planSnapshot: "/repo/.claude/plan-snapshots/thing-plan.md",
  planSha: "abc123abc123",
  prCount: prs.length,
  prs,
  ...over,
});

// ── the harness ──────────────────────────────────────────────────────────────

const DEFAULTS = {
  setup: { ok: true, headBranch: null, dirty: [], reused: false, prerequisitesOnMain: ["yes"], detail: "ready" },
  implement: { summary: "built it", fileMap: ["a.py — the thing"], weakSpots: [] },
  gate: { gateExit: 0, gateTail: "GATE OK", committed: true, sha: "c0mm1t", files: ["a.py"], detail: "committed" },
  review: { status: "APPROVED", rightThingBuilt: "yes", criteria: [], gaps: "", issues: [] },
  pr: { ok: true, number: 101, url: "https://gh/pr/101", sha: "c0mm1t", detail: "opened" },
  ci: { exitCode: 0, status: "GREEN", failing: [], detail: "6 passed", invocations: 1 },
  // A cleanup-only seat reports merged:false; the merging one is overridden per
  // scenario. `finish()` must not trust a merged:true from a seat it never asked
  // to merge, which is asserted below.
  finish: { merged: false, worktreeRemoved: true, detail: "cleaned up" },
  merge: { merged: true, mergeSha: "merge5ha", worktreeRemoved: true, detail: "merged and cleaned" },
  localCi: { status: "GREEN", checks: ["gate: PASS"], notRun: [], preexisting: [], commented: true, detail: "ok" },
};

// Which default a label falls back to. Order matters: longest match first.
const ROUTES = [
  ["parse-plan", "parse"],
  [":setup", "setup"],
  [":implement", "implement"],
  [":gate", "gate"],
  [":review", "review"],
  [":fix", "implement"],
  [":pr", "pr"],
  [":ci-push", "pr"],
  [":ci", "ci"],
  [":local-ci", "localCi"],
  [":merge-and-cleanup", "merge"],
  [":cleanup", "finish"],
  ["verify-feature", "review"],
];

async function simulate({ plan, replies = {}, argsIn }) {
  const calls = [];
  const logs = [];
  const counts = {};
  const agent = async (prompt, opts) => {
    const label = (opts && opts.label) || "(unlabelled)";
    counts[label] = (counts[label] || 0) + 1;
    calls.push({ label, prompt, opts });
    if (Object.prototype.hasOwnProperty.call(replies, label)) {
      const r = replies[label];
      return typeof r === "function" ? r(calls) : r;
    }
    if (label === "parse-plan") return plan;
    const hit = ROUTES.find(([needle]) => label.includes(needle));
    const kind = hit ? hit[1] : null;
    if (kind === "parse") return plan;
    if (!kind) return { detail: "unrouted" };
    const base = DEFAULTS[kind];
    if (kind === "setup") return { ...base, headBranch: label.split(":")[0] };
    if (kind === "pr") {
      // A distinct PR number per branch, so the report is checkable.
      const branch = label.split(":")[0];
      const n = 100 + [...branch].reduce((a, c) => a + c.charCodeAt(0), 0) % 89;
      return { ...base, number: n, url: `https://gh/pr/${n}` };
    }
    return base;
  };
  // Deep-clone across the boundary — see the header. `undefined` fields vanish,
  // exactly as they would through a serializing runtime.
  const parallel = async (thunks) => {
    const out = await Promise.all(thunks.map((t) => t().catch(() => null)));
    return out.map((r) => (r === undefined ? null : JSON.parse(JSON.stringify(r))));
  };
  const result = await RUN(
    argsIn,
    (m) => logs.push(String(m)),
    () => {},
    agent,
    parallel,
    async (items) => items,
    { total: null, spent: () => 0, remaining: () => Infinity },
    async () => null,
  );
  return { result, calls, logs, counts, labels: calls.map((c) => c.label), log: logs.join("\n") };
}

const promptFor = (sim, label) => (sim.calls.find((c) => c.label === label) || {}).prompt || "";
const seatsFor = (sim, branch) => sim.labels.filter((l) => l.startsWith(`${branch}:`)).map((l) => l.split(":")[1]);

// ── 1. the happy path ────────────────────────────────────────────────────────
group("a clean run of two independent PRs");
{
  const prs = [prOf({ branch: "feat/a", title: "feat(a): the a thing" }), prOf({ branch: "feat/b", title: "feat(b): the b thing" })];
  const sim = await simulate({ plan: planOf(prs), argsIn: { plan: "thing-plan.md" } });
  const r = sim.result;
  ok("both PRs are open", r.open.length === 2, JSON.stringify(r));
  ok("nothing merged — both are leaves", r.merged.length === 0);
  ok("nothing stopped", r.stopped.length === 0);
  ok("not halted", r.halted === null);
  ok("the plan sha is reported", r.planSha === "abc123abc123");
  ok(
    "the seat order per PR is setup → implement → gate → review → pr → ci → cleanup",
    seatsFor(sim, "feat/a").join(",") === "setup,implement,gate,review,pr,ci,cleanup",
    seatsFor(sim, "feat/a").join(","),
  );
  ok("no merge seat ran for a leaf", !sim.labels.some((l) => l.includes("merge")));
  ok("the two PRs ran concurrently in one group", /Group 1\/1/.test(sim.log));
  ok("each PR reports its own number", r.open[0].pr !== r.open[1].pr, JSON.stringify(r.open));
  ok("gate/review/ci loop counts are reported", r.open.every((o) => o.gateLoops === 0 && o.reviewLoops === 0 && o.ciLoops === 0));
  ok("ciMode is github", r.open.every((o) => o.ciMode === "github"));
  ok("nextAction points at review", /Review and squash-merge/.test(r.nextAction));
}

// ── 2. the F1 regression: a prerequisite that merges ─────────────────────────
group("a prerequisite merges, and the next group starts (the F1 regression)");
{
  const prs = [
    prOf({ branch: "feat/a", title: "feat(a): the a thing" }),
    prOf({ branch: "feat/b", title: "feat(b): the b thing", depends: ["feat/a"] }),
  ];
  const sim = await simulate({ plan: planOf(prs), argsIn: { plan: "thing-plan.md" } });
  const r = sim.result;
  ok("two groups", /2 group\(s\)/.test(sim.log), sim.log.split("\n")[1]);
  ok("the prerequisite is marked auto-merges in the plan log", /feat\/a \(auto-merges\)/.test(sim.log));
  ok("its finish seat is the merging one", sim.labels.includes("feat/a:merge-and-cleanup"));
  ok("the leaf's is not", sim.labels.includes("feat/b:cleanup"));
  ok(
    "the merge SURVIVES back to the report — merged, not open",
    r.merged.length === 1 && r.merged[0].branch === "feat/a",
    JSON.stringify({ merged: r.merged, open: r.open }),
  );
  ok("and it is NOT also listed as open", !r.open.some((o) => o.branch === "feat/a"));
  ok("the dependent was built", r.open.some((o) => o.branch === "feat/b"));
  ok("the run did not halt", r.halted === null, String(r.halted));
  ok("the closing log says what merged", /Merged: feat\/a\(#\d+\)/.test(sim.log), sim.log.split("\n").pop());
  ok("the merge is logged when it lands", /squash-merged/.test(sim.log));
  ok(
    "the dependent's setup seat is told to verify the prerequisite on origin/main",
    /PREREQUISITE CHECK/.test(promptFor(sim, "feat/b:setup")) &&
      promptFor(sim, "feat/b:setup").includes("feat/a"),
  );
}

// ── 3. a prerequisite that will not merge ────────────────────────────────────
group("a prerequisite that will not merge halts the DAG, and says so consistently");
{
  const prs = [
    prOf({ branch: "feat/a", title: "feat(a): the a thing" }),
    prOf({ branch: "feat/b", title: "feat(b): the b thing", depends: ["feat/a"] }),
  ];
  const sim = await simulate({
    plan: planOf(prs),
    replies: { "feat/a:merge-and-cleanup": { merged: false, detail: "blocked by a required check" } },
    argsIn: { plan: "thing-plan.md" },
  });
  const r = sim.result;
  ok("halted", typeof r.halted === "string" && r.halted.includes("feat/a"), String(r.halted));
  ok("nothing is reported merged", r.merged.length === 0);
  ok("the dependent was NOT built", !sim.labels.some((l) => l.startsWith("feat/b:")));
  ok("the prerequisite is open for the human", r.open.length === 1 && r.open[0].branch === "feat/a");
  ok("the failed merge is logged with its reason", /did NOT merge — blocked by a required check/.test(sim.log));
  ok("the closing line carries the halt, not 'Stopped: none' alone", /HALTED: group 2 needs/.test(sim.log), sim.log.split("\n").pop());
  ok("nextAction tells the operator what to resolve", /Resolve the stop/.test(r.nextAction));
}

// ── 4. the gate ──────────────────────────────────────────────────────────────
group("the gate is a hard gate: red twice and no PR is opened");
{
  const red = { gateExit: 1, gateTail: "FAILED tests/unit/test_x.py::test_y", committed: false, detail: "ruff and a test" };
  const sim = await simulate({
    plan: planOf([prOf()]),
    replies: { "feat/a:gate": red, "feat/a:gate-retry1": red, "feat/a:gate-retry2": red },
    argsIn: { plan: "thing-plan.md" },
  });
  const r = sim.result;
  ok("stopped", r.stopped.length === 1);
  ok("the reason names the gate", /gate red after 2 fix loop/.test(r.stopped[0].reason), r.stopped[0].reason);
  ok("the exit code reaches the report", r.stopped[0].gateExit === 1);
  ok("NO review ran — nothing is judged against a tree that fails its own gate", !sim.labels.some((l) => l.includes(":review")));
  ok("NO PR was opened", !sim.labels.some((l) => l.endsWith(":pr")));
  ok("exactly two fix agents ran", sim.counts["feat/a:gate-fix1"] === 1 && sim.counts["feat/a:gate-fix2"] === 1);
  ok("the third gate run was the last", sim.counts["feat/a:gate-retry2"] === 1 && !sim.counts["feat/a:gate-retry3"]);
  ok("the fix agent is handed the failure tail", promptFor(sim, "feat/a:gate-fix1").includes("test_x.py::test_y"));
  ok("the recovery line names the command", /just gate/.test(r.stopped[0].recovery));
}

group("a gate that goes green on the second try proceeds, and the first commit keeps the PR title");
{
  const sim = await simulate({
    plan: planOf([prOf()]),
    replies: { "feat/a:gate": { gateExit: 2, gateTail: "boom", committed: false, detail: "red" } },
    argsIn: { plan: "thing-plan.md" },
  });
  ok("it recovered and opened the PR", sim.result.open.length === 1, JSON.stringify(sim.result));
  ok("gateLoops is reported", sim.result.open[0].gateLoops === 1);
  ok(
    "the retry still commits under the PR title, not a 'fix the gate' subject",
    promptFor(sim, "feat/a:gate-retry1").includes("Subject EXACTLY"),
    promptFor(sim, "feat/a:gate-retry1").slice(0, 200),
  );
}

group("a green gate that refuses to commit is its own finding, not a red gate");
{
  const sim = await simulate({
    plan: planOf([prOf()]),
    replies: { "feat/a:gate": { gateExit: 0, gateTail: "GATE OK", committed: false, detail: "an unrecognised path appeared" } },
    argsIn: { plan: "thing-plan.md" },
  });
  ok("stopped as commit-refused", sim.result.stopped[0].reason === "commit-refused", sim.result.stopped[0].reason);
  ok("no gate-fix agent was dispatched", !sim.labels.some((l) => l.includes("gate-fix")));
  ok("the seat's own words reach the recovery line", /unrecognised path/.test(sim.result.stopped[0].recovery));
}

// ── 5. review rejection ──────────────────────────────────────────────────────
group("review rejection: bounded, re-gated, and no PR");
{
  const rejected = {
    status: "REJECTED", rightThingBuilt: "no", criteria: [], gaps: "AC-1's edge has no test",
    processNotes: "the plan snapshot was readable", issues: [{ ac: "AC-1", area: "test_x.py" }],
  };
  const sim = await simulate({
    plan: planOf([prOf()]),
    replies: { "feat/a:review": rejected, "feat/a:review1": rejected, "feat/a:review2": rejected },
    argsIn: { plan: "thing-plan.md" },
  });
  const r = sim.result;
  ok("stopped", r.stopped.length === 1 && /review rejected after 2/.test(r.stopped[0].reason));
  ok("no PR was opened", !sim.labels.some((l) => l.endsWith(":pr")));
  ok("gaps reach the operator", r.stopped[0].gaps === "AC-1's edge has no test");
  ok("processNotes are reported separately from gaps", r.stopped[0].processNotes === "the plan snapshot was readable");
  ok("two fix agents ran", sim.counts["feat/a:fix1"] === 1 && sim.counts["feat/a:fix2"] === 1);
  ok(
    "each fix is re-gated and re-committed before it is re-reviewed",
    sim.counts["feat/a:gate-fix-commit1"] === 1 && sim.counts["feat/a:gate-fix-commit2"] === 1,
  );
  ok(
    "the re-review is TARGETED at the rejected criteria only",
    /TARGETED re-review/.test(promptFor(sim, "feat/a:review1")) &&
      promptFor(sim, "feat/a:review1").includes("AC-1 (test_x.py)"),
  );
  ok("the fix agent is told only what was rejected", promptFor(sim, "feat/a:fix1").includes("AC-1 — responsible area: test_x.py"));
  ok("the recovery line says the work is committed but unpushed", /committed on branch feat\/a .* NOT pushed/.test(r.stopped[0].recovery));
}

group("a dead agent is never an approval");
{
  for (const [label, reason] of [
    ["feat/a:review", "review rejected after 0 fix loop(s) — no PR opened"],
    ["feat/a:implement", "implement-failed"],
    ["feat/a:gate", "gate-agent-died"],
    ["feat/a:setup", "worktree-setup-failed"],
  ]) {
    const replies = { [label]: null };
    if (label === "feat/a:implement") replies["feat/a:implement-retry"] = null;
    const sim = await simulate({ plan: planOf([prOf()]), replies, argsIn: { plan: "thing-plan.md" } });
    ok(`a dead ${label.split(":")[1]} seat stops the PR (${reason})`, sim.result.stopped[0]?.reason === reason, JSON.stringify(sim.result.stopped));
    ok(`  …and opens no PR`, !sim.labels.some((l) => l.endsWith(":pr")));
  }
  const deadFix = await simulate({
    plan: planOf([prOf()]),
    replies: {
      "feat/a:review": { status: "REJECTED", rightThingBuilt: "no", criteria: [], gaps: "g", issues: [{ ac: "AC-1", area: "x" }] },
      "feat/a:fix1": null,
    },
    argsIn: { plan: "thing-plan.md" },
  });
  ok("a dead fix agent stops instead of re-reviewing an unchanged tree", deadFix.result.stopped[0].reason === "fix-agent-died");
  ok("  …and no second review ran", !deadFix.labels.includes("feat/a:review1"));
}

// ── 6. CI ────────────────────────────────────────────────────────────────────
group("CI red → fix → SCOPED re-review → push → green");
{
  let ciCall = 0;
  const sim = await simulate({
    plan: planOf([prOf()]),
    replies: {
      "feat/a:ci": () => {
        ciCall++;
        return { exitCode: 1, status: "RED", failing: ["fuzz"], detail: "schemathesis: undocumented 400", invocations: 1 };
      },
      "feat/a:gate-ci1": { gateExit: 0, gateTail: "GATE OK", committed: true, sha: "f1xsha", files: ["a.py"], detail: "committed" },
    },
    argsIn: { plan: "thing-plan.md" },
  });
  const r = sim.result;
  ok("it recovered to green", r.open.length === 1 && r.open[0].ciLoops === 1, JSON.stringify(r));
  const ciP = promptFor(sim, "feat/a:ci");
  ok(
    "the CI seat runs the script, not a watch",
    /scripts\/ci-status\.mjs/.test(ciP) &&
      ciP.split("\n").filter((l) => l.includes("--watch")).every((l) => /never/i.test(l)),
    ciP.split("\n").filter((l) => l.includes("--watch")).join(" | "),
  );
  ok("  …and maps exit codes, not prose", /0 → GREEN/.test(ciP) && /2 → NO_BUDGET/.test(ciP));
  ok("the fix agent gets the failing check and the diagnosis", promptFor(sim, "feat/a:ci-fix1").includes("fuzz") && promptFor(sim, "feat/a:ci-fix1").includes("undocumented 400"));
  ok("the CI fix is gated and committed before it is pushed", sim.labels.indexOf("feat/a:gate-ci1") < sim.labels.indexOf("feat/a:ci-push1"));
  const rr = promptFor(sim, "feat/a:review-ci1");
  ok("the re-review is SCOPED to the diff since approval, not a full re-judgement", /SCOPED re-review/.test(rr), rr.slice(0, 120));
  ok("  …and it names the approved SHA", rr.includes("c0mm1t") && rr.includes("diff c0mm1t..HEAD"));
  ok("  …and asks only whether the fix damaged an approved criterion", /does the new work break, weaken or hollow out/.test(rr));
  ok("CI was re-checked after the push", sim.labels.includes("feat/a:ci-recheck1"));
}

group("CI that stays red stops with the PR open, and the worktree is reclaimed");
{
  const red = { exitCode: 1, status: "RED", failing: ["integration"], detail: "alembic drift", invocations: 1 };
  const sim = await simulate({
    plan: planOf([prOf()]),
    replies: { "feat/a:ci": red, "feat/a:ci-recheck1": red, "feat/a:ci-recheck2": red },
    argsIn: { plan: "thing-plan.md" },
  });
  const r = sim.result;
  ok("stopped after two loops", /CI RED after 2 fix loop/.test(r.stopped[0].reason), r.stopped[0].reason);
  ok("the PR stays open and is named in the recovery", /#\d+ is open with red CI/.test(r.stopped[0].recovery));
  ok("the worktree is reclaimed, since the work is pushed", sim.labels.includes("feat/a:cleanup"));
  ok("and the recovery says how to get it back", /git worktree add/.test(r.stopped[0].recovery));
}

group("UNKNOWN CI is not treated as red, and never merges");
{
  const sim = await simulate({
    plan: planOf([prOf({ branch: "feat/a" }), prOf({ branch: "feat/b", title: "feat(b): b", depends: ["feat/a"] })]),
    replies: { "feat/a:ci": { exitCode: 3, status: "UNKNOWN", failing: [], detail: "still pending after 4 tries", invocations: 4 } },
    argsIn: { plan: "thing-plan.md" },
  });
  ok("no fix agent was dispatched", !sim.labels.some((l) => l.includes("ci-fix")));
  ok("stopped as UNKNOWN", /CI UNKNOWN/.test(sim.result.stopped[0].reason));
  ok("nothing merged", sim.result.merged.length === 0);
  ok("the dependent group did not start", !sim.labels.some((l) => l.startsWith("feat/b:")));
}

// ── 7. the no-CI fallback ────────────────────────────────────────────────────
group("no CI run registered → local verification");
{
  const noBudget = { exitCode: 2, status: "NO_BUDGET", failing: [], detail: "no run for the head SHA 12m after push", invocations: 1 };
  const sim = await simulate({
    plan: planOf([prOf()]),
    replies: { "feat/a:ci": noBudget },
    argsIn: { plan: "thing-plan.md" },
  });
  const r = sim.result;
  ok("the local-CI seat ran", sim.labels.includes("feat/a:local-ci"));
  ok("no fix agent was dispatched for a defect that does not exist", !sim.labels.some((l) => l.includes("ci-fix")));
  ok("the PR is open with ciMode local", r.open.length === 1 && r.open[0].ciMode === "local");
  ok("the checks it ran are in the report", Array.isArray(r.open[0].localCi));
  const lp = promptFor(sim, "feat/a:local-ci");
  ok("it is told to take the Docker lock", /docker-lock\.sh acquire/.test(lp) && /release/.test(lp));
  ok("it is told to pre-flight the registry", /docker pull alpine/.test(lp));
  ok("it is told how to attribute a pre-existing failure", /PREEXISTING/.test(lp) && /control/.test(lp));
  ok("it is told not to invent the smoke password", /do NOT invent a password/.test(lp));
}

group("a local verification that skipped a tier never auto-merges");
{
  const prs = [prOf({ branch: "feat/a" }), prOf({ branch: "feat/b", title: "feat(b): b", depends: ["feat/a"] })];
  const sim = await simulate({
    plan: planOf(prs),
    replies: {
      "feat/a:ci": { exitCode: 2, status: "NO_BUDGET", failing: [], detail: "none registered", invocations: 1 },
      "feat/a:local-ci": {
        status: "GREEN", checks: ["gate: PASS", "smoke: NOT_RUN — no E2E_PASSWORD"],
        notRun: ["smoke", "schemathesis"], preexisting: [], commented: true, detail: "partial",
      },
    },
    argsIn: { plan: "thing-plan.md" },
  });
  const r = sim.result;
  ok("ciMode is local-partial", r.open[0].ciMode === "local-partial");
  ok("the skipped tiers are named in the report", (r.open[0].notRun || []).join() === "smoke,schemathesis");
  ok("it did NOT merge, even though the DAG waits on it", r.merged.length === 0, JSON.stringify(r.merged));
  ok("its finish seat was the non-merging one", sim.labels.includes("feat/a:cleanup") && !sim.labels.includes("feat/a:merge-and-cleanup"));
  ok("the run halted rather than cutting group 2 from a main without it", /group 2 needs/.test(String(r.halted)));
  ok("the operator is told which tiers were skipped", /could not\s+run \(smoke, schemathesis\)/.test(sim.log), sim.log);
}

group("a pre-existing failure is not this PR's failure");
{
  const sim = await simulate({
    plan: planOf([prOf()]),
    replies: {
      "feat/a:ci": { exitCode: 2, status: "NO_BUDGET", failing: [], detail: "none registered", invocations: 1 },
      "feat/a:local-ci": {
        status: "GREEN",
        checks: ["gate: PASS", "e2e: PREEXISTING — reproduces on 28bc389"],
        notRun: [], preexisting: ["e2e"], commented: true, detail: "e2e already broken on main",
      },
    },
    argsIn: { plan: "thing-plan.md" },
  });
  ok("the PR is not stopped over it", sim.result.stopped.length === 0, JSON.stringify(sim.result.stopped));
  ok("it is reported as pre-existing", (sim.result.open[0].preexisting || []).join() === "e2e");
  ok("and logged as not attributed to this PR", /fail identically on main/.test(sim.log));
  ok("ciMode is local (nothing was skipped)", sim.result.open[0].ciMode === "local");
}

group("a local verification with no evidence posted does not pass");
{
  const sim = await simulate({
    plan: planOf([prOf()]),
    replies: {
      "feat/a:ci": { exitCode: 2, status: "NO_BUDGET", failing: [], detail: "none registered", invocations: 1 },
      "feat/a:local-ci": { status: "GREEN", checks: ["gate: PASS"], notRun: [], preexisting: [], commented: false, detail: "ok" },
    },
    argsIn: { plan: "thing-plan.md" },
  });
  ok("stopped as unrecorded", sim.result.stopped[0].reason === "local-ci-unrecorded");
  ok("the worktree is reclaimed — the work is pushed", sim.labels.includes("feat/a:cleanup"));
  ok("  …and the recovery says how to restore it", /git worktree add/.test(sim.result.stopped[0].recovery));
  const redLocal = await simulate({
    plan: planOf([prOf()]),
    replies: {
      "feat/a:ci": { exitCode: 2, status: "NO_BUDGET", failing: [], detail: "none registered", invocations: 1 },
      "feat/a:local-ci": { status: "RED", checks: ["test-int: FAIL"], notRun: [], preexisting: [], commented: true, detail: "alembic drift" },
    },
    argsIn: { plan: "thing-plan.md" },
  });
  ok("  …with the reason on the report", redLocal.result.stopped[0].reason === "local-ci-red" && /alembic drift/.test(redLocal.result.stopped[0].recovery));
  ok("  …and its worktree reclaimed too", redLocal.labels.includes("feat/a:cleanup"));
}

// ── 8. the parse seat ────────────────────────────────────────────────────────
group("the parse seat is a relay, and its failures are the operator's");
{
  const refused = await simulate({
    plan: { ok: false, exitCode: 2, stderr: "PLAN DEFECTS: PR \"feat(a): a\": no `> **Branch**:` line." },
    argsIn: { plan: "thing-plan.md" },
  });
  ok("a plan defect stops everything", refused.result.error === "plan-defects");
  ok("the script's own words reach the log", /no `> \*\*Branch\*\*:` line/.test(refused.log));
  ok("no seat ran after the parse", refused.labels.length === 1);

  const unreachable = await simulate({ plan: { ok: false, exitCode: 3, stderr: "gh pr list failed" }, argsIn: { plan: "p.md" } });
  ok("an unreachable remote refuses rather than guessing", unreachable.result.error === "parse-refused");

  const dead = await simulate({ plan: null, argsIn: { plan: "p.md" } });
  ok("a dead parse seat is not an empty plan", dead.result.error === "parse-agent-died");

  const truncated = await simulate({
    plan: planOf([prOf({ branch: "feat/a" })], { prCount: 3 }),
    argsIn: { plan: "p.md" },
  });
  ok("a truncated echo of the plan JSON is caught", truncated.result.error === "parse-echo-corrupt", JSON.stringify(truncated.result));
  ok("  …and says what did not add up", /reported 3 PR\(s\)/.test(truncated.log));

  const noSnapshot = await simulate({ plan: planOf([prOf()], { planSnapshot: "" }), argsIn: { plan: "p.md" } });
  ok("a missing plan snapshot is caught before any agent reads it", noSnapshot.result.error === "parse-echo-corrupt");
}

// ── 9. the plan guards still refuse whole runs ───────────────────────────────
group("plan guards, end to end through the script");
{
  const cases = [
    ["open-questions", planOf([prOf()], { openQuestions: ["Should it be X? **(confirm)**"] })],
    ["no-why", planOf([prOf()], { why: "too short" })],
    ["bad-pr-title", planOf([prOf({ title: "Feat(a): uppercase" })])],
    ["unsafe-pr-title", planOf([prOf({ title: "feat(a): `backticks`" })])],
    ["positional-label", planOf([prOf({ title: "feat(a): phase 2 of it" })])],
    ["bad-branch-name", planOf([prOf({ branch: "nope" })])],
    ["no-acceptance-criteria", planOf([prOf({ acceptance: [] })])],
    ["unknown-dependency", planOf([prOf({ depends: ["feat/ghost"] })])],
    ["two-migrations-in-group", planOf([
      prOf({ branch: "feat/a", title: "feat(a): a", owns: ["backend/alembic/versions/0015_x.py"] }),
      prOf({ branch: "feat/b", title: "feat(b): b", owns: ["backend/alembic/versions/0015_y.py"] }),
    ])],
  ];
  for (const [expected, plan] of cases) {
    const sim = await simulate({ plan, argsIn: { plan: "p.md" } });
    ok(`refuses the plan with ${expected}`, sim.result.error === expected, JSON.stringify(sim.result));
    ok(`  …before building anything`, !sim.labels.some((l) => l.includes(":implement")));
  }
  const dupe = await simulate({
    plan: planOf([prOf({ branch: "feat/a", title: "feat(a): one" }), prOf({ branch: "feat/a", title: "feat(a): two" })]),
    argsIn: { plan: "p.md" },
  });
  ok("refuses two PRs on one branch", dupe.result.error === "duplicate-branch");
}

// ── 10. scoping ──────────────────────────────────────────────────────────────
group("scoping: onlyBranch, autoMerge, verifyFeature");
{
  const prs = [prOf({ branch: "feat/a" }), prOf({ branch: "feat/b", title: "feat(b): b", depends: ["feat/a"] })];
  const only = await simulate({ plan: planOf(prs), argsIn: { plan: "p.md", onlyBranch: "feat/b" } });
  ok("onlyBranch builds just that PR", !only.labels.some((l) => l.startsWith("feat/a:")), only.labels.join(","));
  ok("  …and it is refused if its prerequisite is not merged", only.result.error === "dependency-cycle", JSON.stringify(only.result));

  const onlyOk = await simulate({
    plan: planOf([prs[0], { ...prs[1], depends: [] }]),
    argsIn: { plan: "p.md", onlyBranch: "feat/b" },
  });
  ok("a scoped run of an independent PR builds it alone", onlyOk.result.open.length === 1 && onlyOk.result.open[0].branch === "feat/b");

  const typo = await simulate({ plan: planOf(prs), argsIn: { plan: "p.md", onlyBranch: "feat/typo" } });
  ok("an unknown branch is a loud stop, not 'nothing to build'", typo.result.error === "unknown-branch");

  const noMerge = await simulate({ plan: planOf(prs), argsIn: { plan: "p.md", autoMerge: false } });
  ok("autoMerge:false merges nothing", noMerge.result.merged.length === 0);
  ok("  …and halts at the group boundary instead", /group 2 needs/.test(String(noMerge.result.halted)));
  ok("  …with no merge seat anywhere", !noMerge.labels.some((l) => l.includes("merge")));

  const already = await simulate({
    plan: planOf([prOf({ merged: true }), { ...prs[1], merged: true }]),
    argsIn: { plan: "p.md", verifyFeature: true },
  });
  ok("verifyFeature with everything merged runs the verification", already.labels.includes("verify-feature"));
  ok("  …and returns a verdict", !!already.result.featureVerdict);
  const blocked = await simulate({ plan: planOf(prs), argsIn: { plan: "p.md", verifyFeature: true } });
  ok("verifyFeature refuses while PRs are unmerged", blocked.result.note === "verify-blocked");
  const noCriteria = await simulate({
    plan: planOf([prOf({ merged: true })], { featureAcceptance: [] }),
    argsIn: { plan: "p.md", verifyFeature: true },
  });
  ok("a verification with nothing to verify is refused, not rubber-stamped", noCriteria.result.error === "no-feature-acceptance");

  const shipped = await simulate({ plan: planOf([prOf({ merged: true, prExists: true })]), argsIn: { plan: "p.md" } });
  ok("an already-merged plan builds nothing", shipped.result.note === "nothing-pending");
  ok("  …and says so without spawning a seat", shipped.labels.length === 1);

  const noPlan = await simulate({ plan: planOf([prOf()]), argsIn: {} });
  ok("no plan is refused up front", noPlan.result.error === "no-plan" && noPlan.labels.length === 0);
}

// ── 11. worktree safety ──────────────────────────────────────────────────────
group("worktree safety");
{
  const dirty = await simulate({
    plan: planOf([prOf()]),
    replies: { "feat/a:setup": { ok: false, headBranch: "feat/a", dirty: ["backend/app/x.py"], detail: "left over" } },
    argsIn: { plan: "p.md" },
  });
  ok("a dirty worktree stops before anything is implemented", dirty.result.stopped[0].reason === "worktree-dirty");
  ok("  …naming the paths", dirty.result.stopped[0].recovery.includes("exists nowhere else"));
  ok("  …and no implement seat ran", !dirty.labels.some((l) => l.includes("implement")));

  const wrongBranch = await simulate({
    plan: planOf([prOf()]),
    replies: { "feat/a:setup": { ok: true, headBranch: "main", dirty: [], detail: "oops" } },
    argsIn: { plan: "p.md" },
  });
  ok("a worktree on the wrong branch stops", wrongBranch.result.stopped[0].reason === "worktree-unsafe");

  const notOk = await simulate({
    plan: planOf([prOf({ depends: [] })]),
    replies: { "feat/a:setup": { ok: false, headBranch: "feat/a", dirty: [], detail: "prerequisite missing" } },
    argsIn: { plan: "p.md" },
  });
  ok("ok:false stops even with a clean tree and the right branch", notOk.result.stopped[0].reason === "worktree-unsafe");
}

// ── 12. Docker serialisation ─────────────────────────────────────────────────
group("Docker PRs run one at a time; others run together");
{
  const prs = [
    prOf({ branch: "feat/a", title: "feat(a): a", needsDocker: true }),
    prOf({ branch: "feat/b", title: "feat(b): b", needsDocker: true }),
    prOf({ branch: "feat/c", title: "feat(c): c" }),
  ];
  const sim = await simulate({ plan: planOf(prs), argsIn: { plan: "p.md" } });
  ok("the group log separates concurrent from serial", /concurrent: feat\/c; serial \(need Docker\): feat\/a, feat\/b/.test(sim.log), sim.log);
  const aImpl = sim.labels.indexOf("feat/a:implement");
  const bImpl = sim.labels.indexOf("feat/b:implement");
  const aDone = sim.labels.indexOf("feat/a:cleanup");
  ok("the second Docker PR starts only after the first finishes", aDone < bImpl, `${aImpl} ${aDone} ${bImpl}`);
  ok("a Docker PR is told to take the lock", /docker-lock\.sh acquire/.test(promptFor(sim, "feat/a:implement")));
  ok("a non-Docker PR is told not to run those tiers at all", /Do NOT run `just test-int`/.test(promptFor(sim, "feat/c:implement")));
}

// ── 13. what the prompts actually say ────────────────────────────────────────
group("prompt contracts");
{
  const sim = await simulate({ plan: planOf([prOf()]), argsIn: { plan: "p.md" } });
  const impl = promptFor(sim, "feat/a:implement");
  ok("the implementer gets the plan SNAPSHOT, not the working copy", impl.includes("/repo/.claude/plan-snapshots/thing-plan.md") && !/\bp\.md\b/.test(impl.split("READ FIRST")[1] || ""));
  ok("  …the feature Why verbatim", impl.includes("WHY THIS FEATURE EXISTS"));
  ok("  …its acceptance criteria verbatim, edges included", impl.includes("**AC-1**") && impl.includes("- Edge: nothing at all"));
  ok("  …the decisions table row and where it lands", impl.includes("lands in `mod.py` docstring"));
  ok("  …an instruction NOT to run the gate itself", /Do NOT run `just check`/.test(impl));
  ok("  …to iterate with targeted tests", /ITERATE WITH TARGETED TESTS/.test(impl));
  ok("  …to keep suite output out of context", /KEEP OUTPUT OUT OF YOUR CONTEXT/.test(impl));
  ok("  …how to wait, since bare sleep is blocked", /run_in_background/.test(impl) && /until-loop|until <check>/.test(impl));
  ok("  …not to touch shared or global config", /Do not touch `git config`/.test(impl) && /~\/\.ssh/.test(impl));
  ok("  …not to pkill by pattern", /Never `pkill` by\s+pattern/.test(impl), "pkill rule missing");
  ok("  …and not to commit", /Do NOT commit/.test(impl));

  const g = promptFor(sim, "feat/a:gate");
  ok("the gate seat runs `just gate` and redirects its output", /just gate >/.test(g));
  ok("  …reports the exit code and a tail", /gateExit/.test(g) && /gateTail/.test(g));
  ok("  …stages explicitly, never -A", /NEVER `git add -A`/.test(g));
  ok("  …excludes agent-memory and the changelog", /agent-memory/.test(g) && /CHANGELOG\.md/.test(g));
  ok("  …is forbidden to fix what it finds", /not even to fix a\s+lint error/.test(g));
  ok("  …and does not push", /Do NOT push/.test(g));

  const rev = promptFor(sim, "feat/a:review");
  ok("the reviewer is told the work IS COMMITTED", /it IS COMMITTED/i.test(rev), rev.slice(0, 300));
  ok("  …and given the one diff command that now works", /diff origin\/main\.\.\.HEAD/.test(rev));
  ok("  …the OBSERVED gate exit code as evidence", /GATE EVIDENCE — observed, not claimed/.test(rev) && /exited\s*0/.test(rev));
  ok("  …not to re-run the suites the gate already ran", /do NOT re-run lint/i.test(rev));
  ok("  …to reject rather than approve against an unreadable plan", /unreadable, say so in `processNotes` and REJECT/.test(rev));
  ok("  …and the criteria with their edges", rev.includes("- Edge: nothing at all"));

  const push = promptFor(sim, "feat/a:pr");
  ok("the push seat backgrounds the push instead of blocking", /git push[^\n]*&\b|> \/tmp\/push-/.test(push));
  ok("  …uses the exact PR title", push.includes(prOf().title));
  ok("  …looks for the issue to close, with the plan's Source as the hint", /Closes #/.test(push) && push.includes("issue #99"));
  ok("  …reports a failed push instead of reconfiguring git", /Do not reconfigure anything/.test(push));
  ok("  …and does not merge", /Do NOT merge/.test(push));

  const fin = promptFor(sim, "feat/a:cleanup");
  ok("the finish seat proves the work is on the remote before removing anything", /git ls-remote --heads origin/.test(fin));
  ok("  …never --force", /Do NOT pass --force/.test(fin));
  ok("  …keeps the branch", /Do NOT\s+delete the branch/.test(fin));
  ok("  …and is told plainly not to merge a leaf", /Do NOT merge it/.test(fin));
}

group("prompt contracts — the merging finish seat");
{
  const prs = [prOf({ branch: "feat/a" }), prOf({ branch: "feat/b", title: "feat(b): b", depends: ["feat/a"] })];
  const sim = await simulate({ plan: planOf(prs), argsIn: { plan: "p.md" } });
  const m = promptFor(sim, "feat/a:merge-and-cleanup");
  ok("it squash-merges", /gh pr merge \d+ --squash/.test(m));
  ok("it polls until it SEES the merge", /until `state` is\s+`MERGED`/.test(m) && /never optimistically/.test(m));
  ok("it does not touch branch protection", /do NOT\s+change branch protection/.test(m));
  ok("a github-CI merge is not told to read a local-verification comment", !/verification comment/.test(m));

  const localMerge = await simulate({
    plan: planOf(prs),
    replies: {
      "feat/a:ci": { exitCode: 2, status: "NO_BUDGET", failing: [], detail: "none", invocations: 1 },
      "feat/a:local-ci": { status: "GREEN", checks: ["gate: PASS"], notRun: [], preexisting: [], commented: true, detail: "all ran" },
    },
    argsIn: { plan: "p.md" },
  });
  const lm = promptFor(localMerge, "feat/a:merge-and-cleanup");
  ok("a locally-verified merge MUST read the comment first", /READ the verification comment/.test(lm), lm.slice(0, 200));
  ok("  …and the written record wins over the workflow's belief", /the\s+comment, being the written record on the PR, wins/.test(lm));
  ok("  …and it did merge, since nothing was skipped", localMerge.result.merged.length === 1);
}

console.log(`\n  ${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
