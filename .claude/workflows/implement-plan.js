export const meta = {
  name: "implement-plan",
  description:
    "Build a feature plan's pull requests: each in its own worktree, developed TDD against its acceptance criteria and edge cases, gated by an independent arc-reviewer, then pushed as its own PR with CI driven green. PRs others depend on auto-merge so the next group can start; leaf PRs stay open for the operator.",
  // Only the statically-known group is declared. Per-PR groups are created at
  // runtime by their agents' `phase:` option — their titles are dynamic, and
  // declaring them here would render empty groups on a scoped run.
  phases: [{ title: "Parse", detail: "plan → PRs, cross-checked against gh + git" }],
};

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
  if (/\bverify\b/i.test(str)) out.verifyFeature = true;
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
// with NO PR opened. CI red → fix → re-review → push → re-watch, likewise. Both
// bounded: an unbounded loop burns tokens on something that needs a human.
const MAX_REVIEW_LOOPS = 2;
const MAX_CI_LOOPS = 2;

if (!PLAN) {
  log("No plan given. Pass { plan: '<slug>-plan.md' }. Aborting.");
  return { error: "no-plan" };
}

// ── Schemas ──────────────────────────────────────────────────────────────────

const PARSE_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["feature", "why", "openQuestions", "prs", "featureAcceptance"],
  properties: {
    feature: { type: "string" },
    // The plan's "Why" section, verbatim. Carried into every developer prompt:
    // it is the one thing that cannot be inferred from the codebase, and it is
    // what a developer reasons from when the plan runs out of instructions.
    why: { type: "string" },
    openQuestions: { type: "array", items: { type: "string" } },
    featureAcceptance: { type: "array", items: { type: "string" } },
    prs: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        required: [
          "title", "branch", "depends", "why", "delivers", "reuses",
          "owns", "needsDocker", "triggers", "decisions", "acceptance",
          "prExists", "merged",
        ],
        properties: {
          // Verbatim from the "### <title>" heading. This IS the PR title, and
          // squash-merge makes it the commit subject on main.
          title: { type: "string" },
          branch: { type: "string" },
          // Branch names this PR needs MERGED before it can start.
          depends: { type: "array", items: { type: "string" } },
          why: { type: "string" },
          delivers: { type: "string" },
          reuses: { type: "string" },
          owns: { type: "array", items: { type: "string" } },
          needsDocker: { type: "boolean" },
          triggers: { type: "array", items: { type: "string" } },
          // "<decision> | displaces <x> | lands in <site>" per row.
          decisions: { type: "array", items: { type: "string" } },
          // Each AC verbatim INCLUDING its level, test file and nested edge cases.
          acceptance: { type: "array", items: { type: "string" } },
          // Two DIFFERENT questions, and conflating them is a real bug: a PR that
          // is merely OPEN satisfies "don't rebuild this" but not "its dependents
          // may start". An open prerequisite means the next group would be cut
          // from a main that lacks it. Both derived from gh/git, never the plan.
          prExists: { type: "boolean" }, // a PR with this exact title, any state
          merged: { type: "boolean" }, // that PR is MERGED, or the subject is on main
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
    gaps: { type: "string" },
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
// anyway. That made the dirty-worktree hard stop unable to fire, and made
// `prPrompt`'s "this worktree was verified clean at setup" a false premise —
// which is exactly the reasoning that lets the commit agent stage without
// suspicion.
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
    // Prerequisite titles the agent confirmed are on origin/main.
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

const CI_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["status", "detail"],
  properties: {
    // NO_BUDGET: checks could not run because Actions minutes / spending limit
    // are exhausted. A distinct outcome from RED — nothing is wrong with the
    // code, and the answer is a local verification, not a fix.
    status: { type: "string", enum: ["GREEN", "RED", "NO_BUDGET", "UNKNOWN"] },
    failing: { type: "array", items: { type: "string" } },
    detail: { type: "string" },
  },
};

const LOCAL_CI_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["status", "checks", "commented"],
  properties: {
    status: { type: "string", enum: ["GREEN", "RED"] },
    // One entry per CI-equivalent check: "<name>: PASS|FAIL|NOT_RUN — <detail>".
    checks: { type: "array", items: { type: "string" } },
    commented: { type: "boolean" },
    detail: { type: "string" },
  },
};

const MERGE_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["merged", "detail"],
  properties: {
    merged: { type: "boolean" },
    sha: { type: "string" },
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
const TITLE_RE = new RegExp(`^(${TITLE_TYPES})(\\([^)]+\\))?: (?![A-Z])\\S.*[^.\\s]$`);

// A positional label tells a reader nothing and outlives the plan that gave it
// meaning — the title is the permanent commit subject on main and the changelog
// entry.
const POSITIONAL_RE = /\b(wp|pr|phase|slice|step|part|increment)[\s._-]?\d+\b/i;

// The title is pasted into a double-quoted shell command by an agent
// (`gh pr create --title "…"`), so a backtick or `$` in it is command
// substitution and the mangled result becomes the permanent subject on main.
const SHELL_UNSAFE_RE = /[`$"\\]/;

// Branch names are interpolated into `git worktree add` / `git push` the same way.
const BRANCH_RE = new RegExp(`^(${TITLE_TYPES})\\/[a-z0-9][a-z0-9._-]*$`);

const isMigration = (owned) => /alembic[\\/]|\bmigration/i.test(owned);

function badTitles(prs) {
  return prs.filter((p) => !TITLE_RE.test(p.title));
}
function unsafeTitles(prs) {
  return prs.filter((p) => SHELL_UNSAFE_RE.test(p.title));
}
function positionalOffenders(prs) {
  return prs.filter(
    (p) => POSITIONAL_RE.test(p.title) || POSITIONAL_RE.test(p.branch),
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
function guardrails(p) {
  const wt = wtPath(p);
  return `
WHERE YOU WORK: everything happens in the worktree \`${wt}\` on branch \`${p.branch}\`. Prefix every
command with \`cd ${wt} && …\` (or run git with \`-C ${wt}\`). NEVER edit, stage or run anything in the
main checkout — other PRs are being built there in parallel.

READ FIRST: this PR's section of ${PLAN}, the repo's CLAUDE.md, and any \`.claude/rules/*.md\` whose
\`paths:\` match the files you touch.

⚠️ THE PLAN IS NOT IN YOUR WORKTREE. It is an untracked working note in the MAIN checkout, absent
from every worktree. Read it at the path above WITHOUT prefixing \`cd ${wt}\` —
that path is resolved from the main checkout. Everything else you do is inside \`${wt}\`.

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
  \`uv run\` / \`bun run\` — a worktree shell can otherwise import the main checkout's code.
- The gate is \`just check\` (ruff · pyrefly · import-linter · backend+frontend unit tests ·
  production build · api-contract drift). It must end GREEN.${
    p.needsDocker
      ? `\n- This PR is marked "needs Docker": ALSO run \`just test-int\` and it must be green. You have
  exclusive use of the compose ports — no other PR is being built while you run.`
      : `\n- Do NOT run \`just test-int\`, \`just smoke\` or \`just up\`. They bind fixed host ports shared with
  other worktrees running right now, and this PR is not marked as needing them.`
  }${
    p.triggers && p.triggers.length && p.triggers[0] !== "none"
      ? `\n- Build steps this PR triggers — run them, VERIFY the output before anything consumes it, and
  commit the result: ${p.triggers.join("; ")}.`
      : ""
  }
- A model change ships with its Alembic migration in the same PR. A new setting goes in
  \`app/core/config.py\` AND \`.env.example\` (a test enforces it). A backend schema change means
  \`just api-sync\`, committed.
- Layering: imports point inward only (api | mcp → ingest → services → persistence → domain).
  \`app/domain/\` is pure. Endpoints and MCP tools stay thin; services hold the logic and commit the
  transaction; services raise \`AppError\` subclasses, never \`HTTPException\`.
- Reasoning goes where it binds. Each row below names a docstring, comment or test — put it THERE,
  not in the plan:${p.decisions.length ? "\n" + p.decisions.map((d) => `    · ${d}`).join("\n") : " (none recorded)"}
- Fix every problem you touch. No skips, no broken windows, never \`--no-verify\`.
- Do NOT commit, do NOT \`git add\`, do NOT push, do NOT modify ${PLAN}. A separate agent commits.
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
  return `Parse the feature plan ${PLAN} into a machine-readable pull-request list.

Read ${PLAN} in full. Then run BOTH yourself and use them — NOT the plan's checkboxes — to decide what
is already shipped, and whether it is merely OPEN or actually MERGED:
- \`gh pr list --state all --limit 60 --json number,title,state\`
- \`git fetch origin main\` then \`git log --oneline origin/main -40\` (ignore failure if offline)

Return the plan's H1 as \`feature\`, its entire "## Why" section as \`why\` (verbatim prose — this is
carried into every developer prompt and is the most important field here), every unresolved
**(confirm)** item under "Open questions" as \`openQuestions\` (empty array if none), and every
"- [ ] **AC-n** …" bullet under "Feature acceptance" as \`featureAcceptance\`, verbatim.

For each "### <title>" section under "## Pull requests":
- title — the heading text, VERBATIM. This becomes the PR title.
- branch, needsDocker — from the \`**Branch**\` and \`**Needs Docker**\` blockquote lines
  (needsDocker is TRUE unless the line starts with "no").
- depends — the branch names from \`**Depends**\`; empty array for "—".
- owns / triggers — entries from \`**Owns**\` and \`**Triggers**\`; empty array for "—" or "none".
- why — the "**Why this PR**" paragraph, verbatim.
- delivers / reuses — those paragraphs, verbatim.
- decisions — one string per row of the "Decisions landing in code" table, formatted
  "<decision> | displaces <alternative> | lands in <site>". Empty array if the table is absent.
- acceptance — every "- [ ] **AC-n** …" bullet in this PR, VERBATIM and COMPLETE, including its
  level, its test path, and every nested "- Edge: …" line underneath it. Do not summarise or drop
  edge cases; the developer builds from these and the reviewer judges against them.
- prExists — TRUE if a PR whose title EXACTLY equals this title exists in ANY state (open, merged,
  or closed). FALSE otherwise.
- merged — TRUE only if that PR's state is MERGED, or the exact subject already appears in
  \`git log origin/main\`. A PR that is merely OPEN is prExists=true and merged=FALSE.

These two are deliberately separate and you must not collapse them. An open PR means "do not build
this again"; only a merged one means "the PRs that depend on it may start", because a dependent is
cut from \`origin/main\` and an unmerged prerequisite is not there.

Return only real PRs from the plan. Do not invent, reorder or renumber anything.`;
}

function setupPrompt(p) {
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
   only if \`${wt}/.venv\` and \`${wt}/frontend/node_modules\` both already exist.

6. Confirm with \`git -C ${wt} rev-parse --abbrev-ref HEAD\` that HEAD is \`${p.branch}\`, and return
   it as \`headBranch\` whatever it turns out to be — do not report the expected value, report the
   observed one.
${
  (p.depends || []).length
    ? `
7. ⚠️ PREREQUISITE CHECK. This PR depends on ${p.depends.map((d) => `\`${d}\``).join(", ")}. Run
   \`git log origin/main --oneline -40\` and confirm each of these titles appears:
${p.depends.map((d) => `     - the PR whose branch is \`${d}\``).join("\n")}
   List the ones you found in \`prerequisitesOnMain\`. If any prerequisite is NOT on origin/main,
   return \`ok: false\` and say which — this worktree would be built against a main that lacks the
   work it extends.
`
    : ""
}
Return \`ok: true\` only when the worktree exists, HEAD is \`${p.branch}\`, \`dirty\` is empty${
    (p.depends || []).length ? ", and every prerequisite is on origin/main" : ""
  }.
Do not implement anything.`;
}

function implementPrompt(parsed, p, fixIssues, handoff, ci) {
  if (ci) {
    return `CI is RED on the PR "${p.title}". Fix it.

Failing checks:
${ci.failing && ci.failing.length ? ci.failing.map((f) => `- ${f}`).join("\n") : `- ${ci.detail}`}

Read the actual failure before changing anything: find the run with \`gh run list --branch ${p.branch}\`
and read \`gh run view <run-id> --log-failed\`. CI runs tiers \`just check\` deliberately omits —
integration against real Postgres (the only place \`alembic check\` runs), Playwright e2e, the
fullstack smoke suite, and schemathesis — so red here is usually a REAL defect your local gate could
not see. Treat it as one unless the log proves otherwise.

If schemathesis found it, fix it AND pin the case as a unit test; that is this repo's standing rule.
If \`alembic check\` found model/migration drift, the migration is missing or incomplete.

Fix the cause, re-run the relevant local gate, and leave the tree GREEN and uncommitted; a separate
agent commits and pushes.
${whyBlock(parsed, p)}${handoffBlock(handoff)}${guardrails(p)}${HANDOFF_INSTRUCTION}`;
  }
  if (fixIssues && fixIssues.length) {
    return `The independent review of "${p.title}" REJECTED these acceptance criteria. Fix ONLY these —
do not refactor beyond them:
${fixIssues.map((i) => `- ${i.ac} — responsible area: ${i.area}`).join("\n")}

For each, make the criterion genuinely true and prove it with a test that asserts that specific claim
at the right level, covering the edge cases the plan lists under it. Then re-run this PR's full gate —
your fix can break things beyond the flagged items, and nothing downstream re-runs the suites for you.
${whyBlock(parsed, p)}${handoffBlock(handoff)}${guardrails(p)}${HANDOFF_INSTRUCTION}`;
  }
  return `Implement the pull request "${p.title}" from ${PLAN}, END TO END, by yourself, using TDD.
${whyBlock(parsed, p)}
DELIVERS: ${p.delivers}

REUSES — extend these rather than growing a parallel implementation: ${p.reuses}

ACCEPTANCE CRITERIA. Each states input, action and the exact expected result, and lists the edge
cases its tests must also cover. An independent reviewer with no write tools will judge your diff
against exactly these, per criterion and per edge case:
${acBlock(p)}
${guardrails(p)}${HANDOFF_INSTRUCTION}`;
}

function reviewPrompt(parsed, p, rejected, handoff) {
  const wt = wtPath(p);
  const where = `The work is in the worktree \`${wt}\` on branch \`${p.branch}\`. Inspect it with
\`git -C ${wt} diff origin/main...HEAD\` (\`--stat\` first). Run any targeted test with \`cd ${wt} && …\`,
never in the main checkout. Plan: ${PLAN} — this PR's section, its acceptance criteria AND its
"Decisions landing in code" table, whose landing sites you verify actually exist in the code.
The plan is untracked and therefore absent from the worktree: read it at that path from the MAIN
checkout, without a \`cd\`.`;
  if (rejected && rejected.length) {
    return `TARGETED re-review of "${p.title}" after a fix. Validate ONLY these previously-rejected
criteria; do not re-open ones that already passed:
${rejected.map((i) => `- ${i.ac} (${i.area})`).join("\n")}

${where}${handoffBlock(handoff)}
Confirm each is now genuinely fulfilled — including the edge cases listed under it — citing the test
that exercises it. Run only the targeted tests for these criteria; the fix agent already re-ran the
gate. Return the verdict in the structured format.`;
  }
  return `Review the pull request "${p.title}".
${whyBlock(parsed, p)}
Judge the delivered diff against these acceptance criteria, one at a time. Each lists the edge cases
its tests must cover: an AC whose happy path passes but whose listed edges have no test is PARTIAL,
not FULFILLED. Record what you found per criterion in \`edgeCasesCovered\`.
${acBlock(p)}

${where}${handoffBlock(handoff)}
\`just check\` is already GREEN on this branch — do not re-run lint, type-check, unit tests or the
build. Judge whether the diff is what the criteria asked for, and run only the targeted tests that
prove a specific criterion. Return the verdict in the structured format.`;
}

function prPrompt(p) {
  const wt = wtPath(p);
  return `"${p.title}" passed independent review. Commit it, push it, and open its PR.

All git work happens with \`-C ${wt}\` or after \`cd ${wt}\`.

1. Stage explicitly by path. Run \`git -C ${wt} status --porcelain -uall\` and stage every path that
   belongs to this PR with \`git -C ${wt} add -- <path> …\`. NEVER \`git add -A\`, \`.\` or \`-u\`.
   Always exclude anything under \`.claude/agent-memory/\` at any depth, and \`CHANGELOG.md\`.
2. Commit. Subject in Conventional Commits, scoped by subsystem; body wrapped at ~76 columns.
   NEVER \`--no-verify\` — pre-commit hooks may rewrite files (api-schema-sync regenerates
   \`frontend/generated/api/\`); if a hook aborts the commit, re-derive the path list the same way,
   \`git add --\` exactly those, and commit again.
3. Push: \`git -C ${wt} push -u origin ${p.branch}\`. This runs the pre-push hooks (pyrefly,
   import-linter, backend unit tests, frontend type-check, frontend unit tests) and takes minutes —
   give it a generous timeout and do not bypass it. If it fails on something unrelated, report it.
4. Open the PR with EXACTLY this title — it is a required CI check that it be a lowercase-start,
   no-trailing-period Conventional Commit, and squash-merge makes it the commit subject on main:
     ${p.title}
   Write the body to a file and pass \`--body-file\`; it is full of backticks and brackets:
     \`gh pr create --base main --title "${p.title}" --body-file <path>\`
   Fill in \`.github/pull_request_template.md\` and tick only what you actually verified.
   THE BODY BECOMES THE COMMIT BODY ON MAIN, so lead with WHY this change exists — the problem it
   solves, not a list of files. Write for a reader outside the branch: one line per paragraph, NO
   hard wrapping (GitHub renders PR bodies with hard line breaks on, so a wrapped paragraph becomes a
   ragged column; GitHub re-wraps it itself for the squash commit). Keep it flat: no nested lists, no
   tables, no \`Key: value\` trailers.
5. Before reporting success, run \`git -C ${wt} show --stat --oneline HEAD\` and read the file list.
   Every path must be work this PR actually did. This worktree was verified clean at setup, so
   anything you do not recognise is a bug — report it as a FAILURE rather than opening the PR.
6. Report the PR number, URL and commit SHA, and the exact list of files committed.

Do NOT merge and do NOT enable auto-merge — a later step decides that.`;
}

function ciPrompt(p, pr) {
  return `Watch CI on PR #${pr.number} (${pr.url}) and report the outcome.

Run \`gh pr checks ${pr.number} --watch --interval 30\`. It can exceed a single command timeout — if
the command times out or still reports pending checks, run it again, up to 6 times total.

Classify the result:
- All checks passed → GREEN.
- A check failed → RED. List the failing check names in \`failing\`, and for each read the real
  failure (\`gh run view <run-id> --log-failed\`) and put a one-line diagnosis in \`detail\`. A fix
  agent works from this, so name the test or the error, not "CI failed".
- \`pr-title\` failing → RED, and say explicitly that it needs a retitle, not a code fix.
- **Checks could not run because the Actions budget is exhausted → NO_BUDGET.** Look for: zero checks
  registered after several minutes; a run whose jobs never started; or an error mentioning spending
  limit, billing, quota, or minutes exhausted (\`gh run list --branch ${p.branch}\` and
  \`gh run view <run-id>\` show it). Distinguish this carefully from RED — nothing is wrong with the
  code, and misreporting it sends a fix agent to hunt a defect that does not exist. Put the exact
  message you saw in \`detail\`.
- Anything else, or you could not tell → UNKNOWN, with what you saw.

Do not change any code, do not push, do not merge.`;
}

function localCiPrompt(p, pr) {
  const wt = wtPath(p);
  return `CI could not run on PR #${pr.number} ("${p.title}") because the Actions budget is exhausted.
Verify the PR LOCALLY against every check CI would have run, then record it on the PR.

You have exclusive use of Docker and the fixed compose ports right now — nothing else is running.
Work in \`${wt}\`.

Run each of these and record the result. Run them in this order; a later one is worthless if an
earlier one is red:
1. \`cd ${wt} && just check\`      — ruff, pyrefly, import-linter, backend+frontend unit tests,
                                     production build, api-contract drift
2. \`cd ${wt} && just test-int\`    — integration on real Postgres; the ONLY place \`alembic check\`
                                     runs, so this is what catches model/migration drift
3. \`cd ${wt} && just e2e\`         — Playwright, UI-only
4. \`cd ${wt} && just smoke\`       — full Docker stack. It needs \`E2E_PASSWORD\` to match the bcrypt
                                     hash in \`.env\`; if you do not have it, the login step fails and
                                     you must record smoke as NOT_RUN with that reason — do NOT
                                     invent a password and do NOT claim it passed.
5. schemathesis — only if this PR changed an API route or schema. Follow the reproduction recipe in
   CLAUDE.md (run the API against the compose test database, log in for a cookie, then
   \`uvx schemathesis run …\` from \`backend/\`). If you cannot obtain a session cookie, record NOT_RUN
   with the reason.

Then post ONE comment on the PR with \`gh pr comment ${pr.number} --body-file <path>\`. It must:
- state plainly that CI did not run because the GitHub Actions budget was exhausted, and that these
  checks were therefore run locally on the branch;
- name the commit SHA they were run against;
- list every check with PASS / FAIL / NOT_RUN and, for NOT_RUN, the reason it could not be run.

BE HONEST. This comment stands in for CI on a public pull request, and a comment claiming coverage
that was not achieved is worse than no comment at all. Never mark something PASS that you did not
observe pass. Never omit a check you skipped.

Return \`status\` GREEN only if every check that RAN passed; RED if any failed. Report each check as
"<name>: PASS|FAIL|NOT_RUN — <detail>" in \`checks\`, and whether the comment was posted.

Do not change any code and do not merge.`;
}

function mergePrompt(p, pr) {
  return `PR #${pr.number} ("${p.title}") is review-approved and CI-verified, and other pull requests in
this plan DEPEND on it being merged before they can start. Merge it.

\`main\` is protected: squash-only, PR required. Use:
  \`gh pr merge ${pr.number} --squash --auto\`
If auto-merge is not enabled for this repository the command errors — in that case, confirm the
checks are currently passing (\`gh pr checks ${pr.number}\`) and merge directly:
  \`gh pr merge ${pr.number} --squash\`
If CI did not run at all (the budget-exhausted case, recorded in a comment on the PR), \`--auto\` will
never fire — merge directly, and only if that local-verification comment is present and green.

Then poll \`gh pr view ${pr.number} --json state,mergeStateStatus,mergedAt\` until \`state\` is
\`MERGED\`, up to 10 times. Report \`merged: true\` with the resulting SHA only when you have SEEN it
merged — never optimistically.

If it will not merge (conflicts, a failing required check, a blocked branch), report \`merged: false\`
with the exact reason. Do NOT force anything and do NOT change the branch protection.`;
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
  model: "sonnet",
});

if (!parsed || !parsed.prs || parsed.prs.length === 0) {
  log("No pull requests parsed from the plan — aborting.");
  return { error: "parse-failed", parsed };
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
    `HARD STOP: ${bad.length} title(s) would fail the required \`pr-title\` check — the subject must ` +
      `not start with an uppercase letter and must not end with a period:\n` +
      bad.map((p) => `  - "${p.title}"`).join("\n"),
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
that reached the API but not MCP, a decision that landed in two places with two meanings. Run the
suites you need, including \`just test-int\` — nothing else is running now.

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
  return { feature: parsed.feature, featureVerdict: report };
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
const mergedBranches = parsed.prs.filter((p) => p.merged).map((p) => p.branch);
const layered = groupsOf(pending, mergedBranches);
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

// Who is depended on: those PRs auto-merge when green, because the whole DAG
// waits on them. Leaves stay open for the operator to review at leisure.
const dependedOn = new Set(parsed.prs.flatMap((p) => p.depends || []));

log(
  `${parsed.feature} — ${pending.length} PR(s) to build in ${groups.length} group(s):\n` +
    groups
      .map(
        (g, i) =>
          `  ${i + 1}. ${g.map((p) => p.branch + (dependedOn.has(p.branch) ? " (auto-merges)" : "")).join(" ∥ ")}`,
      )
      .join("\n"),
);

// ── The per-PR pipeline ──────────────────────────────────────────────────────
// implement → independent review (bounded fix loop) → commit/push/PR → CI green.
// Every seat is a FRESH agent: the reviewer never wrote the code it judges, and
// the fixer is never the implementer either. The reviewer additionally has no
// write tools (see .claude/agents/arc-reviewer.md), so it structurally cannot
// repair what it finds — independence enforced by the toolset, not by a prompt.

async function buildPr(p) {
  // Every agent carries `phase:` explicitly rather than calling the global
  // phase() — PRs run concurrently, and the global is shared mutable state.
  const title = p.branch;
  const dev = { phase: title, model: "opus", schema: HANDOFF_SCHEMA };
  const rev = { phase: title, agentType: "arc-reviewer", schema: REVIEW_SCHEMA };
  const fail = (reason, extra) => {
    log(`HARD STOP — ${p.branch}: ${reason}`);
    return { branch: p.branch, title: p.title, ok: false, reason, ...extra };
  };

  const setup = await agent(setupPrompt(p), {
    label: `${title}:setup`, phase: title, model: "sonnet", schema: SETUP_SCHEMA,
  });
  if (setup == null) return fail("worktree-setup-failed");
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

  // A dead implement agent must never advance to review against an empty diff.
  let handoff = await agent(implementPrompt(parsed, p, null, null, null), { ...dev, label: `${title}:implement` });
  if (handoff == null) {
    log(`${p.branch}: implement agent returned nothing — retrying once.`);
    handoff = await agent(
      `NOTE: a previous attempt died partway through, so \`${wtPath(p)}\` may already hold partial work
(tests and/or implementation). Inspect what exists before writing: keep what is correct, replace what
is not, and do not be derailed if some tests already exist or already pass. Reach the same end state
as a clean run.

` + implementPrompt(parsed, p, null, null, null),
      { ...dev, label: `${title}:implement-retry` },
    );
  }
  if (handoff == null) {
    return fail("implement-failed", {
      recovery: `Inspect ${wtPath(p)} — the dead attempt's partial work is still there. Finish or reset it before re-launching.`,
    });
  }

  let verdict = await agent(reviewPrompt(parsed, p, null, handoff), { ...rev, label: `${title}:review` });
  let loops = 0;
  while (verdict && verdict.status === "REJECTED" && loops < MAX_REVIEW_LOOPS) {
    loops++;
    const rejected = verdict.issues;
    log(`${p.branch} REJECTED (fix ${loops}/${MAX_REVIEW_LOOPS}): ${verdict.gaps}`);
    handoff =
      (await agent(implementPrompt(parsed, p, rejected, handoff, null), { ...dev, label: `${title}:fix${loops}` })) ||
      handoff;
    verdict = await agent(reviewPrompt(parsed, p, rejected, handoff), { ...rev, label: `${title}:review${loops}` });
  }
  if (!verdict || verdict.status !== "APPROVED") {
    // No PR is opened. Nothing reaches the remote that has not passed an
    // independent check against its own acceptance criteria.
    return fail(`review rejected after ${loops} fix loop(s) — no PR opened`, {
      verdict,
      recovery: `The work is in ${wtPath(p)}, uncommitted. Read the gaps, decide by hand, then re-launch with { onlyBranch: "${p.branch}" }.`,
    });
  }

  const pr = await agent(prPrompt(p), { label: `${title}:pr`, phase: title, model: "sonnet", schema: PR_SCHEMA });
  if (pr == null || !pr.ok || !pr.number) {
    return fail("pr-failed", {
      verdict,
      detail: pr ? pr.detail : "agent returned nothing",
      recovery: `Review-approved but not on the remote. Check \`git -C ${wtPath(p)} status\` and \`gh pr list\` before re-launching.`,
    });
  }
  log(`${p.branch}: PR #${pr.number} opened — ${pr.url}`);

  let ci = await agent(ciPrompt(p, pr), { label: `${title}:ci`, phase: title, model: "sonnet", schema: CI_SCHEMA });
  let ciLoops = 0;
  while (ci && ci.status === "RED" && ciLoops < MAX_CI_LOOPS) {
    ciLoops++;
    log(`${p.branch} CI RED (fix ${ciLoops}/${MAX_CI_LOOPS}): ${ci.detail}`);
    handoff =
      (await agent(implementPrompt(parsed, p, null, handoff, ci), { ...dev, label: `${title}:ci-fix${ciLoops}` })) ||
      handoff;
    // A CI fix changes behaviour, so it is re-reviewed before it goes back up:
    // no code reaches the PR without an independent pass over the criteria.
    const recheck = await agent(reviewPrompt(parsed, p, null, handoff), { ...rev, label: `${title}:review-ci${ciLoops}` });
    // A DEAD re-review is not an approval. Reading `recheck && …` let a null fall
    // through to the push below, which contradicted the guarantee this step
    // exists for — and was inconsistent with the initial review, where a null
    // verdict is a hard stop.
    if (!recheck || recheck.status !== "APPROVED") {
      return fail(
        recheck ? "CI fix broke the acceptance criteria" : "re-review agent died after a CI fix",
        {
          verdict: recheck, pr,
          recovery: `PR #${pr.number} is open and its latest work is unreviewed — nothing was pushed. Decide by hand.`,
        },
      );
    }
    const repush = await agent(
      `Commit and push the CI fix for "${p.title}" to the existing PR #${pr.number}, from \`${wtPath(p)}\`.
Stage explicitly by path (never \`git add -A\`/\`.\`/\`-u\`), exclude \`.claude/agent-memory/\` and
\`CHANGELOG.md\`, commit with a Conventional Commit subject, then \`git -C ${wtPath(p)} push\`. The
pre-push hooks take minutes — generous timeout, never \`--no-verify\`. If the failing check was
\`pr-title\`, fix it with \`gh pr edit ${pr.number} --title …\`, keeping it a lowercase-start
Conventional Commit with no trailing period. Report the pushed SHA.`,
      { label: `${title}:ci-push${ciLoops}`, phase: title, model: "sonnet", schema: PR_SCHEMA },
    );
    if (repush == null || !repush.ok) return fail("ci-push-failed", { pr, verdict });
    ci = await agent(ciPrompt(p, pr), { label: `${title}:ci-recheck${ciLoops}`, phase: title, model: "sonnet", schema: CI_SCHEMA });
  }

  const status = ci ? ci.status : "UNKNOWN";
  // Budget exhausted is not a defect. It needs a local run of everything CI would
  // have done — which is Docker-bound, so it is deferred to the serial pass
  // rather than run here inside a concurrent branch.
  if (status === "NO_BUDGET") {
    log(`${p.branch}: CI did not run — Actions budget exhausted. Deferring local verification.`);
    return { branch: p.branch, title: p.title, ok: false, reason: "needs-local-ci", pr, verdict, ci, pr_obj: p };
  }
  if (status !== "GREEN") {
    return fail(`CI ${status} after ${ciLoops} fix loop(s)`, {
      pr, verdict, ci,
      recovery: `PR #${pr.number} is open with ${status.toLowerCase()} CI. It needs a human decision before merge.`,
    });
  }
  log(`✅ ${p.branch}: PR #${pr.number} green.`);
  await removeWorktree(p, title);
  return { branch: p.branch, title: p.title, ok: true, pr, verdict, reviewLoops: loops, ciLoops, ciMode: "github" };
}

// A finished worktree holds ~1.4 GB of installed dependencies (.venv +
// node_modules) and its work is already on the remote, so it is pure dead
// weight. Removed ONLY on the success path: every hard stop leaves the worktree
// standing, because the recovery lines point at it and an uncommitted rejected
// implementation exists nowhere else. The branch is never deleted here — the PR
// is still open, and `/clean-gone` reaps branches after they merge.
async function removeWorktree(p, title) {
  const wt = wtPath(p);
  await agent(
    `The PR for "${p.title}" is open and its work is pushed, so the worktree \`${wt}\` is no longer
needed. Remove it and free the ~1.4 GB of installed dependencies it holds.

1. Confirm the work is safely on the remote: \`git ls-remote --heads origin ${p.branch}\` must return a
   ref, and \`git -C ${wt} status --porcelain -uall\` must be empty. If EITHER check fails, STOP and
   report it — do not remove anything. An uncommitted change here exists nowhere else.
2. \`git worktree remove ${wt}\` from the main checkout. Do NOT pass --force: if git refuses because
   the tree is dirty, that is step 1's guarantee failing and the worktree must be kept.
3. Do NOT delete the branch \`${p.branch}\` — its PR is still open.
4. Report what was removed, or why it was kept.`,
    { label: `${title}:cleanup`, phase: title, model: "sonnet" },
  );
}

// Run everything CI would have run, locally, and record it on the PR. Serial by
// construction: it uses `test-int`/`smoke`, which bind fixed host ports and one
// shared compose project name across every checkout.
async function localCiVerify(r) {
  const p = r.pr_obj;
  const title = p.branch;
  const res = await agent(localCiPrompt(p, r.pr), {
    label: `${title}:local-ci`,
    phase: title,
    model: "opus",
    schema: LOCAL_CI_SCHEMA,
  });
  if (res == null) {
    return { ...r, reason: "local-ci-failed", recovery: `PR #${r.pr.number} is open and unverified — CI had no budget and the local run produced nothing.` };
  }
  if (res.status !== "GREEN") {
    return { ...r, reason: "local-ci-red", localCi: res, recovery: `PR #${r.pr.number} failed local verification: ${res.detail}` };
  }
  if (!res.commented) {
    log(`⚠️ ${p.branch}: local verification passed but the PR comment was not posted — the record is missing.`);
  }
  log(`✅ ${p.branch}: PR #${r.pr.number} verified locally (CI budget exhausted), recorded on the PR.`);
  await removeWorktree(p, title);
  return { branch: p.branch, title: p.title, ok: true, pr: r.pr, verdict: r.verdict, localCi: res, ciMode: "local" };
}

// ── Run the groups ───────────────────────────────────────────────────────────
// Within a group: non-Docker PRs concurrently (a review is Read/Grep plus
// targeted tests — no ports, no compose project, so the expensive gate is the
// parallelisable one), then Docker PRs strictly one at a time, then the deferred
// local-CI verifications. Between groups: merge the depended-on PRs and wait, so
// the next group cuts from a main that actually contains its prerequisites.

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

  // Merge the prerequisites so the next group can branch off them. A leaf PR is
  // never merged here: it exists to be read by a human.
  const toMerge = results.filter((r) => r.ok && dependedOn.has(r.branch));
  if (AUTO_MERGE && toMerge.length && gi < groups.length - 1) {
    log(`Merging ${toMerge.length} prerequisite PR(s) so group ${gi + 2} can start.`);
    const merges = await parallel(
      toMerge.map((r) => () =>
        agent(mergePrompt(byBranch.get(r.branch), r.pr), {
          label: `${r.branch}:merge`,
          phase: r.branch,
          model: "sonnet",
          schema: MERGE_SCHEMA,
        }).then((m) => ({ r, m })),
      ),
    );
    for (const entry of merges.filter(Boolean)) {
      entry.r.merged = !!(entry.m && entry.m.merged);
      entry.r.mergeDetail = entry.m ? entry.m.detail : "merge agent returned nothing";
    }
  }

  // A later group whose prerequisite did not merge would branch off a main that
  // lacks what it builds on — stop rather than build on sand.
  const nextGroup = groups[gi + 1];
  if (nextGroup) {
    const missing = nextGroup.flatMap((p) =>
      (p.depends || []).filter((d) => {
        const r = done.find((x) => x.branch === d);
        return r ? !r.merged : !byBranch.get(d)?.merged;
      }),
    );
    if (missing.length) {
      halted = `group ${gi + 2} needs these merged first: ${[...new Set(missing)].join(", ")}`;
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
    `Stopped: ${stopped.map((r) => `${r.branch}(${r.reason})`).join(", ") || "none"}.`,
);
const allShipped = parsed.prs.every(
  (p) => p.merged || done.some((r) => r.branch === p.branch && r.merged),
);
if (allShipped) log(`Every PR is merged — re-launch with { verifyFeature: true } for the feature-scoped verification.`);

return {
  feature: parsed.feature,
  merged: merged.map((r) => ({ branch: r.branch, title: r.title, pr: r.pr.number, url: r.pr.url })),
  open: open.map((r) => ({
    branch: r.branch, title: r.title, pr: r.pr.number, url: r.pr.url,
    reviewLoops: r.reviewLoops, ciLoops: r.ciLoops, ciMode: r.ciMode,
    localCi: r.localCi ? r.localCi.checks : undefined,
  })),
  stopped: stopped.map((r) => ({
    branch: r.branch, reason: r.reason,
    gaps: r.verdict ? r.verdict.gaps : undefined,
    recovery: r.recovery,
  })),
  halted,
  nextAction: halted
    ? `Resolve the stop, then re-launch: ${halted}`
    : allShipped
      ? "Re-launch with { verifyFeature: true }."
      : open.length
        ? "Review and squash-merge the open PRs."
        : "Resolve the hard stops first.",
};
