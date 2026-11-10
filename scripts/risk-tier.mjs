#!/usr/bin/env node
// Classify a pull request into a review lane — auto, agent or owner — from what
// it changes, never from who wrote it or what an LLM thinks of it.
//
//   owner  touches a path in .github/CODEOWNERS (auth, MCP auth, migrations,
//          proxy and deploy files, CI, hooks, .claude, scripts, toolchain pins),
//          or cannot be classified at all. The operator's approving review is
//          required by GitHub itself; this label only says so early.
//   auto   cannot change behaviour: a small docs/test-only change, or a
//          dependabot bump of a kind CI is a sufficient judge of.
//   agent  everything else: ordinary code behind the existing tests.
//
// WHY THE OWNER PATHS ARE NOT LISTED HERE. They are .github/CODEOWNERS, read
// from the BASE branch. That file is what `protect-main` enforces, so a second
// list in a script or a doc would be a list that can disagree with the one that
// bites. One consequence worth knowing: a dependabot bump of a GitHub Action
// edits .github/workflows/**, which is owned, so it is owner lane and waits for
// the operator's approval like any other change to CI. That is the policy, not a
// bug in this classifier.
//
// FAIL SAFE. Anything this cannot classify is owner: no files, a path that is
// absolute or climbs with `..`, a CODEOWNERS that is unreadable, empty, or holds
// a glob (the checker, scripts/check-codeowners.sh, forbids those, so one here
// means the file is not the one it checks), or a diff GitHub truncated (3000
// files). A rename counts at BOTH ends: moving an owned file out of its directory
// is a change to the owned path.
//
// WHAT `auto` MEANS: prose, a brand-new test, or a dependency bump whose actual
// changed lines are dependency pins. Not "small" and not "in the tests/ tree": a
// modified conftest, a mocks/ fixture, a golden file or a gutted test is code the
// gate runs, and a rename can move production code into docs. So the docs/test
// lane looks at BOTH ends of every rename, accepts only Markdown, and accepts a
// test only when it is newly added and is not support code.
//
// An agent's approval never replaces the operator's on this lane. The author is
// reported, and it matters in exactly one place: a PR is only treated as
// dependabot's when its opener AND every commit on it are dependabot's, because
// anyone with write access can push to a `dependabot/*` branch.
//
// THE LABEL IS NOT AN INPUT TO ANY MERGE DECISION. Labels can be set by anyone
// with triage access, and the workflow YAML that sets them comes from the PR's own
// merge ref. Anything that lets an agent merge unattended must recompute the tier
// from the base branch's copy of this script at that moment.
//
// Usage:
//   node scripts/risk-tier.mjs --pr <n> [--repo owner/name] [--codeowners <file>]
//   node scripts/risk-tier.mjs --input <pr.json> [--codeowners <file>]
//   node scripts/risk-tier.mjs --replay <n>     # a table of the last n merged PRs,
//                                               # classified by TODAY's CODEOWNERS
// Prints {"tier", "reasons", "authorType"} as JSON (or a markdown table).

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const TIERS = ["auto", "agent", "owner"];

/** GitHub's `pulls/{n}/files` stops at 3000 files; a diff that long is cut off. */
export const FILE_LIMIT = 3000;
/** Lines changed, additions plus deletions, up to which a docs/test-only change is auto. */
export const SMALL_CHANGE_LINES = 200;

const TEST_TREE = /^(backend\/tests\/|frontend\/tests\/|frontend\/e2e\/)/;
// Code the gate itself runs, even though it sits in a test tree: collection hooks,
// shared fixtures and mocks, golden data (binary, so GitHub counts it as 0 lines),
// and the e2e setup that logs in.
const TEST_SUPPORT = /(^|\/)(conftest\.py$|(mocks|data|fixtures)\/|[^/]*setup[^/]*(\/|$))/;
// Markdown that steers agents or feeds code is not "just docs": CLAUDE.md and
// AGENTS.md steer every session, `<slug>-plan.md` is what implement-plan builds,
// and .agents/ holds the real text of the skills .claude/skills symlinks to.
const DOC_EXCLUDED = /(^|\/)(CLAUDE|AGENTS)\.md$|-plan\.md$|^\.agents\//;
const isDoc = (p) => p.endsWith(".md") && !DOC_EXCLUDED.test(p);
const isNewTest = (f) => TEST_TREE.test(f.path) && !TEST_SUPPORT.test(f.path) && f.status === "added" && !f.previous;

const DEPENDENCY_PIN = /^[+-]\s*"[A-Za-z0-9][A-Za-z0-9_.\-]*(\[[^\]]*\])?\s*(==|>=|<=|~=|!=|<|>)[^"]*"\s*,?\s*(#.*)?$/;
const VERSION = /\d+(?:\.\d+){1,3}/g;
// 0.x breaks on minors, so its "major" is the first two components.
const major = (v) => (v.startsWith("0.") ? v.split(".").slice(0, 2).join(".") : v.split(".")[0]);

/** Every changed line of a pyproject patch is a dependency pin (not config, not a source). */
export function onlyDependencyPins(patch) {
  const changed = String(patch ?? "").split("\n").filter((l) => /^[+-]/.test(l));
  return changed.length > 0 && changed.every((l) => DEPENDENCY_PIN.test(l));
}

/** The patch changes only version numbers, and none of them across a major. */
export function versionBumpsWithinMajor(patch) {
  const lines = String(patch ?? "").split("\n");
  const removed = lines.filter((l) => l.startsWith("-")).map((l) => l.slice(1));
  const added = lines.filter((l) => l.startsWith("+")).map((l) => l.slice(1));
  if (!removed.length || removed.length !== added.length) return false;
  return removed.every((r, i) => {
    const [old, now] = [r.match(VERSION), added[i].match(VERSION)];
    return old && now && old.length === now.length
      && r.replace(VERSION, "V") === added[i].replace(VERSION, "V")
      && old.every((v, k) => major(v) === major(now[k]));
  });
}

/** Parse CODEOWNERS into literal entries, collecting anything that is not one. */
export function parseCodeowners(text) {
  const entries = [];
  const problems = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const [pattern, ...owners] = line.split(/\s+/);
    if (!pattern.startsWith("/")) problems.push(`'${pattern}' is not anchored at the root`);
    else if (/[*?[]/.test(pattern)) problems.push(`'${pattern}' is a glob`);
    else if (!owners.length) problems.push(`'${pattern}' has no owner`);
    else entries.push({ pattern, owners });
  }
  if (!entries.length && !problems.length) problems.push("no entries");
  return { entries, problems };
}

/** The CODEOWNERS pattern that owns `path`, or null. */
export function ownedBy(path, entries) {
  const p = path.replace(/^\.\//, "");
  for (const { pattern } of entries) {
    const want = pattern.slice(1);
    if (want.endsWith("/") ? p.startsWith(want) : p === want) return pattern;
  }
  return null;
}

export function authorType(login) {
  const name = String(login ?? "").replace(/^app\//, "");
  if (name === "dependabot[bot]" || name === "dependabot") return "dependabot";
  if (name === "brokdar-agent[bot]" || name === "brokdar-agent") return "agent";
  return "human";
}

const badPath = (p) =>
  typeof p !== "string" || !p || p.startsWith("/") || /[\0\n]/.test(p) || p.split("/").includes("..");

const result = (tier, reasons, type) => ({ tier, reasons, authorType: type });

/**
 * @param pr {author, branch, title, files: [{path, previous?, status?, additions?, deletions?}]}
 * @param codeowners the text of .github/CODEOWNERS on the BASE branch (or null if unreadable)
 */
export function classify(pr, codeowners) {
  const type = authorType(pr.author);
  const files = Array.isArray(pr.files) ? pr.files : [];

  // ── cannot classify → owner ────────────────────────────────────────────────
  if (codeowners == null) return result("owner", ["CODEOWNERS could not be read, so nothing can be proven unowned"], type);
  const { entries, problems } = parseCodeowners(codeowners);
  if (problems.length) return result("owner", [`CODEOWNERS is not the file this trusts: ${problems.join("; ")}`], type);
  if (!files.length) return result("owner", ["no changed files were given, so nothing can be proven safe"], type);
  if (files.length >= FILE_LIMIT) return result("owner", [`the diff has ${files.length} files, which GitHub truncates, so some are unseen`], type);
  const invalid = files.flatMap((f) => [f.path, f.previous].filter((p) => p !== undefined && p !== null)).filter(badPath);
  if (invalid.length) return result("owner", [`unclassifiable path: ${JSON.stringify(invalid[0])}`], type);

  // ── owner paths, at both ends of a rename ──────────────────────────────────
  const owned = [];
  for (const f of files) {
    for (const p of [f.path, f.previous]) {
      const hit = p ? ownedBy(p, entries) : null;
      if (hit) owned.push(`${p} is under ${hit}`);
    }
  }
  if (owned.length) {
    const shown = owned.slice(0, 5);
    if (owned.length > shown.length) shown.push(`…and ${owned.length - shown.length} more owned paths`);
    return result("owner", shown, type);
  }

  if (files.some((f) => !Number.isFinite(f.additions) || !Number.isFinite(f.deletions))) {
    return result("owner", ["a file's size is missing, so the change cannot be measured"], type);
  }
  const lines = files.reduce((n, f) => n + f.additions + f.deletions, 0);

  // ── dependabot ─────────────────────────────────────────────────────────────
  if (type === "dependabot") {
    const eco = /^dependabot\/([^/]+)\//.exec(pr.branch ?? "")?.[1];
    // Opening a PR is not authoring it: anyone with write access can push to a
    // `dependabot/*` branch, and the PR still says dependabot opened it.
    const authors = Array.isArray(pr.commitAuthors) ? pr.commitAuthors : [];
    if (!authors.length || authors.some((a) => authorType(a) !== "dependabot")) {
      return result("agent", ["dependabot opened this PR but not every commit on it is dependabot's (or the commits were not given), so its content is unproven"], type);
    }
    const byPath = new Map(files.map((f) => [f.path, f]));
    const pyproject = byPath.get("backend/pyproject.toml");
    if (eco === "uv" && files.every((f) => /^backend\/(pyproject\.toml|uv\.lock)$/.test(f.path)) && byPath.has("backend/uv.lock")) {
      if (!pyproject || onlyDependencyPins(pyproject.patch)) {
        return result("auto", ["dependabot uv bump: the lock changed and every changed pyproject line is a dependency pin; majors are ignored in dependabot.yml, CI is the judge"], type);
      }
      return result("agent", ["dependabot uv PR edits pyproject lines that are not dependency pins (config, sources or scripts), or their patch was not given"], type);
    }
    if (eco === "devcontainers" && files.every((f) => /^\.devcontainer\/devcontainer(-lock)?\.json$/.test(f.path))) {
      if (files.every((f) => versionBumpsWithinMajor(f.patch))) {
        return result("auto", ["dependabot devcontainers PR whose changed lines are version numbers, none across a major"], type);
      }
      return result("agent", ["dependabot devcontainers PR that changes more than version numbers within a major (read from the diff, not the title)"], type);
    }
    return result("agent", [`dependabot PR (${eco ?? "unknown ecosystem"}) outside the auto lane: frontend, tools and image bumps get a reviewer`], type);
  }

  // ── docs and new tests ─────────────────────────────────────────────────────
  // Both ends of a rename must qualify: `docs/a.md` renamed FROM production code is
  // a deletion of that code, and a test renamed to `.txt` drops out of collection.
  const ends = files.flatMap((f) => [f.path, f.previous].filter(Boolean));
  if (files.every((f) => (isDoc(f.path) && (!f.previous || isDoc(f.previous))) || isNewTest(f))) {
    if (lines <= SMALL_CHANGE_LINES) return result("auto", [`prose or new-test-only change of ${lines} lines`], type);
    return result("agent", [`prose or new-test-only but ${lines} lines, over the ${SMALL_CHANGE_LINES}-line auto limit`], type);
  }
  if (ends.some((p) => TEST_TREE.test(p)) && files.every((f) => isDoc(f.path) || TEST_TREE.test(f.path) || TEST_TREE.test(f.previous ?? ""))) {
    return result("agent", ["changes existing tests or test support code (conftest, mocks, data, setup, a deletion or a rename); only brand-new tests are auto"], type);
  }

  return result("agent", ["ordinary change behind the existing tests; no owned path touched"], type);
}

// ── CLI ───────────────────────────────────────────────────────────────────────

const gh = (args) => execFileSync("gh", args, { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });

function prFiles(repo, n) {
  const out = gh([
    "api", "--paginate", `repos/${repo}/pulls/${n}/files`,
    "--jq", ".[] | {path: .filename, previous: .previous_filename, status, additions, deletions, patch}",
  ]);
  return out.split("\n").filter(Boolean).map((l) => JSON.parse(l));
}

/**
 * Who authored each commit on the PR. A commit GitHub cannot attribute to an
 * account is reported as "unattributed", never dropped: dropping it would let it
 * pass for nothing, and it is exactly the commit that is not dependabot's.
 */
function prCommitAuthors(repo, n) {
  const out = gh(["api", "--paginate", `repos/${repo}/pulls/${n}/commits`, "--jq", '.[] | (.author.login // "unattributed")']);
  return out.split("\n").map((l) => l.trim()).filter(Boolean);
}

function repoName() {
  return gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"]).trim();
}

function readCodeowners(path) {
  try {
    return readFileSync(path, "utf8");
  } catch {
    return null;
  }
}

function arg(argv, name) {
  const i = argv.indexOf(name);
  return i === -1 ? null : argv[i + 1];
}

async function main(argv) {
  const codeownersPath = resolve(arg(argv, "--codeowners") ?? ".github/CODEOWNERS");
  const codeowners = readCodeowners(codeownersPath);

  if (arg(argv, "--replay")) {
    const n = Number(arg(argv, "--replay"));
    const repo = arg(argv, "--repo") ?? repoName();
    const prs = JSON.parse(gh(["pr", "list", "--repo", repo, "--state", "merged", "--limit", String(n),
      "--json", "number,title,author,headRefName"]));
    console.log("| PR | author | tier | why | title |\n|---|---|---|---|---|");
    const counts = { auto: 0, agent: 0, owner: 0 };
    for (const pr of prs) {
      const v = classify({
        author: pr.author.login, branch: pr.headRefName, title: pr.title,
        files: prFiles(repo, pr.number), commitAuthors: prCommitAuthors(repo, pr.number),
      }, codeowners);
      counts[v.tier]++;
      console.log(`| #${pr.number} | ${v.authorType} | ${v.tier} | ${v.reasons[0].replace(/\|/g, "/")} | ${pr.title.replace(/\|/g, "/")} |`);
    }
    console.log(`\n${prs.length} PRs: ${counts.auto} auto, ${counts.agent} agent, ${counts.owner} owner (today's CODEOWNERS applied to past PRs)`);
    return 0;
  }

  let pr;
  if (arg(argv, "--input")) {
    pr = JSON.parse(readFileSync(arg(argv, "--input"), "utf8"));
  } else if (arg(argv, "--pr")) {
    const repo = arg(argv, "--repo") ?? repoName();
    const n = arg(argv, "--pr");
    const meta = JSON.parse(gh(["pr", "view", n, "--repo", repo, "--json", "title,author,headRefName"]));
    pr = { author: meta.author.login, branch: meta.headRefName, title: meta.title, files: prFiles(repo, n), commitAuthors: prCommitAuthors(repo, n) };
  } else {
    console.error("usage: risk-tier.mjs --pr <n> [--repo o/r] | --input <file> | --replay <n>   [--codeowners <file>]");
    return 4;
  }
  console.log(JSON.stringify(classify(pr, codeowners)));
  return 0;
}

if (resolve(process.argv[1] ?? "") === resolve(dirname(fileURLToPath(import.meta.url)), "risk-tier.mjs")) {
  process.exit(await main(process.argv.slice(2)));
}
