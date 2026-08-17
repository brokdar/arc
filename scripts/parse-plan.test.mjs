#!/usr/bin/env node
// Unit tests for scripts/parse-plan.mjs.
//
// WHY THIS EXISTS. The parser replaced an agent, and that trade is only worth
// making if the parse is provably right: an agent that drops an edge case
// produces a review that cannot see it, and neither the developer nor the
// reviewer has anything to compare against. These cases pin the shape
// `plan-template.md` promises — including the two failure modes that are silent
// rather than loud: an acceptance criterion whose nested `- Edge:` lines are
// lost, and a `**Depends**` line whose em-dash is read as a branch name.
//
// The fixture below is a deliberately awkward but legal plan. Run:
//   node scripts/parse-plan.test.mjs

import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  annotate,
  blockquoteFields,
  labelled,
  listField,
  parseAcceptance,
  parseDecisions,
  parsePlan,
  section,
} from "./parse-plan.mjs";

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

const PLAN = `# The coach reads back what it wrote

> **Source**: issue #45
> **Base**: main (\`895afb9\`)

## Why

\`AgentNoteService\` has a read method and the MCP server never wired it, so a coaching agent cannot
see its own past evaluations. A note is append-only, so a coach that does not see what it already
said will eventually contradict itself under its own \`model_id\`.

## Done means

- The coach can read its own notes back.

## What already exists

| Existing thing | Where | Why it matters here |
| --- | --- | --- |
| \`AgentNoteService.list\` | \`app/services/notes.py\` | already returns the rows |

## Open questions

None. Both readings are resolved in the decisions tables below.

---

## Pull requests

### feat(mcp): the coach can read back what it wrote

> **Branch**: \`feat/mcp-agent-notes-read-path\`
> **Depends**: —
> **Owns**: \`backend/app/mcp/views.py\`, \`backend/app/mcp/tools.py\`
> **Needs Docker**: no
> **Triggers**: —

**Why this PR**: after it merges a coach opening a week review sees last week's written opinions
in the same call, instead of starting blind.

**Delivers**: an \`agent_notes\` block on every MCP read path that already renders the thing a note
can be filed under.

**Reuses**: \`AgentNoteService.list()\` and \`views.note()\`, which already render one note.

**Decisions landing in code**

| Decision | Displaces | Lands in |
| --- | --- | --- |
| \`null\` for a non-Monday window | Returning \`[]\` | \`views.plan_week\` docstring + AC-2 |
| One shape in every context | Two shapes | \`views.note\` docstring |

**Acceptance**

- [ ] **AC-1** After two \`write_session_evaluation\` calls, \`get_session_detail\` returns both notes
      oldest first, each carrying \`session_id\`.
      — *unit*, \`backend/tests/unit/test_mcp_tools.py\`
      - Edge: no notes at all — the key is present and \`[]\`, never absent
      - Edge: a note written by \`annotate\` targeting the same session — both appear,
        and the \`model_id\` of each is its own
- [ ] **AC-2** \`get_plan_week\` with a \`start\` that is not a Monday returns \`agent_notes: null\`.
      — *unit*, \`backend/tests/unit/test_mcp_tools.py\`
      - Edge: a Monday with no notes — \`[]\`, not \`null\`

### feat(mcp): the week strip carries its own ids

> **Branch**: \`feat/mcp-week-ids\`
> **Depends**: \`feat/mcp-agent-notes-read-path\`
> **Owns**: —
> **Needs Docker**: yes — migration chain, so \`just test-int\` before push
> **Triggers**: \`just api-sync\`

**Why this PR**: the week read counts sessions it will not name.

**Delivers**: ids on the week strip.

**Reuses**: \`_completed_sessions()\`.

**Acceptance**

- [ ] **AC-3** The week payload names every session it counted. — *unit*, \`test_plan.py\`

---

## Concurrency map

**Runs together**: nothing — the second depends on the first.

---

## Feature acceptance

- [ ] **AC-4** One \`get_coaching_context\` call shows the notes and the ids together.
      — *verified against \`main\`*
`;

// ── sections ─────────────────────────────────────────────────────────────────
group("sections");
{
  const why = section(PLAN, "Why");
  ok("Why is captured whole", why.startsWith("`AgentNoteService` has a read method") && why.includes("model_id"));
  ok("Why stops at the next heading", !why.includes("Done means"));
  ok("a section's trailing rule is dropped", !section(PLAN, "Open questions").includes("---"));
  ok("a missing section is null", section(PLAN, "Nonexistent") === null);
  ok("Pull requests holds both PRs", (section(PLAN, "Pull requests").match(/^### /gm) || []).length === 2);
  ok(
    "Pull requests stops before Concurrency map",
    !section(PLAN, "Pull requests").includes("Runs together"),
  );
}

// ── acceptance criteria ──────────────────────────────────────────────────────
group("acceptance criteria — the edge cases must survive");
{
  const acs = parseAcceptance(section(PLAN, "Feature acceptance"));
  ok("feature acceptance parses", acs.length === 1 && acs[0].includes("AC-4"));
  ok("its level line survives", acs[0].includes("*verified against `main`*"));

  const prs = parsePlan(PLAN).prs;
  const first = prs[0].acceptance;
  ok("both criteria found", first.length === 2, JSON.stringify(first, null, 1));
  ok("AC-1 keeps its continuation line", first[0].includes("oldest first, each carrying `session_id`"));
  ok("AC-1 keeps its level and test path", first[0].includes("*unit*, `backend/tests/unit/test_mcp_tools.py`"));
  ok("AC-1 keeps BOTH edges", (first[0].match(/- Edge:/g) || []).length === 2, first[0]);
  ok("a multi-line edge keeps its second line", first[0].includes("and the `model_id` of each is its own"));
  ok("the template's left margin is normalised away", /\n- Edge: no notes at all/.test(first[0]), JSON.stringify(first[0]));
  ok("an edge's own continuation stays indented under it", /\n {2}and the `model_id`/.test(first[0]), JSON.stringify(first[0]));
  ok("AC-2 does not swallow AC-1's edges", (first[1].match(/- Edge:/g) || []).length === 1);
  ok("a single-line AC parses", prs[1].acceptance.length === 1 && prs[1].acceptance[0].includes("AC-3"));
  ok("no acceptance block yields none", parseAcceptance("").length === 0);
  ok(
    "a checked box still parses (the reviewer's report ticks them)",
    parseAcceptance("- [x] **AC-9** done — *unit*, `t.py`").length === 1,
  );
}

// ── blockquote fields ────────────────────────────────────────────────────────
group("blockquote fields");
{
  const f = blockquoteFields("> **Branch**: `feat/x`\n> **Depends**: —\n\nprose **Delivers**: no");
  ok("Branch is read", f.Branch === "`feat/x`");
  ok("Depends is read as the em-dash", f.Depends === "—");
  ok("prose outside the blockquote is ignored", f.Delivers === undefined);

  ok("em-dash is an empty list", listField("—").length === 0);
  ok("'none' is an empty list", listField("none").length === 0);
  ok("undefined is an empty list", listField(undefined).length === 0);
  ok("backticks are stripped", listField("`feat/a`, `feat/b`").join() === "feat/a,feat/b");
  ok("semicolons separate too", listField("`just api-sync`; `just fixtures`").length === 2);
}

// ── labelled paragraphs ──────────────────────────────────────────────────────
group("labelled paragraphs");
{
  const body = section(PLAN, "Pull requests").split(/^### /m)[1];
  ok("Why this PR joins its wrapped lines", labelled(body, "Why this PR").endsWith("starting blind."));
  ok("Delivers stops at the blank line", !labelled(body, "Delivers").includes("Reuses"));
  ok("a missing label is null", labelled(body, "Nope") === null);
}

// ── decisions table ──────────────────────────────────────────────────────────
group("decisions table");
{
  const body = section(PLAN, "Pull requests").split(/^### /m)[1];
  const d = parseDecisions(body);
  ok("both rows parse", d.length === 2, JSON.stringify(d, null, 1));
  ok("the row reads as the prompt renders it", d[0] === "`null` for a non-Monday window | displaces Returning `[]` | lands in `views.plan_week` docstring + AC-2");
  ok("the header row is skipped", !d.some((r) => /^Decision \|/.test(r)));
  ok("the separator row is skipped", !d.some((r) => /^-{3}/.test(r)));
  ok("no table yields none", parseDecisions("**Acceptance**\n- [ ] **AC-1** x").length === 0);
  ok(
    "the 'What already exists' table is not mistaken for one",
    parsePlan(PLAN).prs[1].decisions.length === 0,
  );
}

// ── whole-plan shape ─────────────────────────────────────────────────────────
group("whole-plan shape");
{
  const p = parsePlan(PLAN);
  ok("no defects in a legal plan", p.problems.length === 0, JSON.stringify(p.problems));
  ok("feature is the H1", p.feature === "The coach reads back what it wrote");
  ok("the Source line is carried for issue linking", p.source === "issue #45", JSON.stringify(p.source));
  ok(
    "a plan with no Source line yields an empty string, not undefined",
    parsePlan(PLAN.replace("> **Source**: issue #45\n", "")).source === "",
  );
  ok(
    "a PR's own blockquote is not mistaken for the plan's Source",
    parsePlan(PLAN.replace("> **Source**: issue #45", "> **Base**: main")).source === "",
  );
  ok("two PRs", p.prs.length === 2);
  ok("titles are verbatim", p.prs[0].title === "feat(mcp): the coach can read back what it wrote");
  ok("branches are un-ticked", p.prs[0].branch === "feat/mcp-agent-notes-read-path");
  ok("no dependency for the first", p.prs[0].depends.length === 0);
  ok("the second depends on the first", p.prs[1].depends.join() === "feat/mcp-agent-notes-read-path");
  ok("owns is a list", p.prs[0].owns.length === 2 && p.prs[0].owns[0] === "backend/app/mcp/views.py");
  ok("'no' means no Docker", p.prs[0].needsDocker === false);
  ok("'yes — …' means Docker", p.prs[1].needsDocker === true);
  ok("triggers parse", p.prs[1].triggers.join() === "just api-sync");
  ok("no triggers is empty", p.prs[0].triggers.length === 0);
  ok("no open questions when the section says None", p.openQuestions.length === 0);
  ok("feature acceptance is separate from per-PR", p.featureAcceptance.length === 1);
  ok(
    "a per-PR AC is not leaked into featureAcceptance",
    !p.featureAcceptance[0].includes("AC-1"),
  );
}

// ── defects are named, not guessed at ────────────────────────────────────────
group("plan defects — reported with the PR and the line named");
{
  const confirm = PLAN.replace(
    "None. Both readings are resolved in the decisions tables below.",
    "- Should the key be `notes` or `agent_notes`? **(confirm)**",
  );
  const q = parsePlan(confirm).openQuestions;
  ok("an unresolved (confirm) is surfaced", q.length === 1 && q[0].startsWith("Should the key"));

  const noBranch = parsePlan(PLAN.replace("> **Branch**: `feat/mcp-week-ids`\n", ""));
  ok(
    "a missing Branch line is a named defect",
    noBranch.problems.some((s) => s.includes("week strip") && s.includes("**Branch**")),
    JSON.stringify(noBranch.problems),
  );

  const noDepends = parsePlan(PLAN.replace("> **Depends**: —\n", ""));
  ok(
    "a missing Depends line is a defect, not an assumed empty list",
    noDepends.problems.some((s) => s.includes("**Depends**")),
    JSON.stringify(noDepends.problems),
  );

  const noDocker = parsePlan(PLAN.replace("> **Needs Docker**: no\n", ""));
  ok("a missing Needs Docker line is a defect", noDocker.problems.some((s) => s.includes("Needs Docker")));
  ok(
    "and it defaults to true, so a defective plan cannot run two Docker PRs at once",
    noDocker.prs[0].needsDocker === true,
  );

  const noAcs = parsePlan(PLAN.replace(/- \[ \] \*\*AC-3\*\*.*\n/, ""));
  ok(
    "a PR with no acceptance criteria is a defect",
    noAcs.problems.some((s) => s.includes("acceptance criteria")),
    JSON.stringify(noAcs.problems),
  );

  const noWhy = parsePlan(PLAN.replace("## Why\n", "## Wai\n"));
  ok("a missing Why is a defect", noWhy.problems.some((s) => s.includes("## Why")));

  const noPrs = parsePlan("# Just a title\n\n## Why\n\nbecause.\n");
  ok("a plan with no PR section is a defect", noPrs.problems.some((s) => s.includes("Pull requests")));

  const empty = parsePlan("");
  ok("an empty file is defects, not a throw", empty.problems.length >= 2);
}

// ── remote annotation ────────────────────────────────────────────────────────
group("remote annotation — open is not merged");
{
  const prs = [{ title: "feat(a): a" }, { title: "feat(b): b" }, { title: "feat(c): c" }];
  const a = annotate(
    prs,
    [
      { number: 1, title: "feat(a): a", state: "OPEN" },
      { number: 2, title: "feat(b): b", state: "MERGED" },
    ],
    ["feat(c): c", "chore: something else"],
  );
  ok("an OPEN PR exists but is not merged", a[0].prExists === true && a[0].merged === false);
  ok("a MERGED PR is both", a[1].prExists === true && a[1].merged === true);
  ok("a subject on main counts as merged without a PR row", a[2].merged === true);
  ok("its number is carried for the report", a[0].prNumber === 1);
  ok(
    "a near-miss title is not matched",
    annotate([{ title: "feat(a): a!" }], [{ number: 1, title: "feat(a): a", state: "MERGED" }], [])[0]
      .prExists === false,
  );
}

// ── the CLI contract the workflow depends on ─────────────────────────────────
group("CLI");
{
  const dir = mkdtempSync(join(tmpdir(), "parse-plan-"));
  const good = join(dir, "fixture-plan.md");
  writeFileSync(good, PLAN);
  const script = join(here, "parse-plan.mjs");
  const call = (args) => {
    try {
      return {
        code: 0,
        out: execFileSync("node", [script, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }),
      };
    } catch (e) {
      return { code: e.status, out: e.stdout || "", err: e.stderr || "" };
    }
  };

  const r = call([good, "--no-remote", "--no-snapshot"]);
  ok("exits 0 on a legal plan", r.code === 0, r.err);
  let json = null;
  try {
    json = JSON.parse(r.out);
  } catch {
    /* reported below */
  }
  ok("stdout is one JSON document", json !== null);
  if (json) {
    ok("prCount matches the PR array", json.prCount === json.prs.length && json.prCount === 2);
    ok("planSha is present", typeof json.planSha === "string" && json.planSha.length === 12);
    ok("problems are not shipped to the workflow", json.problems === undefined);
    ok("every PR carries prExists/merged", json.prs.every((p) => "prExists" in p && "merged" in p));
    ok(
      "the JSON is compact enough to echo — under 12 KB for a two-PR plan",
      r.out.length < 12_000,
      `${r.out.length} bytes`,
    );
  }

  const missingBranch = join(dir, "broken-plan.md");
  writeFileSync(missingBranch, PLAN.replace("> **Branch**: `feat/mcp-week-ids`\n", ""));
  const b = call([missingBranch, "--no-remote", "--no-snapshot"]);
  ok("exits 2 on a plan defect", b.code === 2, JSON.stringify(b));
  ok("the defect is on stderr, not stdout", b.err.includes("**Branch**") && b.out.trim() === "");
  ok("stdout stays empty so nothing downstream parses a partial plan", b.out === "");

  ok("exits 4 on a missing file", call([join(dir, "nope.md"), "--no-remote"]).code === 4);
  ok("exits 4 with no argument", call(["--no-remote"]).code === 4);

  // The snapshot is what makes the run survive the plan being deleted.
  const snap = call([good, "--no-remote"]);
  const snapPath = snap.code === 0 ? JSON.parse(snap.out).planSnapshot : null;
  ok("a snapshot is written and its path returned", !!snapPath && existsSync(snapPath), String(snapPath));
  ok("the snapshot is not the original file", snapPath !== resolve(good));
}

// ── the real plan in this checkout, if one is present ────────────────────────
group("live plan in the repository root, if any");
{
  const root = resolve(here, "..");
  const plans = existsSync(root)
    ? execFileSync("bash", ["-c", `ls ${root}/*-plan.md 2>/dev/null || true`], { encoding: "utf8" })
        .split("\n")
        .filter(Boolean)
    : [];
  if (!plans.length) {
    console.log("  (skipped — no *-plan.md in the repository root)");
  } else {
    for (const p of plans) {
      const parsed = parsePlan(execFileSync("cat", [p], { encoding: "utf8" }));
      ok(`${p.split("/").pop()} parses with no defects`, parsed.problems.length === 0, JSON.stringify(parsed.problems, null, 1));
      ok(`${p.split("/").pop()} yields at least one PR with criteria`, parsed.prs.length > 0 && parsed.prs.every((x) => x.acceptance.length > 0));
      ok(`${p.split("/").pop()} yields a Why long enough to reason from`, parsed.why.length > 80);
    }
  }
}

console.log(`\n  ${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
