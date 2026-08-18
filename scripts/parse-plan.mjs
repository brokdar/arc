#!/usr/bin/env node
// Parse a `<slug>-plan.md` feature plan into the JSON `implement-plan` builds from.
//
// WHY THIS IS A SCRIPT AND NOT AN AGENT. The Workflow runtime has no shell and
// no filesystem — a workflow script can only call `agent()` — so every
// mechanical step in the pipeline has to be *something an agent runs*, not
// something an agent does. Reading a fixed template into a fixed shape is
// mechanical: it has one right answer, it is the same answer every time, and it
// is testable without a model. Done by an agent it cost ~4 minutes and two
// StructuredOutput schema retries per launch (runs wf_317382cb and wf_0fddad15,
// 16 Aug 2026), and a mis-parse — a `###` heading swallowed, an edge case
// dropped — is invisible: the developer just builds against criteria that are
// missing a line.
//
// So the agent's job shrinks to "run this and hand back what it printed", and
// the parse itself is pinned by `scripts/parse-plan.test.mjs`.
//
// It is deliberately STRICT. A marked line the planner forgot is reported as a
// plan defect with the PR and the line named, before any agent runs — which is
// the cheapest moment to find it. `plan-template.md` promises this.
//
// Usage:
//   node scripts/parse-plan.mjs <plan.md> [--no-remote] [--no-snapshot]
//
// Exit codes:  0 ok (JSON on stdout) · 2 plan defects · 3 remote state
// unavailable · 4 usage.

import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// ── Pure parsing ─────────────────────────────────────────────────────────────
// Everything in this section is a function of the plan text. `parse-plan.test.mjs`
// imports it directly.

const DASH = /^(—|-{1,2}|none)$/i; // "—" is the template's "nothing here"

/** CRLF is not cosmetic here: `\r` is a line terminator in JavaScript, so `.` and
 * `$` stop before it and every `> **Field**:` line became invisible — a plan saved
 * on Windows reported "no `> **Branch**:` line" for a line that is plainly there. */
export const normalizeEol = (text) => text.replace(/\r\n?/g, "\n");

/** Per-line "is this inside a ``` fence?" mask. A plan legitimately shows the
 * shape it wants emitted, and `plan-template.md` is itself one long fenced
 * example — so a `## ` or `### ` inside a fence used to truncate a section or
 * invent a phantom PR. */
export function fenceMask(lines) {
  const mask = [];
  let open = null;
  for (const line of lines) {
    const m = line.match(/^\s*(`{3,}|~{3,})/);
    if (open === null && m) {
      open = m[1][0].repeat(3);
      mask.push(true);
      continue;
    }
    if (open !== null && m && m[1].startsWith(open)) {
      open = null;
      mask.push(true);
      continue;
    }
    mask.push(open !== null);
  }
  return mask;
}
/** True when this line is a column-0 heading that is NOT inside a fence. */
const headingAt = (lines, mask, i, prefix) => !mask[i] && lines[i].startsWith(prefix);

/** The text under `## <name>`, up to the next column-0 `## ` heading. */
export function section(text, name) {
  const lines = normalizeEol(text).split("\n");
  const mask = fenceMask(lines);
  const start = lines.findIndex((l, i) => !mask[i] && l.trim() === `## ${name}`);
  if (start === -1) return null;
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (headingAt(lines, mask, i, "## ")) {
      end = i;
      break;
    }
  }
  const body = lines.slice(start + 1, end).join("\n");
  // A trailing horizontal rule belongs to the document, not the section.
  return body.replace(/\n+---\s*$/, "").trim();
}

/** `### `-delimited chunks of a section, fence-aware. Returns [{title, body}]. */
export function splitPrSections(block) {
  const lines = block.split("\n");
  const mask = fenceMask(lines);
  const starts = [];
  for (let i = 0; i < lines.length; i++) if (headingAt(lines, mask, i, "### ")) starts.push(i);
  return starts.map((from, n) => ({
    title: lines[from].replace(/^### /, "").trim(),
    body: lines.slice(from + 1, n + 1 < starts.length ? starts[n + 1] : lines.length).join("\n"),
  }));
}

/**
 * Acceptance criteria, verbatim, one string per `- [ ] **AC-n** …` bullet,
 * carrying its continuation lines, its `— *level*, path` line and every nested
 * `- Edge:` line.
 *
 * The plan binds edge cases to the criterion they stress precisely so they
 * cannot be skipped, so dropping them here would silently weaken every review.
 * Continuation indentation is normalised away (the template indents by 6, edges
 * by 6 and their own continuations by 8) so an AC reads the same in a prompt as
 * it does in the plan, without the template's left margin.
 */
export function parseAcceptance(block, orphans) {
  if (!block) return [];
  const out = [];
  let cur = null;
  const flush = () => {
    if (!cur) return;
    const [head, ...rest] = cur;
    const indents = rest.filter((l) => l.trim()).map((l) => l.match(/^ */)[0].length);
    const base = indents.length ? Math.min(...indents) : 0;
    const body = rest.map((l) => (l.trim() ? l.slice(base).trimEnd() : ""));
    out.push([head, ...body].join("\n").replace(/\n+$/, ""));
    cur = null;
  };
  // `- [ ] **AC-1**`, `* [x] **AC-2**`, `-  [ ]  **AC-3**`: the bullet character
  // and the internal spacing are a human's choice, and a criterion silently
  // dropped for one of them is a criterion the developer never builds and the
  // reviewer never checks.
  const HEAD = /^[-*+]\s+\[[ xX]?\]\s+\*\*AC-/;
  // A line at column 0 that reads as a criterion or an edge case but is not one:
  // a bullet, or anything naming an AC.
  const LOST = /^\s*([-*+]\s|\d+[.)]\s)|\*\*AC-/;
  const lines = block.split("\n");
  const mask = fenceMask(lines);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (mask[i]) {
      if (cur) cur.push(line.replace(/\s+$/, ""));
      continue;
    }
    const indent = line.match(/^ */)[0].length;
    if (HEAD.test(line.trimStart()) && indent < 2) {
      flush();
      cur = [line.trim()];
    } else if (cur) {
      if (/^\s+\S/.test(line) || line.trim() === "") cur.push(line.replace(/\s+$/, ""));
      else {
        flush();
        if (orphans && LOST.test(line)) orphans.push(line.trim());
      }
    } else if (orphans && LOST.test(line)) {
      // A column-0 `- Edge:` before any criterion, or an AC whose bullet shape
      // this parser did not recognise: loud, not dropped. Narrow on purpose —
      // only lines that LOOK like a criterion or an edge that lost its home.
      // Explanatory prose in a section is not a lost criterion, and flagging it
      // made the template's own example fail its own parser.
      orphans.push(line.trim());
    }
  }
  flush();
  return out;
}

/** `> **Field**: value` lines from a PR's blockquote header. */
export function blockquoteFields(block) {
  const out = {};
  for (const line of block.split("\n")) {
    const m = line.match(/^>\s*\*\*(.+?)\*\*:\s*(.*)$/);
    if (m) out[m[1].trim()] = m[2].trim();
  }
  return out;
}

const unTick = (s) => s.replace(/`/g, "").trim();

/** A `—`-tolerant comma/semicolon list, backticks stripped. */
export function listField(raw) {
  if (raw == null) return [];
  const v = raw.trim();
  if (!v || DASH.test(unTick(v))) return [];
  return v
    .split(/[,;]/)
    .map(unTick)
    .filter((s) => s && !DASH.test(s));
}

/**
 * The paragraph introduced by `**Label**:` — every line up to the next blank
 * line. Verbatim: these are carried into the developer prompt, and `why` in
 * particular is the one thing that cannot be recovered from the codebase.
 */
export function labelled(block, label) {
  const lines = block.split("\n");
  const i = lines.findIndex((l) => l.startsWith(`**${label}**`));
  if (i === -1) return null;
  const first = lines[i].replace(new RegExp(`^\\*\\*${label}\\*\\*:?\\s*`), "");
  const acc = [first];
  for (const line of lines.slice(i + 1)) {
    if (!line.trim()) break;
    if (/^\*\*/.test(line) || /^#{1,6} /.test(line)) break;
    acc.push(line.trim());
  }
  return acc.join(" ").replace(/\s+/g, " ").trim() || null;
}

/**
 * One string per row of the "Decisions landing in code" table, in the shape the
 * developer and reviewer prompts read: `<decision> | displaces <x> | lands in <site>`.
 */
export function parseDecisions(block) {
  const lines = block.split("\n");
  const start = lines.findIndex((l) => l.startsWith("**Decisions landing in code**"));
  if (start === -1) return [];
  const out = [];
  for (const line of lines.slice(start + 1)) {
    const t = line.trim();
    if (!t) continue;
    if (!t.startsWith("|")) {
      if (out.length || /^\*\*/.test(t)) break;
      continue;
    }
    // `\|` is an escaped pipe inside a cell, not a column break; and a row with
    // MORE than three columns keeps its tail in the last one rather than losing
    // it — that column names where the decision lands, which is the whole point
    // of the table.
    const cells = t
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split(/(?<!\\)\|/)
      .map((c) => c.replace(/\\\|/g, "|").trim());
    if (cells.length < 3) continue;
    if (cells.length > 3) cells.splice(2, cells.length - 2, cells.slice(2).join(" | "));
    if (/^-{3,}$/.test(cells[0].replace(/[\s:]/g, "")) || /^-+$/.test(cells[0])) continue;
    if (/^decision$/i.test(cells[0])) continue;
    out.push(`${cells[0]} | displaces ${cells[1]} | lands in ${cells[2]}`);
  }
  return out;
}

/** Parse the whole plan. Structural defects land in `problems`, never in a throw. */
export function parsePlan(raw) {
  const text = normalizeEol(raw);
  const problems = [];
  const titleLine = text.split("\n").find((l) => /^# \S/.test(l));
  const feature = titleLine ? titleLine.replace(/^# /, "").trim() : null;
  if (!feature) problems.push("No `# <Feature name>` H1 on the first non-blank line.");

  // The plan's `> **Source**:` line. Carried to the push seat, which uses it to
  // find the issue a PR closes: PRs #54 and #55 linked no issue while #53, #56
  // and #57 did, purely because some push agents happened to load a skill that
  // told them to look and others did not.
  const source = (blockquoteFields(text.split(/^## /m)[0] || "").Source || "").trim();

  const why = section(text, "Why");
  if (!why) problems.push("No `## Why` section. Every developer prompt carries it verbatim.");

  const openBlock = section(text, "Open questions") || "";
  const openQuestions = openBlock
    .split("\n")
    .filter((l) => l.includes("**(confirm)**"))
    .map((l) => l.replace(/^[-*]\s*/, "").trim());

  const featureOrphans = [];
  const featureAcceptance = parseAcceptance(section(text, "Feature acceptance"), featureOrphans);
  for (const line of featureOrphans) {
    problems.push(`Feature acceptance: this line is neither a criterion nor part of one — "${line.slice(0, 90)}"`);
  }

  const prsBlock = section(text, "Pull requests");
  const prs = [];
  if (!prsBlock) {
    problems.push("No `## Pull requests` section.");
  } else {
    const chunks = splitPrSections(prsBlock);
    if (!chunks.length) problems.push("`## Pull requests` contains no `### <title>` section.");
    for (const { title, body } of chunks) {
      const f = blockquoteFields(body);
      const where = `PR "${title}"`;
      const branch = f.Branch ? unTick(f.Branch) : null;
      if (!branch) problems.push(`${where}: no \`> **Branch**:\` line.`);
      if (f.Depends === undefined) {
        problems.push(`${where}: no \`> **Depends**:\` line — write \`—\` when it depends on nothing.`);
      }
      if (f["Needs Docker"] === undefined) {
        problems.push(`${where}: no \`> **Needs Docker**:\` line.`);
      }
      const orphans = [];
      const acceptance = parseAcceptance(
        body.includes("**Acceptance**") ? body.slice(body.indexOf("**Acceptance**")) : "",
        orphans,
      );
      if (!acceptance.length) {
        problems.push(`${where}: no \`- [ ] **AC-n**\` acceptance criteria under \`**Acceptance**\`.`);
      }
      // A line inside the acceptance block that is neither a criterion nor a
      // continuation of one is REPORTED, never dropped: a `- Edge:` at column
      // zero, or an AC written with a bullet shape this parser does not read,
      // used to vanish — and a criterion nobody can see is one nobody builds.
      for (const line of orphans) {
        problems.push(`${where}: this line under **Acceptance** belongs to no criterion — "${line.slice(0, 90)}". Indent it under its AC, or write it as \`- [ ] **AC-n** …\`.`);
      }
      const prWhy = labelled(body, "Why this PR");
      const delivers = labelled(body, "Delivers");
      const reuses = labelled(body, "Reuses");
      if (!prWhy) problems.push(`${where}: no \`**Why this PR**:\` paragraph.`);
      if (!delivers) problems.push(`${where}: no \`**Delivers**:\` paragraph.`);
      if (!reuses) problems.push(`${where}: no \`**Reuses**:\` paragraph.`);

      prs.push({
        title,
        branch: branch || "",
        depends: listField(f.Depends),
        why: prWhy || "",
        delivers: delivers || "",
        reuses: reuses || "",
        owns: listField(f.Owns),
        // Absent reads as "needs Docker": the expensive-but-correct default. A
        // PR wrongly marked no runs `test-int` concurrently with another and
        // both hit the same fixed ports.
        needsDocker: !/^no\b/i.test((f["Needs Docker"] || "").trim()),
        triggers: listField(f.Triggers),
        decisions: parseDecisions(body),
        acceptance,
      });
    }
  }

  return { feature: feature || "", source, why: why || "", openQuestions, featureAcceptance, prs, problems };
}

// ── Remote state ─────────────────────────────────────────────────────────────
// `prExists` and `merged` are two DIFFERENT questions and collapsing them is a
// real bug: an open PR means "do not build this again", but only a merged one
// means "its dependents may start", because a dependent is cut from
// origin/main. Both are read from gh and git — never from the plan's checkboxes.

function run(cmd, args, opts = {}) {
  return execFileSync(cmd, args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], ...opts });
}
function tryRun(cmd, args) {
  try {
    return { ok: true, out: run(cmd, args) };
  } catch (e) {
    return { ok: false, out: "", err: String((e && e.stderr) || (e && e.message) || e) };
  }
}

export function annotate(prs, ghPrs, mainSubjects) {
  const key = (t) => String(t == null ? "" : t).trim();
  const byTitle = new Map();
  for (const p of ghPrs) {
    const k = key(p.title);
    const seen = byTitle.get(k);
    // On a title collision a MERGED row wins, then OPEN: the oldest CLOSED
    // duplicate used to win on `gh`'s newest-first ordering and answer both
    // questions wrongly.
    const rank = (x) => (x.state === "MERGED" ? 2 : x.state === "OPEN" ? 1 : 0);
    if (!seen || rank(p) > rank(seen)) byTitle.set(k, p);
  }
  const onMain = new Set(mainSubjects.map(key));
  return prs.map((p) => {
    const gh = byTitle.get(key(p.title));
    const merged = (gh && gh.state === "MERGED") || onMain.has(key(p.title));
    return {
      ...p,
      // A PR the operator CLOSED without merging is an abandoned attempt, and
      // "close its PR and delete the branch" is the documented way to redo one —
      // so a closed PR must not read as "already built" and freeze the plan.
      prExists: !!gh && gh.state !== "CLOSED",
      prState: gh ? gh.state : null,
      merged,
      prNumber: gh ? gh.number : null,
    };
  });
}

// ── CLI ──────────────────────────────────────────────────────────────────────

function main(argv) {
  const args = argv.filter((a) => !a.startsWith("--"));
  // `--no-remote=true` used to be filtered out of `args` and not recognised as
  // the flag, so it silently meant the opposite.
  const flag = (name) => argv.some((a) => a === `--${name}` || a.startsWith(`--${name}=`));
  const planArg = args[0];
  if (!planArg) {
    process.stderr.write("usage: parse-plan.mjs <plan.md> [--no-remote] [--no-snapshot]\n");
    return 4;
  }
  const planPath = resolve(planArg);
  if (!existsSync(planPath)) {
    process.stderr.write(`Plan not found: ${planPath}\n`);
    return 4;
  }
  let text;
  try {
    text = readFileSync(planPath, "utf8");
  } catch (e) {
    // A directory passes existsSync and then throws EISDIR, which exited 1 — and
    // 1 is a meaningful code to this script's callers.
    process.stderr.write(`Could not read ${planPath}: ${e.message}\n`);
    return 4;
  }
  const parsed = parsePlan(text);
  if (parsed.problems.length) {
    process.stderr.write(
      `PLAN DEFECTS in ${basename(planPath)} — fix the plan and re-launch:\n` +
        parsed.problems.map((p) => `  - ${p}`).join("\n") +
        "\n",
    );
    return 2;
  }

  let ghPrs = [];
  let mainSubjects = [];
  let fetched = false;
  if (!flag("no-remote")) {
    // 100 silently truncates: this repository is already past 55 pull requests,
    // and a truncated list answers "does a PR for this title exist?" with a false
    // negative, which rebuilds work that is already open.
    const gh = tryRun("gh", [
      "pr", "list", "--state", "all", "--limit", "1000", "--json", "number,title,state",
    ]);
    if (!gh.ok) {
      process.stderr.write(
        "Remote state unavailable — `gh pr list` failed:\n" +
          `${gh.err}\n` +
          "Refusing to parse: without it, an already-open PR is rebuilt and an unmerged\n" +
          "prerequisite looks ready. Fix auth/connectivity and re-launch.\n",
      );
      return 3;
    }
    try {
      ghPrs = JSON.parse(gh.out);
    } catch {
      process.stderr.write("Remote state unavailable — `gh pr list` returned unparseable JSON.\n");
      return 3;
    }
    fetched = tryRun("git", ["fetch", "origin", "main"]).ok;
    if (!fetched) {
      // Not fatal — `git log origin/main` still answers from the ref we have —
      // but the answer is then as old as the ref, and "is this merged?" decides
      // whether a dependent may start. Say so instead of recording it silently.
      process.stderr.write(
        "WARNING: `git fetch origin main` failed, so mergedness was resolved against a possibly stale\n" +
          "origin/main. A prerequisite that merged very recently may read as unmerged, which halts the\n" +
          "run rather than building on it — safe, but re-launch once the fetch works.\n",
      );
    }
    const log = tryRun("git", ["log", "--format=%s", "-n", "150", "origin/main"]);
    if (!log.ok) {
      process.stderr.write(
        "Remote state unavailable — `git log origin/main` failed. A dependency is\n" +
          '"is it on main", and that cannot be answered without the ref.\n',
      );
      return 3;
    }
    mainSubjects = log.out.split("\n").map((s) => s.trim()).filter(Boolean);
  }

  // Snapshot the plan. WHY: the plan is one untracked file in the main checkout
  // and every seat needs it for the whole run. On 16 Aug 2026 it was deleted
  // between the parse step (12:59:37) and the implement step (13:04:29) of run
  // wf_e0fcc017 — five minutes — and the review that approved PR #55 therefore
  // never read it. Nothing noticed. A copy the operator does not know about
  // cannot be tidied away mid-run, and it keeps them free to delete plans
  // whenever they like, which is the stance `.gitignore` and `arc-reviewer.md`
  // already take.
  let planSnapshot = planPath;
  if (!flag("no-snapshot")) {
    // Anchored on the COMMON git dir, like the Docker lock: `--show-toplevel`
    // resolves to the current WORKTREE, and a snapshot that lives inside a
    // worktree dies when that worktree is reclaimed mid-run — the precise failure
    // the snapshot exists to prevent.
    const common = tryRun("git", ["rev-parse", "--git-common-dir"]);
    const anchor = common.ok ? dirname(resolve(common.out.trim())) : process.cwd();
    const dir = join(anchor, ".claude", "plan-snapshots");
    mkdirSync(dir, { recursive: true });
    planSnapshot = join(dir, basename(planPath));
    copyFileSync(planPath, planSnapshot);
  }

  const out = {
    ...parsed,
    prs: annotate(parsed.prs, ghPrs, mainSubjects),
    planSnapshot,
    planSha: createHash("sha256").update(text).digest("hex").slice(0, 12),
    // The workflow cross-checks this against `prs.length`: the parse seat has to
    // copy this JSON into its structured output, and a silently truncated echo
    // that drops a PR would otherwise read as a shorter plan.
    prCount: parsed.prs.length,
    remote: { fetched, ghPrs: ghPrs.length, mainSubjects: mainSubjects.length },
  };
  delete out.problems;
  process.stdout.write(JSON.stringify(out) + "\n");
  return 0;
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  process.exit(main(process.argv.slice(2)));
}
