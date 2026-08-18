export const meta = {
  name: "implement-plan",
  description:
    "Build a feature plan's pull requests: each in its own worktree, developed TDD against its acceptance criteria and edge cases, gated by a deterministic `just gate` run and then by an independent arc-reviewer, then pushed as its own PR with CI driven green. PRs the DAG waits on auto-merge so the next group can start; leaf PRs stay open for the operator.",
  // Only the statically-known group is declared. Per-PR groups are created at
  // runtime by their agents' `phase:` option — their titles are dynamic, and
  // declaring them here would render empty groups on a scoped run.
  phases: [{ title: "Parse", detail: "plan → PRs, via scripts/parse-plan.mjs" }],
};

// ── What this script may and may not do ──────────────────────────────────────
// The Workflow runtime has NO shell and NO filesystem: this script can only call
// `agent()`. So every mechanical step is a SCRIPT in `scripts/`, run by the
// cheapest possible seat, whose exit code and output tail come back as
// structured data for the seats that exercise judgement. Three steps moved out
// of judgement this way, each after costing a real run:
//   · parsing the plan      → scripts/parse-plan.mjs   (tested)
//   · the pre-review gate   → `just gate`              (one exit code)
//   · CI status / no-budget → scripts/ci-status.mjs    (tested)
// Do not put any of them back in a prompt. A model asked to derive a mechanical
// fact spends 40 turns and is occasionally wrong in a way nothing detects.

// ── Inputs ───────────────────────────────────────────────────────────────────
// Tolerant of a bare string (hand-launch, or a slash-command wrapper echoing the
// user's words) — but a string cannot carry structured scoping, so an
// unrecognised phrase must never silently WIDEN the run. The blast radius here is
// real PRs on a real remote, so anything unparsed falls through to the plan's own
// dependency order rather than to "everything at once".
function parseFreeform(str) {
  const out = {};
  const md = [...str.matchAll(/@?([./\w-]+\.md)\b/g)].map((m) => m[1]);
  if (md.length) out.plan = md[0];
  const br = str.match(/\b((?:feat|fix|perf|refactor|revert|docs|chore|build|ci|test|style)\/[\w./-]+)/);
  if (br) out.onlyBranch = br[1];
  // Tested against the string WITHOUT the plan filename: `implement
  // verify-zones-plan.md` used to parse as a feature-verification of a plan it
  // then refused to build.
  const rest = md.length ? str.replace(md[0], " ") : str;
  if (/\bverify\b/i.test(rest)) out.verifyFeature = true;
  if (!out.plan) out.plan = str.trim();
  return out;
}

const A = (() => {
  if (typeof args !== "string") return args || {};
  try {
    const p = JSON.parse(args);
    return typeof p === "string" ? parseFreeform(p) : p || {};
  } catch {
    return parseFreeform(args);
  }
})();

const PLAN = A.plan || null;
const ONLY_BRANCH = A.onlyBranch || null;
const VERIFY_FEATURE = A.verifyFeature === true || A.verifyFeature === "true";
// The operator may opt out of prerequisite auto-merge and merge everything by
// hand. Off by default would deadlock the DAG, so it defaults ON.
const AUTO_MERGE = A.autoMerge === false || A.autoMerge === "false" ? false : true;

// Review reject → fix → targeted re-review, this many times, then a hard stop
// with NO PR opened. CI red → fix → scoped re-review → push → re-watch, likewise.
// A red gate → fix → re-gate, likewise. All bounded: an unbounded loop burns
// tokens on something that needs a human.
const MAX_REVIEW_LOOPS = 2;
const MAX_CI_LOOPS = 2;
const MAX_GATE_LOOPS = 2;

// Seat tiers. The judging seats are expensive because judgement is the product;
// everything mechanical is cheap because it runs a script and reports an exit
// code. Cheap seats were 4–14 turns each on sonnet in the 16 Aug 2026 runs.
const CHEAP = { model: "sonnet", effort: "low" };

if (!PLAN) {
  log("No plan given. Pass { plan: '<slug>-plan.md' }. Aborting.");
  return { error: "no-plan" };
}
// The freeform fallback accepts any string, and PLAN is interpolated into
// `node scripts/parse-plan.mjs <PLAN>` for an agent to run. The guard block
// validates titles and branches for exactly this reason; a path deserves the
// same, and a bare phrase ("build the thing") is a mis-parse, not a plan.
if (!/^[\w][\w./-]*\.md$/.test(PLAN) || PLAN.includes("..")) {
  log(
    `HARD STOP: "${PLAN}" is not a plan path. Pass { plan: "<slug>-plan.md" } — a relative path to a ` +
      `markdown file in the repository root. Nothing was run.`,
  );
  return { error: "bad-plan-path", plan: PLAN };
}

// ── Schemas ──────────────────────────────────────────────────────────────────

// The parse seat runs a script and hands back what it printed. `ok` and
// `exitCode` are the only required fields BECAUSE the failure paths (a plan
// defect, an unreachable remote) have no payload to report — requiring the
// payload would force a dead agent into inventing one.
const PARSE_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["ok", "exitCode"],
  properties: {
    ok: { type: "boolean" },
    exitCode: { type: "number" },
    // Verbatim stderr when the script refused. Relayed to the operator.
    stderr: { type: "string" },
    feature: { type: "string" },
    // The plan's `> **Source**:` line — the push seat's hint for the issue this
    // work closes.
    source: { type: "string" },
    why: { type: "string" },
    openQuestions: { type: "array", items: { type: "string" } },
    featureAcceptance: { type: "array", items: { type: "string" } },
    // The run-local copy of the plan. Every later prompt points at THIS, never
    // at the operator's working copy.
    planSnapshot: { type: "string" },
    planSha: { type: "string" },
    // Cross-checked against `prs.length`: the seat has to copy a ~16 KB JSON
    // document into its structured output, and a truncated echo that drops a PR
    // would otherwise read as a shorter plan.
    prCount: { type: "number" },
    // The script prints this and the prompt says to copy the JSON verbatim, so
    // `additionalProperties: false` made those two instructions contradict each
    // other at the first seat of every run.
    remote: { type: "object" },
    prs: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["title", "branch", "depends", "acceptance", "prExists", "merged"],
        properties: {
          title: { type: "string" },
          branch: { type: "string" },
          depends: { type: "array", items: { type: "string" } },
          why: { type: "string" },
          delivers: { type: "string" },
          reuses: { type: "string" },
          owns: { type: "array", items: { type: "string" } },
          needsDocker: { type: "boolean" },
          triggers: { type: "array", items: { type: "string" } },
          decisions: { type: "array", items: { type: "string" } },
          acceptance: { type: "array", items: { type: "string" } },
          prExists: { type: "boolean" },
          merged: { type: "boolean" },
          prNumber: { type: ["number", "null"] },
        },
      },
    },
  },
};

// Stateless agents: the implementer emits this so the reviewer and any fix agent
// skip re-exploration. Deliberately lean — fileMap is flat strings, not
// array-of-objects (which truncates mid-array and drops the property), and only
// `summary` is required so a partial emit still validates rather than forcing
// endless StructuredOutput retries.
const HANDOFF_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["summary"],
  properties: {
    summary: { type: "string" },
    fileMap: { type: "array", items: { type: "string" } },
    decisions: { type: "array", items: { type: "string" } },
    testLocations: { type: "array", items: { type: "string" } },
    weakSpots: { type: "array", items: { type: "string" } },
  },
};

// The gate seat: run `just gate`, then commit if it passed. `gateExit` is an
// OBSERVED exit code, which is the whole point — it replaces "the implementer
// says the gate is green", a claim that was false on PR #54 and that both the
// review prompt and arc-reviewer.md were told to trust.
const GATE_SCHEMA = {
  type: "object",
  additionalProperties: false,
  // `sha` is required because two later steps interpolate it into a shell
  // command: the push seat names the commit, and the post-CI-fix re-review reads
  // `git diff <sha>..HEAD`. Optional, it rendered `git diff undefined..HEAD` and
  // the one review mode that may only read an incremental diff got none.
  required: ["gateExit", "committed", "sha", "detail"],
  properties: {
    gateExit: { type: "number" },
    // TRUE when the gate failed ONLY on checks that were already failing before
    // this PR existed. Without this the gate — a hard gate, by design — made any
    // pre-existing breakage in the repository fatal to every pull request in the
    // plan: each one would burn two opus fix agents trying to repair something it
    // did not cause, then hard-stop with no PR. Discovered on 17 Aug 2026, when
    // two date-dependent wellness tests failed on a Monday and would have taken
    // the whole run down with them.
    preexistingOnly: { type: "boolean" },
    // The failing check or test names, so "only the ones that were already
    // failing" is a comparison rather than an impression.
    failing: { type: "array", items: { type: "string" } },
    // The last ~25 lines of `just gate` output. Carried to the reviewer as
    // evidence, and to a fix agent as the failure.
    gateTail: { type: "string" },
    committed: { type: "boolean" },
    sha: { type: "string" },
    files: { type: "array", items: { type: "string" } },
    detail: { type: "string" },
  },
};

const REVIEW_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["status", "rightThingBuilt", "criteria", "gaps", "issues"],
  properties: {
    status: { type: "string", enum: ["APPROVED", "REJECTED"] },
    rightThingBuilt: { type: "string" },
    criteria: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["ac", "verdict", "evidence", "edgeCasesCovered"],
        properties: {
          ac: { type: "string" },
          verdict: { type: "string", enum: ["FULFILLED", "NOT_FULFILLED", "PARTIAL"] },
          evidence: { type: "string" },
          // The plan binds edge cases to the criterion they stress; an AC whose
          // happy path passes but whose edges are untested is not fulfilled.
          edgeCasesCovered: { type: "string" },
        },
      },
    },
    // Criteria only. Anything about the run itself — a tool that failed, a file
    // that was missing — goes in `processNotes`, because `gaps` is the field the
    // skill tells the operator to relay verbatim and it was once three
    // paragraphs about an uncommitted branch.
    gaps: { type: "string" },
    processNotes: { type: "string" },
    issues: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["ac", "area"],
        properties: { ac: { type: "string" }, area: { type: "string" } },
      },
    },
  },
};

// Without a schema the setup agent returns a STRING, so every guarantee it is
// asked to establish is discarded: an agent that faithfully reports "the tree is
// dirty, stopping" returns a non-null string and the run proceeds into implement
// anyway. That made the dirty-worktree hard stop unable to fire.
const SETUP_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["ok", "headBranch", "dirty", "detail"],
  properties: {
    ok: { type: "boolean" },
    headBranch: { type: "string" },
    // Every dirty/untracked path in the worktree. MUST be empty to proceed.
    dirty: { type: "array", items: { type: "string" } },
    reused: { type: "boolean" },
    // Prerequisite titles the agent confirmed are on origin/main. This is the
    // SECOND, independent check that a prerequisite really merged — the first
    // being the finish seat that merged it.
    prerequisitesOnMain: { type: "array", items: { type: "string" } },
    detail: { type: "string" },
  },
};

const PR_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["ok", "detail"],
  properties: {
    ok: { type: "boolean" },
    url: { type: "string" },
    number: { type: "number" },
    sha: { type: "string" },
    detail: { type: "string" },
  },
};

// Mapped from `scripts/ci-status.mjs` exit codes, not from prose.
const CI_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["exitCode", "status", "detail"],
  properties: {
    exitCode: { type: "number" },
    // NO_RUNS: exit 2 — no workflow run ever registered for the head SHA. A
    // distinct outcome from RED: nothing is wrong with the code, and the answer
    // is a local verification, not a fix agent hunting a defect that isn't there.
    // Named exactly as `scripts/ci-status.mjs` prints it, so the seat relays a
    // word instead of translating one.
    status: { type: "string", enum: ["GREEN", "RED", "NO_RUNS", "UNKNOWN"] },
    failing: { type: "array", items: { type: "string" } },
    detail: { type: "string" },
    invocations: { type: "number" },
  },
};

const LOCAL_CI_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["status", "checks", "notRun", "commented"],
  properties: {
    // GREEN means "everything that RAN passed" — which is NOT the same as
    // "everything ran". `notRun` is a separate, structured field precisely so
    // the difference is machine-readable: a GREEN with four skipped tiers must
    // never be worth the same as a GREEN with none.
    status: { type: "string", enum: ["GREEN", "RED"] },
    // One entry per CI-equivalent check:
    // "<name>: PASS|FAIL|NOT_RUN|PREEXISTING — <detail>".
    checks: { type: "array", items: { type: "string" } },
    // The names of every check that could NOT be run, with no exceptions and no
    // rounding down. This is the field that decides whether the PR may merge.
    notRun: { type: "array", items: { type: "string" } },
    // Tiers that failed IDENTICALLY on main, with the control SHA as evidence.
    // Without this the honest answer to "e2e was already broken" was RED, which
    // halted a DAG over a defect the PR did not introduce (PR #55, 16 Aug 2026).
    preexisting: { type: "array", items: { type: "string" } },
    commented: { type: "boolean" },
    detail: { type: "string" },
  },
};

// The finish seat: clean up, and merge if the DAG is waiting on this PR. `merged`
// comes back as DATA on the seat's own result — it is never written onto a shared
// object afterwards. Run wf_a4a6a27e squash-merged PR #54, reported
// `merged: true`, and the workflow still halted and printed "Merged: none",
// because the flag was assigned through an object handed back out of
// `parallel()` and that assignment was not visible to the caller.
// What `just gate` says about this checkout BEFORE the run touches anything.
const BASELINE_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["exitCode", "failing", "detail"],
  properties: {
    exitCode: { type: "number" },
    failing: { type: "array", items: { type: "string" } },
    tail: { type: "string" },
    detail: { type: "string" },
  },
};

const FINISH_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["merged", "detail"],
  properties: {
    merged: { type: "boolean" },
    mergeSha: { type: "string" },
    // Read, not decoration: a worktree the seat refused to remove makes the
    // recovery line "restore it with `git worktree add …`" wrong, because the
    // path still exists.
    worktreeRemoved: { type: "boolean" },
    detail: { type: "string" },
  },
};

// ── GUARDS-BEGIN ─────────────────────────────────────────────────────────────
// Everything between these sentinels is PURE: functions of a parsed plan and
// nothing else — no agents, no git, no `log`, no closure over module state.
// `scripts/workflow-guards.test.mjs` extracts this block verbatim and runs cases
// against it, so the guards are proven without spawning a single agent. That
// matters because every one of them refuses an entire run: a false positive
// blocks legitimate work, and a false negative is how two Alembic heads or an
// unreviewed push reach `main`.
//
// KEEP THIS BLOCK PURE. A `log()` or an `await` in here breaks the extraction.

// `pr-title` is a required status check, and this must state ITS rule and no
// more. The workflow refuses the whole plan on a mismatch, so a stricter regex
// here rejects titles CI would accept — see the test's false-positive cases.
// CI (.github/workflows/pr-title.yml): subjectPattern `^(?![A-Z])(?!.*\.$).+$`,
// i.e. the subject must not START uppercase and must not END with a period. It
// does NOT require the first character be a letter, and this repo's prose is
// full of backticked identifiers.
const TITLE_TYPES = "feat|fix|perf|refactor|revert|docs|chore|build|ci|test|style";
const TITLE_RE = new RegExp(`^(${TITLE_TYPES})(\\([^)]+\\))?: (?![A-Z])\\S(?:.*[^.\\s])?$`);
// The hard stop used to print one sentence — "must not start uppercase and must
// not end with a period" — for every rejection, including the ones where neither
// was true (a doubled space after the colon, an unparseable prefix). A guard that
// refuses an entire plan has to say which rule it applied.
const TITLE_SHAPE_RE = new RegExp(`^(${TITLE_TYPES})(\\([^)]+\\))?: \\S`);
function titleProblem(title) {
  if (TITLE_RE.test(title)) return null;
  if (!new RegExp(`^(${TITLE_TYPES})(\\([^)]+\\))?:`).test(title)) {
    return "not `<type>(<scope>): <subject>` with one of the eleven types";
  }
  if (/:\s{2,}/.test(title) || /: *$/.test(title)) return "the subject is empty or starts with a doubled space";
  if (!TITLE_SHAPE_RE.test(title)) return "there is no single space after the colon";
  if (/: [A-Z]/.test(title)) return "the subject starts with an uppercase letter, which `pr-title` refuses";
  if (/[.\s]$/.test(title)) return "the subject ends with a period or whitespace, which `pr-title` refuses";
  return "it does not match the `pr-title` rule";
}

// A positional label tells a reader nothing and outlives the plan that gave it
// meaning — the title is the permanent commit subject on main and the changelog
// entry. Applied to the SCOPE and the BRANCH, which is where they actually
// appeared in this repo's history (`feat(wp-5): …`, `feat/phase2-…`), plus the
// `wp-n` form anywhere. Not to the whole subject: this is a training app, so
// "base phase 1 template" and "step 3 of the ramp test" are domain nouns, and
// this guard refuses the entire plan when it fires.
const POSITIONAL_RE = /\b(wp|pr|phase|slice|step|part|increment)[\s._-]?\d+\b/i;
const WP_ANYWHERE_RE = /\bwp[\s._-]?\d+\b/i;
const scopeOf = (title) => (title.match(/^[a-z]+\(([^)]+)\)/) || [])[1] || "";

// The title is pasted into a double-quoted shell command by an agent
// (`gh pr create --title "…"`), so a backtick or `$` in it is command
// substitution and the mangled result becomes the permanent subject on main.
const SHELL_UNSAFE_RE = /[`$"\\]/;

// Branch names are interpolated into `git worktree add` / `git push` the same way.
// The trailing rules are git's own refname rules (`git check-ref-format`): no
// `..`, no `.lock` suffix, no trailing `.` or `-`. Without them the guard passed
// and `git worktree add` failed later, off in an agent.
const BRANCH_RE = new RegExp(`^(${TITLE_TYPES})\\/(?!.*\\.\\.)(?!.*\\.lock$)[a-z0-9][a-z0-9._-]*[a-z0-9_]$|^(${TITLE_TYPES})\\/[a-z0-9]$`);

// Matches an Alembic revision file, not the word "migration": the old
// `/\bmigration/i` also matched `scripts/check-migration-required.sh` and
// `docs/migration-guide.md`, so two PRs that merely mention one in `Owns` were
// refused as a two-Alembic-heads collision — a false hard stop over the whole
// plan.
const isMigration = (owned) =>
  /alembic[\\/]versions[\\/]|(^|[\\/])versions[\\/][0-9]{2,}[\w.*-]*(\.py)?$|\bmigrations?[\\/]/i.test(owned);

function badTitles(prs) {
  return prs.filter((p) => !TITLE_RE.test(p.title));
}
// Optional fields the parse seat may legitimately omit when they are empty. Read
// unguarded, `p.decisions.length` threw inside `guardrails()` — and a throw in a
// concurrent `buildPr` is swallowed by `parallel()`, so the pull request
// disappeared from `merged`, `open` AND `stopped` and the report said "Nothing to
// do." Normalising once, here, is the only place that cannot be forgotten.
function normalizePrs(prs) {
  return prs.map((p) => ({
    ...p,
    depends: p.depends || [],
    owns: p.owns || [],
    triggers: p.triggers || [],
    decisions: p.decisions || [],
    acceptance: p.acceptance || [],
    why: p.why || "",
    delivers: p.delivers || "",
    reuses: p.reuses || "",
    // Absent reads as "needs Docker": the expensive-but-correct default, matching
    // the parser. A PR wrongly marked otherwise runs `test-int` beside another.
    needsDocker: p.needsDocker !== false,
  }));
}
function unsafeTitles(prs) {
  return prs.filter((p) => SHELL_UNSAFE_RE.test(p.title));
}
function positionalOffenders(prs) {
  return prs.filter(
    (p) =>
      POSITIONAL_RE.test(scopeOf(p.title)) ||
      POSITIONAL_RE.test(p.branch) ||
      WP_ANYWHERE_RE.test(p.title),
  );
}
function badBranches(prs) {
  return prs.filter((p) => !BRANCH_RE.test(p.branch));
}
function duplicateBranches(prs) {
  const seen = new Set();
  const dupes = new Set();
  for (const p of prs) {
    if (seen.has(p.branch)) dupes.add(p.branch);
    seen.add(p.branch);
  }
  return [...dupes];
}
// The TITLE is the identity `prExists` and `merged` are keyed on — it is what
// `gh pr list` is matched against and what squash-merge puts on main. Two PRs
// sharing one means that once the first merges, the second reads as already
// shipped and is silently dropped from the run.
function duplicateTitles(prs) {
  const seen = new Set();
  const dupes = new Set();
  for (const p of prs) {
    if (seen.has(p.title)) dupes.add(p.title);
    seen.add(p.title);
  }
  return [...dupes];
}
// The whole safety story is "an independent agent judges the diff against the
// criteria". A PR with none is an unreviewed push to a protected branch — and it
// is a likely parse failure, since a stray `### heading` becomes a PR with no ACs.
function acceptanceless(prs) {
  return prs.filter((p) => !p.acceptance || p.acceptance.length === 0);
}
function unknownDependencies(prs) {
  const known = new Set(prs.map((p) => p.branch));
  return prs.flatMap((p) =>
    (p.depends || []).filter((d) => !known.has(d)).map((d) => `${p.branch} → ${d}`),
  );
}
// Two branches cut from the same head both writing `alembic/versions/` produce
// two Alembic heads: each is green locally with one head, and main ends up
// broken. `plan-template.md` promises the executor refuses this.
function migrationCollisions(groups) {
  return groups
    .map((g) => g.filter((p) => (p.owns || []).some(isMigration)))
    .filter((owners) => owners.length > 1);
}

// Topological layering. `mergedBranches` is the set that is actually ON main —
// an OPEN prerequisite must not satisfy a dependency, or the next group is cut
// from a main that lacks it. Returns {groups} or {error, remaining}.
function groupsOf(pending, mergedBranches) {
  const remaining = pending.slice();
  const done = new Set(mergedBranches);
  const groups = [];
  let spins = 0;
  while (remaining.length && spins++ < 50) {
    const ready = remaining.filter((p) => (p.depends || []).every((d) => done.has(d)));
    if (!ready.length) return { error: "unbuildable", remaining };
    groups.push(ready);
    for (const p of ready) {
      done.add(p.branch);
      remaining.splice(remaining.indexOf(p), 1);
    }
  }
  return remaining.length ? { error: "unbuildable", remaining } : { groups };
}

// Does the DAG actually WAIT on this branch? Only then is it merged unattended.
// This is narrower than "something in the plan lists it under Depends": a
// dependent that is already merged waits for nothing. It replaces the old
// `dependedOn` set plus a group-index test, and being pure it is testable.
function blocksSomething(prs, branch) {
  return prs.some((x) => (x.depends || []).includes(branch) && !x.merged);
}

// Prerequisites of `nextGroup` that are not demonstrably on main. `mergedNow` is
// what THIS run merged (returned as data by each finish seat), `planMerged` what
// the parse found already on main. Nothing is inferred from mutation.
function missingPrerequisites(nextGroup, mergedNow, planMerged) {
  const have = new Set([...(mergedNow || []), ...(planMerged || [])]);
  return [
    ...new Set(nextGroup.flatMap((p) => (p.depends || []).filter((d) => !have.has(d)))),
  ];
}

// A parse seat has to echo a ~16 KB JSON document into its structured output.
// This is the integrity check on that echo: a truncated array, a dropped PR, or
// a payload that does not match the count the script printed.
function echoProblems(parsed) {
  const out = [];
  if (!parsed.prs || !parsed.prs.length) out.push("no PRs in the parsed payload");
  if (typeof parsed.prCount === "number" && parsed.prs && parsed.prCount !== parsed.prs.length) {
    out.push(`the script reported ${parsed.prCount} PR(s) but the payload carries ${parsed.prs.length}`);
  }
  if (!parsed.planSnapshot) out.push("no planSnapshot path");
  for (const p of parsed.prs || []) {
    if (!p.title || !p.branch) out.push("a PR is missing its title or branch");
    // Only for PRs still to be built. A long-lived plan legitimately has its
    // shipped sections trimmed to a heading, and refusing the run for that
    // reports a corrupt echo when the echo is fine.
    if (!p.merged && (!p.why || !p.delivers)) out.push(`"${p.title || "?"}" is missing why/delivers`);
  }
  return out;
}
// ── GUARDS-END ───────────────────────────────────────────────────────────────

// ── Shared prompt blocks ─────────────────────────────────────────────────────

const wtPath = (p) => `.claude/worktrees/${p.branch.replace(/\//g, "-")}`;
const acBlock = (p) => p.acceptance.map((a) => `- ${a}`).join("\n");

// The feature's Why, carried verbatim into every developer and reviewer prompt.
// This is the only context that cannot be recovered from the codebase, and it is
// what an agent reasons from at every point the plan does not reach.
function whyBlock(parsed, p) {
  return `
WHY THIS FEATURE EXISTS — read this before the task. It is what you reason from wherever the plan
runs out of instructions; a choice that serves the letter of an AC but defeats this is the wrong choice.
${parsed.why}

WHY THIS PR: ${p.why}
`;
}

// Repo facts stated, not discovered. Every one is enforced by a hook, a test or
// an import-linter contract, so stating them cannot drift silently — and it saves
// every agent a rediscovery pass over CLAUDE.md and the justfile.
function guardrails(parsed, p) {
  const wt = wtPath(p);
  return `
WHERE YOU WORK: everything happens in the worktree \`${wt}\` on branch \`${p.branch}\`. Prefix every
command with \`cd ${wt} && …\` (or run git with \`-C ${wt}\`). NEVER edit, stage or run anything in the
main checkout — other PRs are being built there in parallel.

READ FIRST: this PR's section of the plan snapshot \`${parsed.planSnapshot}\` (an absolute path,
readable from anywhere — read it WITHOUT a \`cd\`), the repo's CLAUDE.md, and any \`.claude/rules/*.md\`
whose \`paths:\` match the files you touch. The snapshot is the run's own copy: the operator's working
copy of the plan may be edited or deleted while you work, and on 16 Aug 2026 it was.

NON-NEGOTIABLE:
- TDD is binding. Write the tests first — the happy path AND every edge case listed under each
  acceptance criterion — see them RED for the right reason, then implement to GREEN. Each test
  asserts the SPECIFIC claim its criterion states, on the artifact the criterion names (the stored
  row, the response body, the rendered text), never a proxy.
- Test at the cheapest layer that catches the bug. Anything about JSONB, arrays, upserts,
  constraints or the migration chain belongs in \`backend/tests/integration/\`, not in an
  in-memory-SQLite unit test. A frontend test mocks the network with the typed handlers in
  \`frontend/tests/mocks/handlers.ts\` — never by mocking \`lib/api/client.ts\` — and a fixture must be
  a payload the real API could produce.
- Package managers: \`uv\` for Python, \`bun\` for the frontend. Never npm/npx/pnpm/yarn. Always
  \`uv run\` / \`bun run\`. A \`VIRTUAL_ENV does not match\` warning from uv is expected noise in a
  worktree, not a failure — \`uv run\` ignores the stale value deliberately.
- ITERATE WITH TARGETED TESTS, not with the whole gate: \`uv run pytest <file> -k <name>\`,
  \`bun run vitest run <file>\`. Do NOT run \`just check\`, \`just lint\`, \`just typecheck\` or
  \`just gate\` yourself — a separate cheap seat runs \`just gate\` once when you are done and hands
  its exit code to the reviewer as evidence. Running it here costs minutes and floods your context
  for a result that is recomputed anyway.
- KEEP OUTPUT OUT OF YOUR CONTEXT: redirect any long run to a log and read the tail
  (\`… > /tmp/x.log 2>&1; tail -30 /tmp/x.log\`). Never let a full suite's output land in the
  transcript.
- WAITING: there is no bare \`sleep\`. To wait for something you started, use \`run_in_background\`
  and poll, or an until-loop with a real condition (\`until <check>; do sleep 5; done\`). Never burn
  turns on \`true\` or a shell spin loop.
- STAY INSIDE THE WORKTREE. Do not touch \`git config\`, \`git remote\`, \`~/.ssh\`, \`~/.gitconfig\` or
  anything else outside it — they are shared with every other worktree and with the operator, and a
  push that will not go through is a REPORTED blocker, not a thing to reconfigure. Never \`pkill\` by
  pattern; kill only a PID you started.${
    p.needsDocker
      ? `
- This PR is marked "needs Docker": ALSO run \`just test-int\` and it must be green. The Docker
  tiers bind fixed host ports and one shared compose project name across every checkout, so take
  the lock around them:
    \`bash scripts/docker-lock.sh acquire ${p.branch}\`
    \`… just test-int …\`
    \`bash scripts/docker-lock.sh release ${p.branch}\`   → on EVERY exit path, including failure
  Exit 3 is BUSY, and it prints who holds it. A DIFFERENT label means another run is working: wait,
  retry a few times, and report it rather than running anyway. YOUR OWN label means a seat died
  holding it — release it and retry once.`
      : `
- Do NOT run \`just test-int\`, \`just smoke\`, \`just up\` or \`just infra\`. They bind fixed host ports
  shared with every other worktree, and this PR is not marked as needing them.`
  }${
    p.triggers && p.triggers.length && p.triggers[0] !== "none"
      ? `
- Build steps this PR triggers — run them, VERIFY the output before anything consumes it, and
  leave the result in the tree: ${p.triggers.join("; ")}.`
      : ""
  }
- A model change ships with its Alembic migration in the same PR. A new setting goes in
  \`app/core/config.py\` AND \`.env.example\` (a test enforces it). A backend schema change means
  \`just api-sync\`.
- Layering: imports point inward only (api | mcp → ingest → services → persistence → domain).
  \`app/domain/\` is pure. Endpoints and MCP tools stay thin; services hold the logic and commit the
  transaction; services raise \`AppError\` subclasses, never \`HTTPException\`.
- Reasoning goes where it binds. Each row below names a docstring, comment or test — put it THERE,
  not in the plan:${p.decisions.length ? "\n" + p.decisions.map((d) => `    · ${d}`).join("\n") : " (none recorded)"}
- Fix every problem you touch. No skips, no broken windows, never \`--no-verify\`.
- Do NOT commit, do NOT \`git add\`, do NOT push, and do NOT edit the plan or its snapshot. A
  separate agent runs the gate and commits.
- \`CHANGELOG.md\` is hand-curated by the operator. Never touch it.
`;
}

const HANDOFF_INSTRUCTION = `

Return the CONTEXT HANDOFF in the structured format, kept DELIBERATELY BRIEF so it never overflows
the output budget: a 2-3 sentence summary; \`fileMap\` a flat list of "path — short role" strings (the
~12 that matter, one short line each); \`decisions\`, \`testLocations\` and \`weakSpots\` as terse
one-line phrases. \`weakSpots\` is where you are least confident a criterion is genuinely met, or an
edge case you could not cleanly test — the reviewer reads it first, so be honest rather than
reassuring. Only \`summary\` is required.`;

function handoffBlock(h) {
  if (!h) return "";
  const list = (xs) => (xs && xs.length ? xs.map((x) => `  - ${x}`).join("\n") : "  (none)");
  return `
CONTEXT HANDOFF from this PR's implement step — use it instead of re-exploring:
Summary: ${h.summary}
Files touched:
${list(h.fileMap)}
Key decisions:
${list(h.decisions)}
Tests live at:
${list(h.testLocations)}
Weak spots the implementer flagged:
${list(h.weakSpots)}
`;
}

// ── Prompt builders ──────────────────────────────────────────────────────────

function parsePrompt() {
  return `Run the plan parser and hand back exactly what it printed.

  \`node scripts/parse-plan.mjs ${PLAN}\`

It reads the plan, cross-checks every PR against \`gh pr list\` and \`git log origin/main\`, snapshots
the plan for the rest of the run, and prints ONE JSON document on stdout.

- Exit 0 → return \`ok: true\`, \`exitCode: 0\`, and the JSON's fields as your structured output,
  COPIED VERBATIM. Do not summarise, re-order, re-wrap or "tidy" anything — the acceptance criteria
  are contracts, and \`prCount\` is cross-checked against the number of PRs you hand back, so a
  truncated copy is detected rather than silently building a shorter plan.
- Exit 2 (plan defects), 3 (remote state unavailable) or 4 (usage) → return \`ok: false\`, the
  \`exitCode\`, and the script's stderr verbatim in \`stderr\`. Do NOT try to parse the plan yourself,
  do not work around it, and do not fix the plan.

Run nothing else.`;
}

function setupPrompt(parsed, p) {
  const wt = wtPath(p);
  return `Prepare the worktree for the PR "${p.title}". Idempotent — this may be a re-run.

1. \`git fetch origin main\` (best-effort; note it and continue if offline).

2. ⚠️ CLEANLINESS FIRST — before touching anything. If \`${wt}\` already exists as a worktree
   (\`git worktree list\`), run \`git -C ${wt} status --porcelain -uall\` NOW and put every dirty or
   untracked path in \`dirty\`. If that list is non-empty, return \`ok: false\` IMMEDIATELY and do
   nothing else. That worktree is left over from a run that hard-stopped, its uncommitted work exists
   nowhere else, and the commit step would sweep it into this PR. Do not clean it, do not fast-forward
   over it, do not proceed — a human decides whether to keep or discard it.
   (Everything \`worktree-init\` creates — \`.venv\`, \`node_modules\`, \`.next\`, the dotenv file — is
   gitignored, so a legitimately reusable worktree reports nothing here.)

3. Only if step 2 found the worktree CLEAN: re-base it on current upstream main, because a worktree
   cut before a prerequisite merged is building on a main that lacks what this PR depends on.
   \`git -C ${wt} merge --ff-only origin/main\`. If that is refused because the branch already carries
   commits of its own, leave it and say so — do not rebase or reset work that may be on a PR. Set
   \`reused: true\` and skip to step 5.

4. If it does not exist, create it cut from CURRENT upstream main:
   \`git worktree add ${wt} -b ${p.branch} origin/main\`
   If branch \`${p.branch}\` already exists (a previous run's worktree was cleaned up but its branch
   kept), use \`git worktree add ${wt} ${p.branch}\` and then ALSO
   \`git -C ${wt} merge --ff-only origin/main\` — otherwise it starts from stale main with no rebase.
   If origin/main is unavailable, fall back to \`git worktree add ${wt} -b ${p.branch}\` and say so.

5. \`cd ${wt} && just worktree-init\` — a worktree has no \`.env\`, no \`.venv\` and no \`node_modules\`,
   and the always-run api-schema-sync pre-commit hook refuses to run without the frontend's. Skip
   only if \`${wt}/.venv\` and \`${wt}/frontend/node_modules\` both already exist (test with
   \`[ -d … ]\`, which does not print an error when they do not).

6. Confirm with \`git -C ${wt} rev-parse --abbrev-ref HEAD\` that HEAD is \`${p.branch}\`, and return
   it as \`headBranch\` whatever it turns out to be — do not report the expected value, report the
   observed one.
${
  (p.depends || []).length
    ? `
7. ⚠️ PREREQUISITE CHECK — an INDEPENDENT confirmation that what this PR extends is really on main,
   not merely reported merged by an earlier step. Confirm each of these subjects appears on main:
${p.depends
     .map((d) => `     - "${(byBranch.get(d) || {}).title || d}"   (branch \`${d}\`)`)
     .join("\n")}
   Those are PR TITLES, and they are what to look for: \`main\` is squash-only, so the merge commit's
   subject IS the pull request's title. Searching for the branch name finds nothing.
   \`git log origin/main --format=%s -120\` and match each one exactly.
   List the TITLES you found in \`prerequisitesOnMain\`. If any is NOT on origin/main, return
   \`ok: false\` and say which — this worktree would be built against a main that lacks the work it
   extends.
`
    : ""
}
Return \`ok: true\` only when the worktree exists, HEAD is \`${p.branch}\`, \`dirty\` is empty${
    (p.depends || []).length ? ", and every prerequisite is on origin/main" : ""
  }.
Do not implement anything. Do not run the gate or any test suite.`;
}

function implementPrompt(parsed, p, handoff) {
  return `Implement the pull request "${p.title}" from the plan snapshot ${parsed.planSnapshot},
END TO END, by yourself, using TDD.
${whyBlock(parsed, p)}
DELIVERS: ${p.delivers}

REUSES — extend these rather than growing a parallel implementation: ${p.reuses}

ACCEPTANCE CRITERIA. Each states input, action and the exact expected result, and lists the edge
cases its tests must also cover. An independent reviewer with no write tools will judge your diff
against exactly these, per criterion and per edge case:
${acBlock(p)}
${guardrails(parsed, p)}${handoffBlock(handoff)}${HANDOFF_INSTRUCTION}`;
}

function fixPrompt(parsed, p, issues, handoff, verdict) {
  const what = issues
    ? `REJECTED these acceptance criteria. Fix ONLY these — do not refactor beyond them:
${issues.map((i) => `- ${i.ac} — responsible area: ${i.area}`).join("\n")}`
    : `REJECTED the work without naming a criterion, which means the obstacle is the review itself.
What it reported:
  gaps: ${verdict && verdict.gaps ? verdict.gaps : "(none given)"}
  process notes: ${verdict && verdict.processNotes ? verdict.processNotes : "(none given)"}
Read that, fix the cause it names, and change nothing else.`;
  return `The independent review of "${p.title}" ${what}

For each, make the criterion genuinely true and prove it with a test that asserts that specific claim
at the right level, covering the edge cases the plan lists under it. Run the targeted tests for what
you changed; the gate runs again automatically after you, so do not run it yourself.
${whyBlock(parsed, p)}${handoffBlock(handoff)}${guardrails(parsed, p)}${HANDOFF_INSTRUCTION}`;
}

function gateFixPrompt(parsed, p, gate, handoff) {
  return `\`just gate\` FAILED on "${p.title}" (exit ${gate.gateExit}). Fix the cause.

\`just gate\` is the repository's one pre-review gate — its exact tiers are listed in the recipe's
comment in the \`justfile\`, and it does NOT include the Docker tiers or the commit-time hygiene
hooks. Its tail:

${(gate.gateTail || gate.detail || "").slice(-2500)}

Fix the actual failure — do not silence it, do not skip a test, do not weaken an assertion to make it
pass. If the api-contract check failed, \`just api-sync\` and leave the regenerated
\`frontend/generated/api/\` in the tree. If the migration heuristic failed, the model change needs its
Alembic migration in this PR.

Re-run only the targeted command that was failing. The gate runs again automatically after you.
${whyBlock(parsed, p)}${handoffBlock(handoff)}${guardrails(parsed, p)}${HANDOFF_INSTRUCTION}`;
}

function ciFixPrompt(parsed, p, ci, handoff) {
  return `CI is RED on the PR "${p.title}". Fix it.

Failing checks: ${ci.failing && ci.failing.length ? ci.failing.join(", ") : "(not named)"}
What the CI seat observed — start from this, it already read the log:
${ci.detail || "(nothing recorded)"}

Read the actual failure before changing anything: \`gh run list --branch ${p.branch}\`, then
\`gh run view <run-id> --log-failed\` (redirect it to a log and read the tail). CI runs tiers the
local gate deliberately omits — integration against real Postgres (the only place \`alembic check\`
runs), Playwright e2e, the fullstack smoke suite, and schemathesis — so red here is usually a REAL
defect the gate could not see. Treat it as one unless the log proves otherwise.

If schemathesis found it, fix it AND pin the case as a unit test; that is this repo's standing rule.
If \`alembic check\` found model/migration drift, the migration is missing or incomplete.

Leave the fix uncommitted; the gate seat commits and pushes it.
${whyBlock(parsed, p)}${handoffBlock(handoff)}${guardrails(parsed, p)}${HANDOFF_INSTRUCTION}`;
}

// The gate seat. Runs one command, then commits — which is what makes the
// reviewer's diff real: the implementer is forbidden to commit, so
// `git diff origin/main...HEAD` was empty for every reviewer until now, and all
// seven of them had to improvise their way to the working tree.
function baselinePrompt() {
  return `Measure the repository's gate BEFORE this run builds anything. One command, no judgement.

  \`just gate > /tmp/gate-baseline.log 2>&1; echo "EXIT=$?"\`

Run it in the MAIN checkout — this is the one seat allowed to, and it is safe: the gate does not
reformat sources. No worktree exists yet, and nothing else is running.

Report the exit code, and if it is non-zero, list in \`failing\` the NAME of every check or test that
failed — a pytest node id (\`tests/unit/test_x.py::test_y\`), a vitest file, a ruff rule, the recipe
that exited. Names, not prose: a later step compares each pull request's own gate failures against
this list to tell "you broke it" from "it was already broken". Put the last ~20 meaningful lines in
\`tail\`.

Do not fix anything. Do not commit. Do not touch any file.`;
}

function gatePrompt(p, subject, baseline) {
  const wt = wtPath(p);
  const known = baseline && baseline.exitCode !== 0 && (baseline.failing || []).length
    ? `

⚠️ THE GATE WAS ALREADY RED BEFORE THIS PR EXISTED. Measured on this checkout at the start of the run
(exit ${baseline.exitCode}), these were failing:
${baseline.failing.map((f) => `  · ${f}`).join("\n")}

So if the gate fails HERE on exactly those and nothing else, this pull request did not cause it: set
\`preexistingOnly: true\`, list what failed in \`failing\`, and COMMIT anyway — the work is sound and
the repository's problem is not this PR's to fix. If ANYTHING ELSE fails, that is this PR's: set
\`preexistingOnly: false\` and do not commit.`
    : "";
  return `${known ? known.trim() + "\n\n" : ""}Run the gate on "${p.title}" and, only if it passes, commit the work. Two steps, no judgement.`
    + gateBody(p, wt, subject);
}

function gateBody(p, wt, subject) {
  return `

1. \`cd ${wt} && just gate > /tmp/gate-${p.branch.replace(/\//g, "-")}.log 2>&1; echo "EXIT=$?"\`
   Read the LAST ~40 lines of that log, not the whole thing. Report the exit code in \`gateExit\`, the
   last ~25 meaningful lines in \`gateTail\`, and the NAME of every failing check or test in
   \`failing\`. If it is non-zero and the failures are not the pre-existing ones named above, STOP:
   report \`committed: false\` and stage nothing. Do not fix it, do not re-run it hoping for a
   different answer, do not investigate further.

2. Only on exit 0: commit, staging explicitly by path.
   - \`git -C ${wt} status --porcelain -uall\`, then \`git -C ${wt} add -- <path> …\` for every path
     that belongs to this PR. NEVER \`git add -A\`, \`.\` or \`-u\`. Always exclude anything under
     \`.claude/agent-memory/\` at any depth, and \`CHANGELOG.md\`.
   - Commit. Check first whether this branch already has commits of its own:
     \`git -C ${wtPath(p)} log --oneline origin/main..HEAD\`.
     · none yet → subject EXACTLY: \`${subject}\`  (it is the PR title, and squash-merge makes it
       the subject on main).
     · some already → a Conventional Commit describing THIS commit, e.g.
       \`fix(${p.branch.split("/")[1] || "gate"}): <what this commit changes>\`.
     Body wrapped at ~76 columns, saying WHY.
   - NEVER \`--no-verify\`. Pre-commit hooks may rewrite files (api-schema-sync regenerates
     \`frontend/generated/api/\`); if a hook aborts the commit, re-derive the path list the same way,
     \`git add --\` exactly those, and commit again.
   - Then \`git -C ${wt} show --stat --oneline HEAD\`. Every path must be work this PR actually did.
     This worktree was verified clean at setup, so anything you do not recognise is a bug — report it
     in \`detail\` and set \`committed: false\` rather than leaving it committed.
   - Report the commit SHA in \`sha\` and the committed paths in \`files\`.

Do NOT push. Do NOT open or merge a PR. Do not modify any source file yourself — not even to fix a
lint error the gate reported.`;
}

function reviewPrompt(parsed, p, mode, ctx, gate, sinceSha) {
  const wt = wtPath(p);
  const evidence = `
GATE EVIDENCE — observed, not claimed. \`just gate\` was run by a separate seat and exited
${gate.gateExit} (0 = pass).${
    gate.gateExit !== 0 && gate.preexistingOnly
      ? ` It failed ONLY on checks that were already failing on this checkout before
this pull request existed — ${(gate.failing || []).join(", ")} — so they are NOT this PR's and NOT a
reason to reject it. Judge the diff on its own criteria. If you believe one of them IS caused by this
diff, say so in \`processNotes\` and REJECT.`
      : ""
  } Its tiers are listed in the \`gate\` recipe's comment in the \`justfile\`
(lint, types, both unit suites, the production build, api-contract drift); the Docker tiers and the
commit-time hooks are NOT in it, and on a first pass the migration heuristic has no commit range to
read, so it says nothing:
${((gate && (gate.gateTail || gate.detail)) || "(no tail reported)").slice(-1200)}

So do NOT re-run lint, type-check, the unit suites or the build. Judge whether the diff is what the
criteria asked for, and run only the targeted tests that prove a specific criterion.`;

  const where = `
WHERE THE WORK IS: the worktree \`${wt}\`, branch \`${p.branch}\`, and it IS COMMITTED — the gate seat
committed it before you were called. So the diff is real:
  \`git -C ${wt} diff origin/main...HEAD --stat\`   then the same without \`--stat\`, per path
Run targeted tests with \`cd ${wt} && …\`, never in the main checkout.
Plan: the run's snapshot at \`${parsed.planSnapshot}\` — this PR's section, its acceptance criteria AND
its "Decisions landing in code" table, whose landing sites you verify actually exist in the code. Read
it at that absolute path, without a \`cd\`. If it is unreadable, say so in \`processNotes\` and REJECT
rather than approving against criteria you could not read in full.`;

  if (mode === "targeted") {
    return `TARGETED re-review of "${p.title}" after a fix. Validate ONLY these previously-rejected
criteria; do not re-open ones that already passed:
${(ctx.rejected || []).map((i) => `- ${i.ac} (${i.area})`).join("\n")}
${where}${evidence}${handoffBlock(ctx.handoff)}
Confirm each is now genuinely fulfilled — including the edge cases listed under it — citing the test
that exercises it. Return the verdict in the structured format.`;
  }

  if (mode === "since") {
    return `SCOPED re-review of "${p.title}" after a CI fix. Every acceptance criterion was already
APPROVED at commit \`${sinceSha}\`; what is new is the CI fix committed on top.

Judge exactly one question: does the new work break, weaken or hollow out any criterion that was
already approved? Read only the incremental diff:
  \`git -C ${wt} diff ${sinceSha}..HEAD\`
Then check the criteria that diff actually touches — a test whose assertion was relaxed, a behaviour
changed to satisfy a schema, an edge case now unreachable. Do NOT re-derive the whole review; the
criteria below are context, not a re-run:
${acBlock(p)}
${where}${evidence}${handoffBlock(ctx.handoff)}
REJECT with the specific criterion if the fix damaged one. APPROVE if the fix is orthogonal or
strengthens it. Return the verdict in the structured format.`;
  }

  return `Review the pull request "${p.title}".
${whyBlock(parsed, p)}
Judge the delivered diff against these acceptance criteria, one at a time. Each lists the edge cases
its tests must cover: an AC whose happy path passes but whose listed edges have no test is PARTIAL,
not FULFILLED. Record what you found per criterion in \`edgeCasesCovered\`.
${acBlock(p)}
${where}${evidence}${handoffBlock(ctx.handoff)}
Return the verdict in the structured format.`;
}

function pushPrompt(parsed, p, gate) {
  const wt = wtPath(p);
  return `"${p.title}" passed the gate and independent review, and is committed at \`${gate.sha}\` in
\`${wt}\`. Push it and open its PR.

1. Push. The pre-push hooks take MINUTES — they re-run the type-checkers, both unit suites, the
   production build and the repository's own tooling suites — and a foreground push has hit the tool
   timeout before, so start it in the background and poll:
     \`cd ${wt} && git push -u origin ${p.branch} > /tmp/push-${p.branch.replace(/\//g, "-")}.log 2>&1 &\`
   then poll for completion and read the log's tail. Never \`--no-verify\`.
   If the push fails, REPORT it with the tail and set \`ok: false\`. Do not reconfigure anything —
   not \`git remote\`, not \`git config\`, not \`~/.ssh\` — they are shared with every other worktree
   and with the operator.
2. ${
     p.prExists && p.prNumber
       ? `This branch ALREADY has pull request #${p.prNumber} — this run is building onto it, so do NOT
   run \`gh pr create\`, which would fail. Confirm it with \`gh pr view ${p.prNumber} --json number,state,title\`,
   update its body if the work has moved on (\`gh pr edit ${p.prNumber} --body-file <path>\`), and report
   its number.`
       : `Open the PR with EXACTLY this title — it is a required CI check that it be a lowercase-start,
   no-trailing-period Conventional Commit, and squash-merge makes it the commit subject on main:
     ${p.title}
   Write the body to a file and pass \`--body-file\`; it is full of backticks and brackets:
     \`gh pr create --base main --title "${p.title}" --body-file <path>\`
   Follow \`.github/pull_request_template.md\`: pick ONE of Why/What-changed (feature) or Root
   cause/The fix/Impact (bug fix) and delete the other. No checklist — \`just gate\` and CI are
   the evidence, not a hand-ticked list.`
   }
   THE BODY BECOMES THE COMMIT BODY ON MAIN, so lead with WHY this change exists — the problem it
   solves, not a list of files. Write for a reader outside the branch: one line per paragraph, NO
   hard wrapping. Keep it flat: no nested lists, no tables, no \`Key: value\` trailers.
3. LINK THE ISSUE, if there is one. Search \`gh issue list --state all --search "<keywords>"\` for the
   issue this PR closes and, if you find a real match, end the body with \`Closes #<n>\` (one line, one
   keyword per issue). The plan's source line is the best hint: ${parsed.source || "none recorded — check the plan snapshot's `> **Source**:` line"}.
   Do not invent a link, and do not close an issue this PR only partially addresses.
4. Report the PR number, URL and the pushed SHA.

Do NOT merge and do NOT enable auto-merge — a later step decides that.`;
}

function repushPrompt(p, pr, gate) {
  const wt = wtPath(p);
  return `Push the CI fix for "${p.title}" to the existing PR #${pr.number}. It is already committed at
\`${gate.sha}\` in \`${wt}\` and the gate passed.

Start the push in the background and poll its log (the pre-push hooks take minutes):
  \`cd ${wt} && git push > /tmp/repush-${p.branch.replace(/\//g, "-")}.log 2>&1 &\`
Never \`--no-verify\`; never reconfigure git, the remote, or anything under \`~\`.

If the failing check was \`pr-title\`, also fix it with \`gh pr edit ${pr.number} --title …\`, keeping it
a lowercase-start Conventional Commit with no trailing period.

Report the pushed SHA. Do not merge.`;
}

function ciPrompt(p, pr) {
  return `Report CI on PR #${pr.number} (${pr.url}). One script decides this — you only relay it.

  \`node scripts/ci-status.mjs ${pr.number} --deadline 480\`

It polls with a bounded deadline and always returns; never use \`gh pr checks --watch\`, which does
not. Map its EXIT CODE, and nothing else, to \`status\`:

  0 → GREEN      every check that exists concluded, none failed
  1 → RED        a check failed. Copy the failing names into \`failing\` and the printed log URLs
                 into \`detail\`, then — only for a RED — read the actual failure with
                 \`gh run view <run-id> --log-failed\` (redirect, read the tail) and put a one-line
                 diagnosis in \`detail\`. A fix agent works from this, so name the test or the error.
  2 → NO_RUNS  no workflow run ever registered for the head SHA. Nothing is wrong with the code.
                 Do not investigate billing, do not hunt for an error message.
  3 → UNKNOWN    still pending at the deadline, or a check was cancelled. RE-RUN the script (up to 4
                 times total) before reporting UNKNOWN; report the number of invocations.
  4 → UNKNOWN    the PR could not be read; put the stderr in \`detail\`.

Put the script's exit code in \`exitCode\` verbatim. Do not change any code, do not push, do not merge.`;
}

function localCiPrompt(parsed, p, pr) {
  const wt = wtPath(p);
  return `CI never ran on PR #${pr.number} ("${p.title}") — no workflow registered for its head SHA.
Verify the PR LOCALLY against every check CI would have run, then record it on the PR.

⚠️ TAKE THE DOCKER LOCK FIRST, and release it when you are done:
  \`bash scripts/docker-lock.sh acquire local-ci-${p.branch}\`
Exit 3 is BUSY — these tiers bind fixed host ports and one shared compose project name, and on
16 Aug 2026 two runs overlapped for an hour, each believing it was alone. It prints who holds it: a
DIFFERENT label means another run is working, so wait, retry a few times, and if it stays busy record
every Docker tier as NOT_RUN with "docker lock held by <holder>" and skip to the comment. YOUR OWN
label means a seat died holding it — release it and retry once. Release with
\`bash scripts/docker-lock.sh release local-ci-${p.branch}\` on EVERY exit path.

PRE-FLIGHT the Docker tiers before spending time on them: \`timeout 60 docker pull alpine:3\`. If the
registry is unreachable, \`just smoke\` cannot build its images — record it NOT_RUN with that reason
immediately rather than watching a stalled pull. That cost ~30 minutes on 16 Aug 2026.

Work in \`${wt}\`. Run these in order; a later one is worthless if an earlier one is red:
1. \`just gate\`        — the repository's pre-review gate; the recipe's comment lists its tiers
2. \`just test-int\`    — integration on real Postgres; the ONLY place \`alembic check\` runs, so this
                          is what catches model/migration drift
3. \`just e2e\`         — Playwright, UI-only
4. \`just smoke\`       — full Docker stack. \`E2E_PASSWORD\` must match the bcrypt hash in \`.env\`; it
                          is in this environment if the operator exported it. If it is absent, record
                          smoke NOT_RUN with that reason — do NOT invent a password and do NOT claim
                          it passed.
5. schemathesis — only if this PR changed an API route or schema. Follow the recipe in CLAUDE.md. If
   you cannot obtain a session cookie, record NOT_RUN with the reason.

ATTRIBUTION. If a tier fails, find out whether it fails for THIS PR before reporting it. Run the same
tier on the main checkout at \`origin/main\` as a control (a read-only clone under /tmp if you need
one — do NOT build in the main checkout, other PRs are being built there). If it fails identically,
record it \`PREEXISTING — reproduces on <main-sha>\` in \`checks\`, list its name in \`preexisting\`, and
do NOT count it as this PR's failure. That distinction halted a whole DAG once over four e2e tests
that were already broken on main.

Then post ONE comment with \`gh pr comment ${pr.number} --body-file <path>\` stating: that CI did not
run because no workflow registered for the head SHA; the commit SHA these checks ran against; and
every check as PASS / FAIL / NOT_RUN / PREEXISTING with the reason for anything not PASS.

BE HONEST. This comment stands in for CI on a public pull request, and one claiming coverage that was
not achieved is worse than no comment at all. Never mark something PASS you did not observe pass.

Return \`status\` GREEN only if every check that ran passed or was PREEXISTING; RED if any failed for
this PR. ⚠️ \`notRun\` MUST list every check you could not run — all of them, no rounding down and no
"it probably would have passed". That field decides whether this PR may merge without a human: a
verification that skipped the Docker tiers is green on the gate alone, which the pre-push hook already
ran, so it proves nothing new.

Do not change any code and do not merge. Plan snapshot, if you need it: ${parsed.planSnapshot}`;
}

function finishPrompt(p, pr, doMerge, localCiNote, heldBackWhy) {
  const wt = wtPath(p);
  return `PR #${pr.number} ("${p.title}") is ${doMerge ? "review-approved and CI-verified" : "finished for this run"}.
${
  doMerge
    ? "Merge it, then clean up."
    : heldBackWhy
      ? `Clean up. Do NOT merge it: something in this plan does depend on it, but it may not merge unattended because ${heldBackWhy}. It stays open for the operator.`
      : "Clean up. Do NOT merge it — nothing unmerged in this plan is waiting on it."
}

1. Confirm the work is safely on the remote: \`git ls-remote --heads origin ${p.branch}\` must return a
   ref, and \`git -C ${wt} status --porcelain -uall\` must be empty. If EITHER fails, STOP: report it
   and remove nothing. An uncommitted change here exists nowhere else.
${
  doMerge
    ? `2. Merge. Other pull requests in this plan DEPEND on this being on main before they can start.
   \`main\` is protected: squash-only, PR required.
     \`gh pr merge ${pr.number} --squash --auto\`
   If auto-merge is not enabled for the repository the command errors — then confirm the checks are
   currently passing (\`gh pr checks ${pr.number}\`) and merge directly with \`gh pr merge ${pr.number} --squash\`.${
     localCiNote
       ? `
   ⚠️ CI never ran on this PR; it was verified locally. Before merging, READ the verification comment
   on the PR yourself and confirm it records every check as PASS or PREEXISTING. If it is missing, or
   records any check as NOT_RUN or FAIL, do NOT merge: report \`merged: false\` with what it said. The
   workflow only routes a PR here when it believes every check ran, so a NOT_RUN you find there means
   the two disagree — and the comment, being the written record on the PR, wins.`
       : ""
   }
   Then poll \`gh pr view ${pr.number} --json state,mergeStateStatus,mergedAt\` until \`state\` is
   \`MERGED\`, up to 10 times. Report \`merged: true\` with the resulting SHA only when you have SEEN it
   merged — never optimistically. If it will not merge (conflicts, a failing required check, a
   blocked branch), report \`merged: false\` with the exact reason. Do NOT force anything and do NOT
   change branch protection.
3. `
    : "2. "
}Remove the worktree, which holds ~1.4 GB of installed dependencies it no longer needs:
   \`git worktree remove ${wt}\` from the main checkout. Do NOT pass --force: if git refuses because
   the tree is dirty, that is step 1's guarantee failing — keep the worktree and say so. Do NOT
   delete the branch \`${p.branch}\`.

Report what happened. \`merged\` MUST be false unless you saw state MERGED.`;
}

// ── Stage 0: parse ───────────────────────────────────────────────────────────

log(
  `Launch: raw=${JSON.stringify(args ?? null)} parsed=${JSON.stringify(A)} plan=${PLAN}` +
    (ONLY_BRANCH ? ` only=${ONLY_BRANCH}` : "") +
    (VERIFY_FEATURE ? " verify-feature" : "") +
    (AUTO_MERGE ? "" : " auto-merge=off"),
);

phase("Parse");
const parsed = await agent(parsePrompt(), {
  label: "parse-plan",
  phase: "Parse",
  schema: PARSE_SCHEMA,
  ...CHEAP,
});

if (!parsed) {
  log("The parse seat returned nothing. Nothing was built.");
  return { error: "parse-agent-died" };
}
if (!parsed.ok) {
  // Exit 2 is a plan defect, 3 an unreachable remote, 4 usage — all of them the
  // operator's to fix, all of them reported in the script's own words.
  log(
    `HARD STOP: scripts/parse-plan.mjs refused (exit ${parsed.exitCode}).\n` +
      (parsed.stderr || "(no stderr reported)"),
  );
  const why =
    parsed.exitCode === 2 ? "plan-defects" : parsed.exitCode === 4 ? "plan-not-found" : "parse-refused";
  return { error: why, exitCode: parsed.exitCode, stderr: parsed.stderr };
}

// Fill every optional field before anything reads one. Unguarded,
// `p.decisions.length` threw inside a concurrent `buildPr`, `parallel()` turned
// the rejection into `null`, `.filter(Boolean)` dropped it, and the run reported
// "Nothing to do." for a plan it had been asked to build.
parsed.prs = normalizePrs(parsed.prs);

const echoBad = echoProblems(parsed);
if (echoBad.length) {
  log(
    `HARD STOP: the parse seat's copy of the plan JSON does not hold up:\n` +
      echoBad.map((s) => `  - ${s}`).join("\n") +
      `\nRe-launch; if it repeats, run \`node scripts/parse-plan.mjs ${PLAN}\` by hand and read it.`,
  );
  return { error: "parse-echo-corrupt", problems: echoBad };
}

// ── Guards: plan defects, caught before any agent runs ───────────────────────

if (parsed.openQuestions && parsed.openQuestions.length) {
  log(
    `HARD STOP: ${parsed.openQuestions.length} open question(s) still marked (confirm) in ${PLAN}. ` +
      `A PR built on a guess at one of these is rework:\n` +
      parsed.openQuestions.map((q) => `  - ${q}`).join("\n"),
  );
  return { error: "open-questions", openQuestions: parsed.openQuestions };
}

if (!parsed.why || parsed.why.trim().length < 80) {
  log(
    `HARD STOP: ${PLAN} has no usable "## Why" section. The developer agents reason from it wherever ` +
      `the plan runs out of instructions — a plan that only says what to build produces guesses at ` +
      `every ambiguity. Write it and re-launch.`,
  );
  return { error: "no-why" };
}

// The title/positional/branch guards run over UNMERGED PRs only. A long-lived
// plan legitimately contains PRs that merged long ago under older conventions —
// this repo's own history has `feat(wp-5): …` — and refusing the whole plan for
// a title that already shipped leaves no way to proceed.
const live = parsed.prs.filter((p) => !p.merged);

const bad = badTitles(live);
if (bad.length) {
  log(
    `HARD STOP: ${bad.length} title(s) would fail the required \`pr-title\` check. The title becomes ` +
      `the commit subject on main, so each is refused for a stated reason:\n` +
      bad.map((p) => `  - "${p.title}" — ${titleProblem(p.title)}`).join("\n"),
  );
  return { error: "bad-pr-title", titles: bad.map((p) => p.title) };
}

const unsafe = unsafeTitles(live);
if (unsafe.length) {
  log(
    `HARD STOP: ${unsafe.length} title(s) contain a backtick, $, quote or backslash. The title is ` +
      `pasted into \`gh pr create --title "…"\` by an agent, so those characters are command ` +
      `substitution — and squash-merge makes the mangled result the permanent subject on main:\n` +
      unsafe.map((p) => `  - "${p.title}"`).join("\n"),
  );
  return { error: "unsafe-pr-title", titles: unsafe.map((p) => p.title) };
}

const positional = positionalOffenders(live);
if (positional.length) {
  log(
    `HARD STOP: ${positional.length} PR(s) carry a positional label in the title or branch. The title ` +
      `becomes the permanent commit subject on main and the changelog entry — it must say what the ` +
      `change does:\n` +
      positional.map((p) => `  - "${p.title}"  (${p.branch})`).join("\n"),
  );
  return { error: "positional-label", offenders: positional.map((p) => p.title) };
}

const badBranch = badBranches(live);
if (badBranch.length) {
  log(
    `HARD STOP: ${badBranch.length} branch name(s) are not \`<type>/<kebab-case>\`. They are ` +
      `interpolated into git commands:\n` +
      badBranch.map((p) => `  - ${p.branch}`).join("\n"),
  );
  return { error: "bad-branch-name", branches: badBranch.map((p) => p.branch) };
}

const dupes = duplicateBranches(parsed.prs);
if (dupes.length) {
  log(
    `HARD STOP: ${dupes.length} branch name(s) appear on more than one PR. Two PRs on one branch ` +
      `share a worktree path, so a concurrent group would run two developers in one tree:\n` +
      dupes.map((b) => `  - ${b}`).join("\n"),
  );
  return { error: "duplicate-branch", branches: dupes };
}

// A PR with no criteria is an unreviewed push: the reviewer is handed an empty
// list, has nothing to reject, approves, and — if anything depends on it — the
// result is squash-merged into protected main.
const dupeTitles = duplicateTitles(parsed.prs);
if (dupeTitles.length) {
  log(
    `HARD STOP: ${dupeTitles.length} title(s) appear on more than one PR. The title is what \`gh pr ` +
      `list\` is matched against, so once the first merges the second reads as already shipped and is ` +
      `dropped without a word:\n` +
      dupeTitles.map((t) => `  - "${t}"`).join("\n"),
  );
  return { error: "duplicate-title", titles: dupeTitles };
}

const noAcs = acceptanceless(live);
if (noAcs.length) {
  log(
    `HARD STOP: ${noAcs.length} PR(s) have no acceptance criteria. The reviewer would have nothing ` +
      `to judge and would approve by default:\n` +
      noAcs.map((p) => `  - "${p.title}"`).join("\n"),
  );
  return { error: "no-acceptance-criteria", titles: noAcs.map((p) => p.title) };
}

const byBranch = new Map(parsed.prs.map((p) => [p.branch, p]));
const unknownDeps = unknownDependencies(parsed.prs);
if (unknownDeps.length) {
  log(`HARD STOP: dependency on a branch not in the plan:\n${unknownDeps.map((d) => `  - ${d}`).join("\n")}`);
  return { error: "unknown-dependency", unknownDeps };
}

// A scoped run naming a branch the plan does not contain must say so. Silently
// filtering to nothing reports "nothing to build", which reads as "the plan is
// complete" — a wrong-direction failure over a typo.
if (ONLY_BRANCH && !byBranch.has(ONLY_BRANCH)) {
  log(
    `HARD STOP: onlyBranch "${ONLY_BRANCH}" is not in ${PLAN}. Branches in this plan:\n` +
      parsed.prs.map((p) => `  - ${p.branch}`).join("\n"),
  );
  return { error: "unknown-branch", requested: ONLY_BRANCH };
}

// ── Feature verification mode ────────────────────────────────────────────────
// The one gate per-PR review structurally cannot pass: criteria that are only
// true once several PRs are merged. Runs against main, after they are.
if (VERIFY_FEATURE) {
  const unshipped = parsed.prs.filter((p) => !p.merged);
  if (unshipped.length) {
    log(
      `Feature verification asked for, but ${unshipped.length} PR(s) are not merged yet: ` +
        unshipped.map((p) => p.branch).join(", ") + ".",
    );
    return { note: "verify-blocked", pending: unshipped.map((p) => p.branch) };
  }
  // A verification with nothing to verify produces a rubber-stamp sign-off — the
  // report the skill tells the operator to sign off on.
  if (!parsed.featureAcceptance || parsed.featureAcceptance.length === 0) {
    log(
      `HARD STOP: ${PLAN} has no "## Feature acceptance" criteria, so there is nothing for this pass ` +
        `to verify. Either write the cross-PR criteria or skip this step deliberately.`,
    );
    return { error: "no-feature-acceptance" };
  }
  phase("Verify feature");
  const task = `Every pull request for "${parsed.feature}" is merged. Verify the feature-scoped
acceptance criteria against the CURRENT main — the ones no single PR could satisfy alone:
${parsed.featureAcceptance.map((a) => `- ${a}`).join("\n")}

WHY THIS FEATURE EXISTS — judge against this, not only against the letter of the criteria:
${parsed.why}

Work in the MAIN checkout (\`git fetch origin main && git switch main && git pull\` first). These
criteria concern capabilities that exist only once the PRs are integrated, so look for what per-PR
review could not see: duplicated work between PRs, one surface contradicting another, a capability
that reached the API but not MCP, a decision that landed in two places with two meanings.

Run the suites you need. \`just test-int\` and \`just smoke\` bind fixed host ports shared with every
worktree, so take the lock around them and release it on every exit path:
  \`bash scripts/docker-lock.sh acquire verify-feature\` (exit 3 = BUSY, and it names the holder: wait
  and retry for another label, release-and-retry-once for your own, or record the tier as not run)
  … \`bash scripts/docker-lock.sh release verify-feature\` on every exit path

Plan snapshot: ${parsed.planSnapshot}. Redirect long runs to a log and read the tail.

This is the report the operator signs off on. Be exhaustive about any criterion that is partial,
misread, or only superficially covered. Return the verdict in the structured format.`;
  const opts = { phase: "Verify feature", agentType: "arc-reviewer", schema: REVIEW_SCHEMA };
  let report = await agent(task, { ...opts, label: "verify-feature" });
  if (report == null) {
    log("Feature verification returned nothing — retrying once.");
    report = await agent(task, { ...opts, label: "verify-feature-retry" });
  }
  if (report == null) {
    log("Feature verification produced no report. Re-launch with { verifyFeature: true }.");
    return { feature: parsed.feature, note: "verification-missing" };
  }
  return { feature: parsed.feature, planSha: parsed.planSha, featureVerdict: report };
}

// ── Select what to build ─────────────────────────────────────────────────────
// `onlyBranch` is the documented re-entry after a hard stop, and the commonest
// stop leaves a PR OPEN (CI red, or a review rejection fixed by hand). Skipping
// on prExists would make that re-entry a no-op that reports "nothing to build" —
// so an explicit branch overrides the skip and says which PR it is pushing onto.
const pending = ONLY_BRANCH
  ? parsed.prs.filter((p) => p.branch === ONLY_BRANCH && !p.merged)
  : parsed.prs.filter((p) => !p.prExists && !p.merged);

if (pending.length === 0) {
  const target = ONLY_BRANCH ? byBranch.get(ONLY_BRANCH) : null;
  if (target && target.merged) {
    log(`"${target.title}" is already merged. Nothing to do.`);
    return { feature: parsed.feature, note: "already-merged", branch: ONLY_BRANCH };
  }
  log(
    `Nothing to build for "${parsed.feature}" — every PR is merged or already has one open. ` +
      `Re-launch with { verifyFeature: true } once they are all merged.`,
  );
  return { feature: parsed.feature, note: "nothing-pending" };
}
if (ONLY_BRANCH && byBranch.get(ONLY_BRANCH).prExists) {
  log(
    `"${ONLY_BRANCH}" already has a pull request; building onto it because it was named explicitly. ` +
      `If that PR should be abandoned instead, close it and delete the branch first.`,
  );
}

// Groups are derived from `depends` against what is actually MERGED. An open
// prerequisite does not count: a dependent is cut from `origin/main`, and code
// that is only on an open PR is not there.
const planMerged = parsed.prs.filter((p) => p.merged).map((p) => p.branch);
const layered = groupsOf(pending, planMerged);
if (layered.error) {
  log(
    `HARD STOP: nothing is buildable — every remaining PR waits on a dependency that is not merged ` +
      `and not in scope. Either the plan has a cycle, or this run was scoped past a prerequisite:\n` +
      layered.remaining
        .map((p) => `  - ${p.branch} waits on [${(p.depends || []).join(", ")}]`)
        .join("\n"),
  );
  return { feature: parsed.feature, error: "dependency-cycle" };
}
const groups = layered.groups;

// `plan-template.md` promises the executor refuses this, so it must actually do
// it: two branches cut from one head both writing a revision give main two heads.
const collisions = migrationCollisions(groups);
if (collisions.length) {
  log(
    `HARD STOP: a concurrent group has more than one PR owning a migration. Both would be cut from ` +
      `the same head and autogenerate the same down_revision, so main ends up with two Alembic ` +
      `heads:\n` +
      collisions
        .map((owners) => `  - ${owners.map((p) => p.branch).join(" ∥ ")}`)
        .join("\n"),
  );
  return {
    feature: parsed.feature,
    error: "two-migrations-in-group",
    groups: collisions.map((o) => o.map((p) => p.branch)),
  };
}

log(
  `${parsed.feature} (plan ${parsed.planSha}) — ${pending.length} PR(s) to build in ${groups.length} group(s):\n` +
    groups
      .map(
        (g, i) =>
          `  ${i + 1}. ${g
            .map((p) => p.branch + (AUTO_MERGE && blocksSomething(parsed.prs, p.branch) ? " (auto-merges)" : ""))
            .join(" ∥ ")}`,
      )
      .join("\n"),
);

// ── The gate's baseline, measured once ───────────────────────────────────────
// The gate is a HARD gate: a red one stops a pull request with nothing pushed.
// That is right when the PR caused it and catastrophic when it did not — every
// PR in the plan would burn two opus fix agents on someone else's breakage and
// then hard-stop. So the run measures the gate before it builds anything, and a
// per-PR failure is compared against that list. Green baseline: nothing changes.
let baseline = null;
if (A.skipGateBaseline !== true) {
  phase("Parse");
  baseline = await agent(baselinePrompt(), { label: "gate-baseline", phase: "Parse", schema: BASELINE_SCHEMA, ...CHEAP });
  if (baseline == null) {
    log("The gate baseline could not be measured; proceeding without it — a red gate will be treated as this PR's.");
  } else if (baseline.exitCode !== 0) {
    log(
      `⚠️ \`just gate\` is ALREADY RED on this checkout (exit ${baseline.exitCode}) before anything is ` +
        `built:\n` +
        (baseline.failing || []).map((f) => `  · ${f}`).join("\n") +
        `\nThese are not attributed to any pull request: a PR whose gate fails on exactly these still ` +
        `commits and is reviewed. Anything else it breaks is its own. Fix them when you can — CI runs ` +
        `them too.`,
    );
  } else {
    log("Gate baseline: green. Any red gate from here belongs to the PR that produced it.");
  }
}

// ── The per-PR pipeline ──────────────────────────────────────────────────────
// implement → GATE+commit → independent review (bounded fix loop) → push+PR →
// CI green → finish (merge if the DAG waits on it, then clean up).
//
// Every seat is a FRESH agent: the reviewer never wrote the code it judges, and
// the fixer is never the implementer either. The reviewer additionally has no
// editing tools — `tools: Read, Bash, Glob, Grep` in .claude/agents/arc-reviewer.md
// — so repairing what it finds would take a deliberate detour through the shell,
// which its own instructions forbid. Not a sandbox; a seat with no reason and no
// convenient means to fix its own findings.
// The gate seat is not a judge and not a developer: it runs one command, commits
// on success, and is explicitly forbidden to fix anything.

async function buildPr(p) {
  // Every agent carries `phase:` explicitly rather than calling the global
  // phase() — PRs run concurrently, and the global is shared mutable state.
  const title = p.branch;
  const dev = { phase: title, model: "opus", schema: HANDOFF_SCHEMA };
  const rev = { phase: title, agentType: "arc-reviewer", schema: REVIEW_SCHEMA };
  const cheap = (label, schema) => ({ label: `${title}:${label}`, phase: title, schema, ...CHEAP });
  const fail = (reason, extra) => {
    log(`HARD STOP — ${p.branch}: ${reason}`);
    return { branch: p.branch, title: p.title, ok: false, merged: false, reason, ...extra };
  };

  const setup = await agent(setupPrompt(parsed, p), cheap("setup", SETUP_SCHEMA));
  if (setup == null) {
    return fail("worktree-setup-failed", {
      recovery: `The setup seat died before reporting. Check \`git worktree list\` and \`git -C ${wtPath(p)} status\`: a worktree may exist half-initialised, and \`just worktree-init\` in it is the repair. Nothing was implemented.`,
    });
  }
  // Act on what it reported. Without these checks the schema would be decoration
  // and the dirty-worktree stop could never fire.
  if (setup.dirty && setup.dirty.length) {
    return fail("worktree-dirty", {
      dirty: setup.dirty,
      recovery: `${wtPath(p)} holds uncommitted work from an earlier run — it exists nowhere else. Keep it or discard it by hand, then re-launch with { onlyBranch: "${p.branch}" }.`,
    });
  }
  if (!setup.ok || setup.headBranch !== p.branch) {
    return fail("worktree-unsafe", {
      detail: setup.detail,
      recovery: `Expected HEAD ${p.branch} in ${wtPath(p)}, agent reported "${setup.headBranch}". Nothing was implemented.`,
    });
  }
  // The SECOND, independent confirmation that a prerequisite really landed — and
  // it has to be READ, or the schema is decoration and the only evidence a merge
  // happened is the seat that claimed to perform it. Titles, not branches:
  // squash-merge puts the PR TITLE on main.
  // Either shape counts. The prompt asks for TITLES (squash-merge puts the title
  // on main), but a seat that answers with branch names must not hard-stop every
  // dependent pull request over a formatting difference.
  const absent = (p.depends || []).filter((d) => {
    const title = (byBranch.get(d) || {}).title || d;
    return !(setup.prerequisitesOnMain || []).some((seen) => seen.includes(title) || seen.includes(d));
  });
  if (setup.reused) {
    log(`${p.branch}: reusing the existing worktree at ${wtPath(p)} (it was clean).`);
  }
  if (absent.length) {
    return fail("prerequisite-not-on-main", {
      detail: setup.detail,
      recovery: `${p.branch} extends ${absent.join(", ")}, and the setup seat did not find that subject on origin/main. Confirm the prerequisite merged (\`git log origin/main --oneline\`), then re-launch with { onlyBranch: "${p.branch}" }.`,
    });
  }

  // A dead implement agent must never advance to review against an empty diff.
  let handoff = await agent(implementPrompt(parsed, p, null), { ...dev, label: `${title}:implement` });
  if (handoff == null) {
    log(`${p.branch}: implement agent returned nothing — retrying once.`);
    handoff = await agent(
      `NOTE: a previous attempt died partway through, so \`${wtPath(p)}\` may already hold partial work
(tests and/or implementation). Inspect what exists before writing: keep what is correct, replace what
is not, and do not be derailed if some tests already exist or already pass. Reach the same end state
as a clean run.

` + implementPrompt(parsed, p, null),
      { ...dev, label: `${title}:implement-retry` },
    );
  }
  if (handoff == null) {
    return fail("implement-failed", {
      recovery: `Inspect ${wtPath(p)} — the dead attempt's partial work is still there. Finish or reset it before re-launching.`,
    });
  }

  // The gate, then the commit. Bounded: a gate that stays red after two fixes is
  // a human's problem, and nothing is pushed.
  let gate = null;
  let gateLoops = 0;
  let gatePreexisting = null;
  for (;;) {
    const tag = gateLoops === 0 ? "gate" : `gate-retry${gateLoops}`;
    gate = await agent(gatePrompt(p, p.title, baseline), cheap(tag, GATE_SCHEMA));
    if (gate == null) return fail("gate-agent-died", { recovery: `Run \`cd ${wtPath(p)} && just gate\` by hand to see where it stands. The work is uncommitted.` });
    if (gate.gateExit === 0 && gate.committed && gate.sha) break;
    // Red, but only on what was already red — and the seat committed anyway.
    // Verified here rather than trusted: every name it reports must appear in the
    // baseline, so a seat that waves through a new failure is caught.
    if (gate.gateExit !== 0 && gate.preexistingOnly && gate.committed && gate.sha) {
      const known = new Set(baseline ? baseline.failing || [] : []);
      const novel = (gate.failing || []).filter((f) => ![...known].some((k) => k.includes(f) || f.includes(k)));
      if (!baseline || baseline.exitCode === 0 || novel.length) {
        return fail("gate-red-claimed-preexisting", {
          gate,
          recovery: `The gate seat committed despite exit ${gate.gateExit}, claiming the failures pre-date this PR — but ${novel.length ? `these are not in the baseline: ${novel.join(", ")}` : "there is no red baseline to compare against"}. Nothing was pushed. Check \`cd ${wtPath(p)} && just gate\` by hand.`,
        });
      }
      log(`${p.branch}: gate red on ${gate.failing.join(", ")} — already failing before this PR, so not attributed to it.`);
      gatePreexisting = gate.failing;
      break;
    }
    // Committed but no SHA is the same class of failure as not committing: two
    // later steps interpolate that SHA into a git command, and `undefined` there
    // gives the scoped re-review no diff at all.
    if (gate.gateExit === 0 && gate.committed && !gate.sha) {
      return fail("commit-sha-missing", {
        gate,
        recovery: `The gate seat reported a commit in ${wtPath(p)} but no SHA. Check \`git -C ${wtPath(p)} log --oneline -3\` before re-launching.`,
      });
    }
    if (gate.gateExit === 0 && !gate.committed) {
      // Green but refused to commit — the seat saw something it did not
      // recognise in the tree. That is a stop, not a retry, and it is NOT the
      // same finding as a red gate, so it is checked before the loop bound.
      return fail("commit-refused", {
        gate,
        recovery: `The gate passed but the commit seat refused: ${gate.detail}. Inspect ${wtPath(p)} by hand.`,
      });
    }
    if (gateLoops >= MAX_GATE_LOOPS) {
      return fail(`gate red after ${gateLoops} fix loop(s) — no PR opened`, {
        gate,
        recovery: `\`just gate\` exits ${gate.gateExit} in ${wtPath(p)} and the work is uncommitted. Read its tail, decide by hand, then re-launch with { onlyBranch: "${p.branch}" }.`,
      });
    }
    gateLoops++;
    log(`${p.branch}: gate red (fix ${gateLoops}/${MAX_GATE_LOOPS}) — ${(gate.detail || "").slice(0, 200)}`);
    const fixed = await agent(gateFixPrompt(parsed, p, gate, handoff), { ...dev, label: `${title}:gate-fix${gateLoops}` });
    // A dead fix agent leaves the tree exactly as it was, so re-gating would
    // produce the same red and burn a second loop for nothing.
    if (fixed == null) {
      return fail("gate-fix-agent-died", {
        gate,
        recovery: `The gate is red in ${wtPath(p)} and the fix agent died. Read \`just gate\`'s output by hand.`,
      });
    }
    handoff = fixed;
  }

  let verdict = await agent(reviewPrompt(parsed, p, "full", { handoff }, gate), { ...rev, label: `${title}:review` });
  let loops = 0;
  while (verdict && verdict.status === "REJECTED" && loops < MAX_REVIEW_LOOPS) {
    loops++;
    // A rejection may legitimately name no criterion — the review prompt itself
    // says to REJECT when the plan snapshot is unreadable. Handing the fix agent
    // an empty list burned both loops on nothing.
    const rejected = verdict.issues && verdict.issues.length ? verdict.issues : null;
    log(`${p.branch} REJECTED (fix ${loops}/${MAX_REVIEW_LOOPS}): ${verdict.gaps}`);
    const fixed = await agent(fixPrompt(parsed, p, rejected, handoff, verdict), { ...dev, label: `${title}:fix${loops}` });
    if (fixed == null) {
      return fail("fix-agent-died", {
        verdict,
        recovery: `The fix agent for ${p.branch} died; the tree is as the reviewer saw it, plus nothing. Decide by hand, then re-launch with { onlyBranch: "${p.branch}" }.`,
      });
    }
    handoff = fixed;
    // The fix has to pass the gate and be committed before it can be reviewed —
    // otherwise the re-review reads a tree that does not match any commit.
    const regate = await agent(
      gatePrompt(p, `fix(${p.branch.split("/")[1] || "review"}): address the review`),
      cheap(`gate-review${loops}`, GATE_SCHEMA),
    );
    if (regate == null || regate.gateExit !== 0 || !regate.committed) {
      return fail("gate-red-after-review-fix", {
        verdict,
        gate: regate,
        recovery: `The review fix left the gate red (exit ${regate ? regate.gateExit : "?"}) in ${wtPath(p)} and it was not committed. Nothing was pushed.`,
      });
    }
    gate = regate;
    verdict = await agent(
      reviewPrompt(parsed, p, rejected ? "targeted" : "full", { handoff, rejected }, gate),
      { ...rev, label: `${title}:review${loops}` },
    );
  }
  // An APPROVED verdict whose own criteria say NOT_FULFILLED is not an approval.
  // arc-reviewer.md forbids it, the data to check it was already in hand, and
  // nothing checked it — so it pushed and squash-merged.
  const unmet = verdict && verdict.status === "APPROVED"
    ? (verdict.criteria || []).filter((c) => c.verdict && c.verdict !== "FULFILLED")
    : [];
  if (unmet.length) {
    return fail("review-self-contradictory", {
      verdict,
      recovery: `The review returned APPROVED while marking ${unmet.map((c) => c.ac).join(", ")} as ${[...new Set(unmet.map((c) => c.verdict))].join("/")}. Nothing was pushed. Read the criteria, decide by hand, then re-launch with { onlyBranch: "${p.branch}" }.`,
    });
  }
  if (!verdict || verdict.status !== "APPROVED") {
    // No PR is opened. Nothing reaches the remote that has not passed an
    // independent check against its own acceptance criteria.
    return fail(`review rejected after ${loops} fix loop(s) — no PR opened`, {
      verdict,
      recovery: `The work is committed on branch ${p.branch} in ${wtPath(p)} but NOT pushed. Read the gaps, decide by hand, then re-launch with { onlyBranch: "${p.branch}" }.`,
    });
  }
  let approvedSha = gate.sha;

  const pr = await agent(pushPrompt(parsed, p, gate), cheap("pr", PR_SCHEMA));
  if (pr == null || !pr.ok || !pr.number) {
    return fail("pr-failed", {
      verdict,
      detail: pr ? pr.detail : "agent returned nothing",
      recovery: `Review-approved and committed, but not on the remote. Check \`git -C ${wtPath(p)} log --oneline -3\` and \`gh pr list\` before re-launching.`,
    });
  }
  log(`${p.branch}: PR #${pr.number} opened — ${pr.url}`);

  let ci = await agent(ciPrompt(p, pr), cheap("ci", CI_SCHEMA));
  if (ci && ci.invocations > 1) {
    log(`${p.branch}: CI status took ${ci.invocations} bounded polls — ${ci.status}.`);
  }
  let ciLoops = 0;
  while (ci && ci.status === "RED" && ciLoops < MAX_CI_LOOPS) {
    ciLoops++;
    log(`${p.branch} CI RED (fix ${ciLoops}/${MAX_CI_LOOPS}): ${ci.detail}`);
    const fixed = await agent(ciFixPrompt(parsed, p, ci, handoff), { ...dev, label: `${title}:ci-fix${ciLoops}` });
    if (fixed == null) {
      return fail("ci-fix-agent-died", {
        pr, verdict, ci,
        recovery: `PR #${pr.number} is red and the fix agent died. Nothing new was pushed.`,
      });
    }
    handoff = fixed;
    const regate = await agent(
      gatePrompt(p, `fix(ci): ${(ci.failing || ["ci"]).join(", ").slice(0, 40)}`),
      cheap(`gate-ci${ciLoops}`, GATE_SCHEMA),
    );
    if (regate == null || regate.gateExit !== 0 || !regate.committed) {
      return fail("gate-red-after-ci-fix", {
        pr, verdict, gate: regate,
        recovery: `The CI fix left the gate red (exit ${regate ? regate.gateExit : "?"}) in ${wtPath(p)}. PR #${pr.number} still carries the old head.`,
      });
    }
    gate = regate;
    // A CI fix changes behaviour, so it is re-reviewed before it goes back up —
    // but SCOPED to the diff since the approval, not a full re-judgement of
    // every criterion. The full pass cost an opus review of 5 ACs for a two-file
    // schemathesis fix (PR #54, 16 Aug 2026).
    const recheck = await agent(reviewPrompt(parsed, p, "since", { handoff }, gate, approvedSha), {
      ...rev,
      label: `${title}:review-ci${ciLoops}`,
    });
    // A DEAD re-review is not an approval.
    if (!recheck || recheck.status !== "APPROVED") {
      return fail(
        recheck ? "CI fix broke the acceptance criteria" : "re-review agent died after a CI fix",
        {
          verdict: recheck, pr,
          recovery: `PR #${pr.number} is open and its latest commit is unreviewed — nothing was pushed. Decide by hand.`,
        },
      );
    }
    approvedSha = gate.sha;
    const repush = await agent(repushPrompt(p, pr, gate), cheap(`ci-push${ciLoops}`, PR_SCHEMA));
    if (repush && repush.ok && repush.sha && gate.sha && !repush.sha.startsWith(gate.sha.slice(0, 7)) && !gate.sha.startsWith(repush.sha.slice(0, 7))) {
      return fail("pushed-a-different-commit", {
        pr, verdict, gate,
        recovery: `The re-review approved ${gate.sha} but the push seat reported pushing ${repush.sha}. PR #${pr.number} may now carry work nothing reviewed. Compare \`git -C ${wtPath(p)} log --oneline -5\` against the PR's head before doing anything else.`,
      });
    }
    if (repush == null || !repush.ok) {
      return fail("ci-push-failed", {
        pr, verdict, gate,
        recovery: `PR #${pr.number} is open and RED, and a reviewed fix is committed at ${gate.sha} in ${wtPath(p)} but NOT pushed — the worktree is kept because it holds the only copy. Push it by hand (\`git -C ${wtPath(p)} push\`) or reset it.${repush ? ` The seat said: ${repush.detail}` : ""}`,
      });
    }
    ci = await agent(ciPrompt(p, pr), cheap(`ci-recheck${ciLoops}`, CI_SCHEMA));
  }

  const status = ci ? ci.status : "UNKNOWN";
  // No run ever registered is not a defect. It needs a local run of everything CI
  // would have done — which is Docker-bound, so it is deferred to the serial pass
  // rather than run here inside a concurrent branch.
  if (status === "NO_RUNS") {
    log(`${p.branch}: CI never registered a run — deferring local verification.`);
    return {
      branch: p.branch, title: p.title, ok: false, merged: false, reason: "needs-local-ci",
      pr, verdict, ci, gate, pr_obj: p, gatePreexisting,
      // Carried through localCiVerify → finish, which reports them. Omitted, the
      // report for every no-CI PR silently lost its loop counts.
      reviewLoops: loops, ciLoops, gateLoops,
    };
  }
  if (status !== "GREEN") {
    const r = fail(`CI ${status} after ${ciLoops} fix loop(s)`, {
      pr, verdict, ci,
      recovery: `PR #${pr.number} is open with ${status.toLowerCase()} CI. It needs a human decision before merge. Its worktree was removed; restore it with \`git worktree add ${wtPath(p)} ${p.branch}\` then \`just worktree-init\`.`,
    });
    await reclaimWorktree(p, pr);
    return r;
  }
  log(`✅ ${p.branch}: PR #${pr.number} green.`);
  return await finish(p, pr, verdict, { reviewLoops: loops, ciLoops, gateLoops, gatePreexisting, ciMode: "github" });
}

// Merge if the DAG is waiting on this PR, then remove the worktree. `merged` is
// returned as data on this PR's own result — never assigned onto an object that
// crossed a `parallel()` boundary, which is how a real merge came back as
// "Merged: none" on 16 Aug 2026.
async function finish(p, pr, verdict, extra) {
  const waited = blocksSomething(parsed.prs, p.branch);
  const doMerge = AUTO_MERGE && waited && extra.ciMode !== "local-partial";
  const heldBackWhy = waited && !doMerge
    ? extra.ciMode === "local-partial"
      ? "its local verification could not run every check"
      : "auto-merge is off for this run"
    : null;
  const res = await agent(finishPrompt(p, pr, doMerge, extra.ciMode !== "github", heldBackWhy), {
    label: `${p.branch}:${doMerge ? "merge-and-cleanup" : "cleanup"}`,
    phase: p.branch,
    schema: FINISH_SCHEMA,
    ...CHEAP,
  });
  // `doMerge &&` is load-bearing, not defensive: a cleanup-only seat was never
  // asked to merge and cannot have, so a `merged: true` from one is noise — and
  // trusting it would report an unmerged PR as merged and let the next group cut
  // from a main that lacks it. The mirror image of the bug that reported a real
  // merge as unmerged, and the more dangerous direction of the two.
  const merged = doMerge && !!(res && res.merged);
  // A merge that was ATTEMPTED and did not land must not read like a leaf that was
  // never meant to merge. Without this the reason lived only in a log line, and in
  // the last group — where there is no next group to halt — the report said
  // "Review and squash-merge the open PRs" as if nothing had gone wrong.
  const mergeFailed = doMerge && !merged;
  if (doMerge) {
    log(
      merged
        ? `🔀 ${p.branch}: PR #${pr.number} squash-merged (${(res.mergeSha || "").slice(0, 9)}).`
        : `${p.branch}: PR #${pr.number} did NOT merge — ${res ? res.detail : "the finish seat returned nothing"}`,
    );
  }
  if (res && res.worktreeRemoved === false) {
    log(`⚠️ ${p.branch}: the worktree was NOT removed — ${res.detail}`);
  }
  return {
    branch: p.branch, title: p.title, ok: true, pr, verdict, merged, mergeFailed,
    mergeDetail: res ? res.detail : "finish seat returned nothing",
    worktreeRemoved: res ? res.worktreeRemoved !== false : false,
    ...extra,
  };
}

// A worktree whose work is pushed is 1.4 GB of dead weight: the PR carries every
// commit, so the stop paths that happen AFTER the push reclaim it and say in
// their recovery line how to get it back. The pre-push stops do the opposite and
// keep it — there the tree holds the only copy of the work.
async function reclaimWorktree(p, pr) {
  await agent(finishPrompt(p, pr, false, false), {
    label: `${p.branch}:cleanup`, phase: p.branch, schema: FINISH_SCHEMA, ...CHEAP,
  });
}

// Run everything CI would have run, locally, and record it on the PR. Serial by
// construction: it uses `test-int`/`smoke`, which bind fixed host ports and one
// shared compose project name across every checkout — and it takes the lock, so
// a second concurrent run cannot walk into it.
async function localCiVerify(r) {
  const p = r.pr_obj;
  const res = await agent(localCiPrompt(parsed, p, r.pr), {
    label: `${p.branch}:local-ci`,
    phase: p.branch,
    model: "opus",
    schema: LOCAL_CI_SCHEMA,
  });
  const restore = `Its worktree was reclaimed; restore it with \`git worktree add ${wtPath(p)} ${p.branch}\` then \`just worktree-init\`.`;
  if (res == null) {
    await reclaimWorktree(p, r.pr);
    return { ...r, reason: "local-ci-failed", recovery: `PR #${r.pr.number} is open and unverified — CI never ran and the local run produced nothing. ${restore}` };
  }
  if (res.status !== "GREEN") {
    await reclaimWorktree(p, r.pr);
    return { ...r, reason: "local-ci-red", localCi: res, recovery: `PR #${r.pr.number} failed local verification: ${res.detail} ${restore}` };
  }
  // The comment IS the evidence. Without it the PR carries no record that CI
  // was replaced by a local run, and the merge step is told to merge only when
  // that comment is present and green.
  if (!res.commented) {
    await reclaimWorktree(p, r.pr);
    return {
      ...r, reason: "local-ci-unrecorded", localCi: res,
      recovery: `PR #${r.pr.number} passed locally but the verification comment was not posted, so the PR carries no evidence. Post it by hand or re-run before merging. ${restore}`,
    };
  }

  // GREEN with skipped tiers is NOT the same claim as GREEN. The gate was already
  // run by the pre-push hook, so a "verification" where test-int, e2e, smoke and
  // schemathesis were all NOT_RUN adds exactly nothing — and this result feeds
  // the auto-merge decision. A PREEXISTING tier is different: it ran, it failed
  // the same way on main, and that is evidence rather than absence.
  // `checks` entries are "<name>: PASS|FAIL|NOT_RUN|PREEXISTING — <detail>" by
  // contract, so a NOT_RUN/FAIL entry whose name is in neither `notRun` nor
  // `preexisting` means the structured fields and the written record disagree.
  // That is not a verification, and the old file's decision not to regex prose
  // does not extend to a field whose shape this prompt dictates.
  const accounted = new Set([...(res.notRun || []), ...(res.preexisting || [])]);
  const contradictions = (res.checks || [])
    .filter((c) => /:\s*(NOT_RUN|FAIL)\b/i.test(c))
    .map((c) => c.split(":")[0].trim())
    .filter((name) => ![...accounted].some((a) => a.includes(name) || name.includes(a)));
  const skipped = [...new Set([...(res.notRun || []), ...contradictions])];
  if (contradictions.length) {
    log(
      `⚠️ ${p.branch}: the local verification reports ${contradictions.join(", ")} as NOT_RUN/FAIL in ` +
        `\`checks\` but not in \`notRun\`. Treating them as not run — it will NOT auto-merge.`,
    );
  }
  const mode = skipped.length ? "local-partial" : "local";
  if (skipped.length) {
    log(
      `⚠️ ${p.branch}: PR #${r.pr.number} verified locally but ${skipped.length} check(s) could not ` +
        `run (${skipped.join(", ")}). It will NOT auto-merge — a human decides whether that evidence ` +
        `is enough.`,
    );
  } else {
    log(`✅ ${p.branch}: PR #${r.pr.number} verified locally (CI never ran), recorded on the PR.`);
  }
  if (res.preexisting && res.preexisting.length) {
    log(`   ${p.branch}: ${res.preexisting.join(", ")} fail identically on main — not attributed to this PR.`);
  }
  const done = await finish(p, r.pr, r.verdict, {
    reviewLoops: r.reviewLoops, ciLoops: r.ciLoops, gateLoops: r.gateLoops,
    gatePreexisting: r.gatePreexisting, ciMode: mode,
  });
  return { ...done, localCi: res, notRun: skipped };
}

// ── Run the groups ───────────────────────────────────────────────────────────
// Within a group: non-Docker PRs concurrently (a review is Read/Grep plus
// targeted tests — no ports, no compose project, so the expensive gate is the
// parallelisable one), then Docker PRs strictly one at a time, then the deferred
// local-CI verifications. Between groups: nothing merges here — each PR's own
// finish seat merged it if the DAG was waiting — so the barrier only checks that
// what the next group depends on is demonstrably on main.

const done = [];
let halted = null;

for (let gi = 0; gi < groups.length && !halted; gi++) {
  const group = groups[gi];
  const concurrent = group.filter((p) => !p.needsDocker);
  const serial = group.filter((p) => p.needsDocker);
  log(
    `Group ${gi + 1}/${groups.length} — concurrent: ${concurrent.map((p) => p.branch).join(", ") || "(none)"}; ` +
      `serial (need Docker): ${serial.map((p) => p.branch).join(", ") || "(none)"}.`,
  );

  const results = [];
  if (concurrent.length) {
    const r = await parallel(concurrent.map((p) => () => buildPr(p)));
    results.push(...r.filter(Boolean));
  }
  for (const p of serial) {
    const r = await buildPr(p);
    if (r) results.push(r);
  }

  // Deferred local verification, one at a time — it needs the compose ports.
  for (let i = 0; i < results.length; i++) {
    if (results[i].reason === "needs-local-ci") results[i] = await localCiVerify(results[i]);
  }

  done.push(...results);

  // A later group whose prerequisite did not merge would branch off a main that
  // lacks what it builds on — stop rather than build on sand. `mergedNow` is
  // data each finish seat returned, and the next group's setup seat verifies it
  // independently against `git log origin/main`.
  const nextGroup = groups[gi + 1];
  if (nextGroup) {
    const mergedNow = done.filter((r) => r.merged).map((r) => r.branch);
    const missing = missingPrerequisites(nextGroup, mergedNow, planMerged);
    if (missing.length) {
      // Naming the stop matters: "needs these merged first: feat/a" reads as
      // "go merge feat/a" even when feat/a never got a pull request at all,
      // and the real recovery is then buried in `stopped`.
      const why = missing
        .map((b) => {
          const r = done.find((x) => x.branch === b);
          if (r && !r.ok) return `${b} (stopped: ${r.reason})`;
          if (r && r.mergeFailed) return `${b} (merge failed: ${r.mergeDetail})`;
          if (r) return `${b} (open, unmerged)`;
          return b;
        })
        .join(", ");
      halted = `group ${gi + 2} needs these merged first: ${why}`;
      log(`STOPPING: ${halted}`);
    }
  }
}

// ── Report ───────────────────────────────────────────────────────────────────

const green = done.filter((r) => r.ok);
const stopped = done.filter((r) => !r.ok);
const merged = green.filter((r) => r.merged);
const open = green.filter((r) => !r.merged);
log(
  `Done. Merged: ${merged.map((r) => `${r.branch}(#${r.pr.number})`).join(", ") || "none"}. ` +
    `Open for review: ${open.map((r) => `${r.branch}(#${r.pr.number})`).join(", ") || "none"}. ` +
    `Stopped: ${stopped.map((r) => `${r.branch}(${r.reason})`).join(", ") || "none"}.` +
    (halted ? ` HALTED: ${halted}` : ""),
);
const mergeFailures = green.filter((r) => r.mergeFailed);
if (mergeFailures.length) {
  log(
    `⚠️ ${mergeFailures.length} PR(s) the DAG waits on did NOT merge: ` +
      mergeFailures.map((r) => `${r.branch}(#${r.pr.number}) — ${r.mergeDetail}`).join("; "),
  );
}
const allShipped = parsed.prs.every(
  (p) => p.merged || done.some((r) => r.branch === p.branch && r.merged),
);
if (allShipped) log(`Every PR is merged — re-launch with { verifyFeature: true } for the feature-scoped verification.`);

return {
  feature: parsed.feature,
  planSha: parsed.planSha,
  merged: merged.map((r) => ({ branch: r.branch, title: r.title, pr: r.pr.number, url: r.pr.url })),
  open: open.map((r) => ({
    branch: r.branch, title: r.title, pr: r.pr.number, url: r.pr.url,
    reviewLoops: r.reviewLoops, ciLoops: r.ciLoops, gateLoops: r.gateLoops, ciMode: r.ciMode,
    localCi: r.localCi ? r.localCi.checks : undefined,
    notRun: r.notRun && r.notRun.length ? r.notRun : undefined,
    preexisting: r.localCi && r.localCi.preexisting && r.localCi.preexisting.length ? r.localCi.preexisting : undefined,
    // A PR the workflow tried and failed to merge must be distinguishable from a
    // leaf it never meant to merge.
    // Checks that were failing before this PR and still are: shipped knowingly,
    // named so the operator can see what the gate did not cover.
    gatePreexisting: r.gatePreexisting || undefined,
    mergeFailed: r.mergeFailed || undefined,
    mergeDetail: r.mergeFailed ? r.mergeDetail : undefined,
    worktreeKept: r.worktreeRemoved === false ? true : undefined,
  })),
  stopped: stopped.map((r) => ({
    branch: r.branch, reason: r.reason,
    gaps: r.verdict ? r.verdict.gaps : undefined,
    processNotes: r.verdict ? r.verdict.processNotes : undefined,
    // The per-criterion verdicts the expensive seat produced. Without them a
    // rejection reaches the operator as one prose paragraph.
    criteria: r.verdict && r.verdict.criteria && r.verdict.criteria.length ? r.verdict.criteria : undefined,
    rightThingBuilt: r.verdict ? r.verdict.rightThingBuilt : undefined,
    gateExit: r.gate ? r.gate.gateExit : undefined,
    recovery: r.recovery,
  })),
  halted,
  nextAction: halted
    ? `Resolve the stop, then re-launch: ${halted}`
    : mergeFailures.length
      ? `Merge ${mergeFailures.map((r) => `#${r.pr.number}`).join(", ")} by hand, or resolve what blocked it: ${mergeFailures.map((r) => r.mergeDetail).join("; ")}`
      : stopped.length
      ? "Resolve the hard stops first — each carries its own recovery line."
      : allShipped
        ? "Re-launch with { verifyFeature: true }."
        : open.length
          ? "Review and squash-merge the open PRs."
          : "Nothing to do.",
};
