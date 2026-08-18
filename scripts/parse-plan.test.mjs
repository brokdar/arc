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
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  annotate,
  blockquoteFields,
  fenceMask,
  labelled,
  listField,
  normalizeEol,
  parseAcceptance,
  parseDecisions,
  parsePlan,
  section,
  splitPrSections,
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


// ── the silent-loss cases, which are the ones that matter ────────────────────
group("CRLF: a plan saved on Windows must not lose its marked lines");
{
  ok("normalizeEol strips CR", normalizeEol("a\r\nb\rc") === "a\nb\nc");
  ok("blockquote fields survive a CR", blockquoteFields(normalizeEol("> **Branch**: `feat/a`\r\n")).Branch === "`feat/a`");
  const crlf = parsePlan(PLAN.replace(/\n/g, "\r\n"));
  ok("a CRLF plan has no defects", crlf.problems.length === 0, JSON.stringify(crlf.problems, null, 1));
  ok("  …its branches parse", crlf.prs.map((x) => x.branch).join() === "feat/mcp-agent-notes-read-path,feat/mcp-week-ids");
  ok("  …its Needs Docker parses", crlf.prs[0].needsDocker === false && crlf.prs[1].needsDocker === true);
  ok("  …its edges survive", (crlf.prs[0].acceptance[0].match(/- Edge:/g) || []).length === 2);
  ok("  …and no stray CR is left in the text", !JSON.stringify(crlf).includes("\\r"));
}

group("fenced blocks: a plan may show the shape it wants emitted");
{
  ok("fenceMask marks the inside of a fence", fenceMask(["a", "```", "in", "```", "b"]).join() === "false,true,true,true,false");
  ok("tildes fence too", fenceMask(["~~~", "in", "~~~"]).join() === "true,true,true");
  ok("a longer closing fence closes", fenceMask(["```js", "in", "````", "out"]).join() === "true,true,true,false");

  const fenced = PLAN.replace(
    "## Done means",
    "The shape we emit:\n\n```markdown\n## Not a heading\n### Not a PR\n```\n\nAnd the real reason continues here.\n\n## Done means",
  );
  const p2 = parsePlan(fenced);
  ok("a fenced `## ` does not truncate the section", section(fenced, "Why").includes("the real reason continues here"), JSON.stringify(section(fenced, "Why")));
  ok("a fenced `### ` does not invent a PR", p2.prs.length === 2, JSON.stringify(p2.prs.map((x) => x.title)));
  ok("and the plan still has no defects", p2.problems.length === 0, JSON.stringify(p2.problems, null, 1));
  ok("splitPrSections ignores a fenced heading", splitPrSections("### real\n\n```\n### fake\n```\n").length === 1);
}

group("near-miss acceptance bullets are read, or reported — never dropped");
{
  const shapes = [
    ["* [ ] **AC-1** starred bullet — *unit*, `t.py`", "an asterisk bullet"],
    ["+ [ ] **AC-1** plus bullet — *unit*, `t.py`", "a plus bullet"],
    ["-  [ ]  **AC-1** loose spacing — *unit*, `t.py`", "doubled spaces inside the bullet"],
    ["- [X] **AC-1** upper-case tick — *unit*, `t.py`", "an upper-case tick"],
  ];
  for (const [line, what] of shapes) {
    ok(`reads ${what}`, parseAcceptance(line).length === 1, JSON.stringify(parseAcceptance(line)));
  }
  // A `- Edge:` at column zero is the dangerous one: it used to be swallowed as
  // nothing at all, so the criterion lost its edge case in silence.
  const orphans = [];
  const acs = parseAcceptance("- [ ] **AC-1** the thing — *unit*, `t.py`\n- Edge: at column zero\n", orphans);
  ok("a column-0 edge does not silently vanish", orphans.length === 1 && /column zero/.test(orphans[0]), JSON.stringify({ acs, orphans }));
  const p3 = parsePlan(PLAN.replace("      - Edge: a Monday with no notes — `[]`, not `null`", "- Edge: a Monday with no notes"));
  ok("and a plan containing one is a reported defect", p3.problems.some((x) => /belongs to no criterion/.test(x)), JSON.stringify(p3.problems));
  ok("  …naming the line", p3.problems.some((x) => /a Monday with no notes/.test(x)));
  // Narrow on purpose: prose inside a section is not a lost criterion. Flagging it
  // made the template's own example fail the parser the template tells you to run.
  const prose = [];
  parseAcceptance("<Criteria no single PR can satisfy — verified against `main`.>\n\n- [ ] **AC-1** x — *unit*, `t.py`", prose);
  ok("explanatory prose is not reported as a lost criterion", prose.length === 0, JSON.stringify(prose));
  const numbered = [];
  parseAcceptance("1. **AC-9** a numbered criterion", numbered);
  ok("but a numbered pseudo-criterion is", numbered.length === 1, JSON.stringify(numbered));

  // The template's own fenced example must parse with zero defects — it is what
  // the template tells the planner to check with this exact parser.
  const tmpl = readFileSync(resolve(here, "../.claude/skills/feature-plan/plan-template.md"), "utf8");
  const fenced = /^````markdown\n([\s\S]*?)^````$/m.exec(tmpl);
  ok("the template has its fenced example", !!fenced);
  if (fenced) {
    const parsedTemplate = parsePlan(fenced[1]);
    ok("plan-template.md's example parses with no defects", parsedTemplate.problems.length === 0, JSON.stringify(parsedTemplate.problems, null, 1));
    ok("  …into two PRs, each with criteria", parsedTemplate.prs.length === 2 && parsedTemplate.prs.every((x) => x.acceptance.length > 0));
    ok("  …and its Open questions placeholder does not trip the confirm stop", parsedTemplate.openQuestions.length === 0);
  }
}

group("a pipe inside a decisions cell keeps the landing site");
{
  const rows = parseDecisions(
    "**Decisions landing in code**\n\n| Decision | Displaces | Lands in |\n| --- | --- | --- |\n" +
      "| scopes are `read\\|write` | one scope | `identity.py` docstring |\n",
  );
  ok("the row parses", rows.length === 1, JSON.stringify(rows));
  ok("the escaped pipe is restored", rows[0].includes("`read|write`"), rows[0]);
  ok("the landing site survives", rows[0].endsWith("lands in `identity.py` docstring"), rows[0]);
  const extra = parseDecisions(
    "**Decisions landing in code**\n| a | b | c | d |\n",
  );
  ok("a fourth column is folded into the landing site, not dropped", extra[0] === "a | displaces b | lands in c | d", JSON.stringify(extra));
}

group("remote state: closed is not built-already, and MERGED wins a collision");
{
  const a = annotate(
    [{ title: "feat(a): a" }, { title: "feat(b): b" }],
    [
      { number: 1, title: "feat(a): a", state: "CLOSED" },
      { number: 2, title: "feat(b): b", state: "OPEN" },
    ],
    [],
  );
  ok("a CLOSED PR does not count as existing", a[0].prExists === false, JSON.stringify(a[0]));
  ok("  …and its state is carried for the report", a[0].prState === "CLOSED");
  ok("an OPEN PR still counts", a[1].prExists === true);

  const collide = annotate(
    [{ title: "feat(x): dup" }],
    [
      { number: 10, title: "feat(x): dup", state: "CLOSED" },
      { number: 9, title: "feat(x): dup", state: "MERGED" },
    ],
    [],
  );
  ok("MERGED wins a title collision whatever the order", collide[0].merged === true && collide[0].prNumber === 9, JSON.stringify(collide[0]));

  const spaced = annotate([{ title: " feat(y): y " }], [{ number: 3, title: "feat(y): y", state: "OPEN" }], []);
  ok("a stray space either side still matches", spaced[0].prExists === true);
  ok("a subject on main matches with surrounding space", annotate([{ title: "feat(z): z" }], [], [" feat(z): z "])[0].merged === true);
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
    // The parse seat has to copy this document into its structured output, so its
    // size is a real constraint. Calibrated on the LIVE plan, not on the toy
    // fixture: the toy is ~2 KB and could never fail this.
    const live = execFileSync("bash", ["-c", `ls ${resolve(here, "..")}/*-plan.md 2>/dev/null | head -1`], { encoding: "utf8" }).trim();
    if (live) {
      const big = call([live, "--no-remote", "--no-snapshot"]);
      ok("a real plan's JSON stays inside the ~20 KB echo budget", big.code === 0 && big.out.length < 20_000, `${big.out.length} bytes`);
    } else {
      ok("the fixture's JSON is compact", r.out.length < 12_000, `${r.out.length} bytes`);
    }
  }

  const missingBranch = join(dir, "broken-plan.md");
  writeFileSync(missingBranch, PLAN.replace("> **Branch**: `feat/mcp-week-ids`\n", ""));
  const b = call([missingBranch, "--no-remote", "--no-snapshot"]);
  ok("exits 2 on a plan defect", b.code === 2, JSON.stringify(b));
  ok("the defect is on stderr, not stdout", b.err.includes("**Branch**") && b.out.trim() === "");
  ok("stdout stays empty so nothing downstream parses a partial plan", b.out === "");

  ok("exits 4 on a missing file", call([join(dir, "nope.md"), "--no-remote"]).code === 4);
  ok("exits 4 with no argument", call(["--no-remote"]).code === 4);
  // A directory passed existsSync and then threw EISDIR, exiting 1 — a code that
  // means something else to this script's callers.
  ok("exits 4 on a directory", call([dir, "--no-remote"]).code === 4);
  // `--no-remote=true` was filtered out of the positional args AND not recognised
  // as the flag, so it silently meant "do query the remote".
  const eq = call([good, "--no-remote=true", "--no-snapshot=true"]);
  ok("honours --flag=value form", eq.code === 0 && JSON.parse(eq.out).remote.ghPrs === 0, JSON.stringify(eq).slice(0, 200));

  // Remote failure: exit 3, with nothing on stdout for anything downstream to
  // half-parse. A fake `gh` on PATH is the only way to reach this branch.
  const fakeBin = join(dir, "bin");
  execFileSync("mkdir", ["-p", fakeBin]);
  writeFileSync(join(fakeBin, "gh"), "#!/bin/sh\necho 'gh: could not authenticate' >&2\nexit 1\n");
  execFileSync("chmod", ["+x", join(fakeBin, "gh")]);
  let remoteFail;
  try {
    execFileSync("node", [script, good, "--no-snapshot"], {
      encoding: "utf8", stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, PATH: `${fakeBin}:${process.env.PATH}` },
    });
    remoteFail = { code: 0, out: "", err: "" };
  } catch (e) {
    remoteFail = { code: e.status, out: e.stdout || "", err: e.stderr || "" };
  }
  ok("exits 3 when gh cannot answer", remoteFail.code === 3, JSON.stringify(remoteFail).slice(0, 300));
  ok("  …says why, on stderr", /Remote state unavailable/.test(remoteFail.err));
  ok("  …and prints nothing on stdout", remoteFail.out === "");

  // The snapshot is what makes the run survive the plan being deleted.
  const snap = call([good, "--no-remote"]);
  const snapPath = snap.code === 0 ? JSON.parse(snap.out).planSnapshot : null;
  ok("a snapshot is written and its path returned", !!snapPath && existsSync(snapPath), String(snapPath));
  ok("the snapshot is not the original file", snapPath !== resolve(good));
}

// ── the real plan in this checkout, if one is present ────────────────────────
group("live plan in the repository root (opt in with PARSE_PLAN_CHECK_LIVE=1)");
{
  // Off by default: these are untracked, gitignored, work-in-progress files, and
  // `parse-plan-test` runs on every edit to the parser — so a half-written plan
  // sitting in the root would fail a commit that has nothing to do with it.
  if (!process.env.PARSE_PLAN_CHECK_LIVE) {
    console.log("  (skipped — set PARSE_PLAN_CHECK_LIVE=1 to check the working tree's plans)");
  } else {
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
        const parsed = parsePlan(readFileSync(p, "utf8"));
        const name = p.split("/").pop();
        ok(`${name} parses with no defects`, parsed.problems.length === 0, JSON.stringify(parsed.problems, null, 1));
        ok(`${name} yields at least one PR with criteria`, parsed.prs.length > 0 && parsed.prs.every((x) => x.acceptance.length > 0));
        ok(`${name} yields a Why long enough to reason from`, parsed.why.length > 80);
      }
    }
  }
}

console.log(`\n  ${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
