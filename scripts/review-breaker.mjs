#!/usr/bin/env node
// Circuit breaker for agent merges: if too many recent merges had to be undone
// or patched straight away, stop agents merging until the operator says so.
//
// WHAT COUNTS AS BAD. Of the last WINDOW merges to main, take those in the auto
// or agent lane (scripts/risk-tier.mjs) AND written by an agent or dependabot:
// owner-lane merges were approved by the operator, and the operator's own PRs are
// not evidence about agent judgement (a lane is decided by the paths touched, not
// by who wrote the change, so authorship is checked separately). Until 2026-11-10
// agents pushed as the operator's login, so earlier history is attributed to the
// operator and does not count. A merge M is BAD if,
// within FOLLOWUP_DAYS of it, a later merge either
//   · is a revert that names M (its `#N`, M's commit hash, or M's title), or
//   · is a `fix(...)` that touches at least one file M touched.
// M is a merge that is not itself a revert or a fix. A fix to the same files is the usual sign that M shipped something wrong; a fix
// to unrelated files is not, so it is not counted. This is a heuristic: it will
// count a fix that was a coincidence and miss a revert that names nothing. The
// threshold is set from a MEASURED baseline (see TRIP_RATIO), not from a guess.
//
// ORDER IS POSITION, NOT TIME. "Later" means nearer the head of main, not a later
// timestamp: commit dates come from several machines and the WSL2 dev host's
// clock steps backwards (.claude/rules/clock-monotonicity.md). Timestamps decide
// only the coarse "within 7 days".
//
// WHAT TRIPPING DOES. It opens an issue labelled `automerge-breaker`. While one is
// open, `scripts/as-agent.sh` refuses `gh pr merge` (exit 4), so no agent merges,
// and every PR that already has auto-merge armed is disarmed, since GitHub would
// otherwise land those with no further `gh pr merge` call. Closing the issue is how
// the operator re-enables merging. It is an issue and not a repository variable
// because the workflow's GITHUB_TOKEN cannot write variables; a token that could
// would be a secret to manage for one boolean.
//
// CLOSING IT MUST STICK. The window is the last 20 merges, and while agents are
// paused it only moves when the operator merges something, so the same bad merges
// would trip the breaker again the next morning and reopen the issue just closed.
// So it files only on NEW evidence: a bad merge that the most recently closed
// breaker issue did not list. Each issue carries its evidence as a marker comment.
//
// Usage:
//   node scripts/review-breaker.mjs [--repo o/r] [--window 20] [--open-issue]
// Prints {"tripped", "bad", "considered", ...} as JSON; --open-issue also files the
// issue when tripped and none is open. Exit 0 whatever the verdict; 4 on usage.

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { classify } from "./risk-tier.mjs";

export const WINDOW = 20;
export const FOLLOWUP_DAYS = 7;
/**
 * It trips when at least MIN_SAMPLE agent-lane merges are in the window and at least
 * TRIP_RATIO of them were bad. Measured on this repository's history on 2026-11-10:
 * 0 bad of 4 agent- or dependabot-authored merges in the last 20, 0 of 11 over 40. That
 * is a thin baseline, because agents only got their own identity that day; before the
 * authorship filter the heuristic flagged 1 of 5 over 20 (#112 then #115, a coincidental
 * file overlap) and 1 of 13 over 40, which is its measured false-positive rate on
 * ordinary PRs, and there were no reverts on main at all. 0.5 is far above both: it
 * means most recent agent merges went wrong. It is an estimate, and the first real
 * trip is the evidence for moving it. A fixed COUNT would not do: only a fraction of
 * merges are in the agent lane, so a count high enough to avoid noise never fires.
 */
export const MIN_SAMPLE = 5;
export const TRIP_RATIO = 0.5;
export const LABEL = "automerge-breaker";

const DAY_MS = 24 * 60 * 60 * 1000;
export const isRevert = (s) => /^(revert "|revert(\([^)]*\))?!?:)/i.test(s);
export const isFix = (s) => /^fix(\([^)]*\))?!?:/i.test(s);
const title = (s) => s.replace(/^\w+(\([^)]*\))?!?:\s*/, "").replace(/\s*\(#\d+\)\s*$/, "").toLowerCase();

/** Does revert `r` name merge `m`? By PR number, by commit hash, or by M's title. */
export function reverts(r, m) {
  const text = `${r.subject}\n${r.body ?? ""}`;
  if (m.number && new RegExp(`#${m.number}\\b`).test(text)) return true;
  if (m.sha && new RegExp(`\\b${m.sha.slice(0, 7)}[0-9a-f]*\\b`).test(text)) return true;
  const t = title(m.subject);
  return t.length >= 12 && text.toLowerCase().includes(t);
}

/**
 * @param merges newest FIRST, each {number, sha, subject, body, at (ms), tier, files: string[]}
 * @returns {tripped, bad, considered, window, minSample, ratio, details}
 */
export function assess(merges, { window = WINDOW, days = FOLLOWUP_DAYS, minSample = MIN_SAMPLE, ratio = TRIP_RATIO } = {}) {
  const recent = merges.slice(0, window);
  const details = [];
  let considered = 0;
  recent.forEach((m, i) => {
    // Reverts and fixes are the EVIDENCE about other merges, not merges to judge: if a
    // fix counted as a candidate, every feature-plus-fix pair would score 1 bad of 2
    // and the ratio could never reach what an all-bad history deserves.
    if (m.tier === "owner" || isRevert(m.subject) || isFix(m.subject)) return;
    // Anyone's revert or fix is evidence, but only an agent's or dependabot's merge is judged.
    if (!["agent", "dependabot"].includes(m.authorType)) return;
    considered++;
    const mine = new Set(m.files);
    // Later = nearer the head of main = a smaller index. Time only bounds the window.
    const followups = recent.slice(0, i).filter((f) => {
      if (f.at - m.at > days * DAY_MS) return false;
      if (isRevert(f.subject)) return reverts(f, m);
      return isFix(f.subject) && f.files.some((p) => mine.has(p));
    });
    if (followups.length) {
      details.push({ merge: `#${m.number} ${m.subject}`, followups: followups.map((f) => `#${f.number} ${f.subject}`) });
    }
  });
  const tripped = considered >= minSample && details.length / considered >= ratio;
  return { tripped, bad: details.length, considered, window, minSample, ratio, details };
}

// ── CLI ───────────────────────────────────────────────────────────────────────

const gh = (args) => execFileSync("gh", args, { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
const arg = (argv, name) => {
  const i = argv.indexOf(name);
  return i === -1 ? null : argv[i + 1];
};

/** Read the newest `window` commits on main with what the breaker needs about each. */
export function loadMerges(repo, window, codeowners, run = gh) {
  const commits = JSON.parse(run(["api", `repos/${repo}/commits?sha=main&per_page=${window}`]));
  return commits.map((c) => {
    const [subject, ...rest] = c.commit.message.split("\n");
    // A commit's file list is paginated at 300 files; a squash can be larger, and an
    // owned path further down must not be missed.
    const files = run([
      "api", "--paginate", `repos/${repo}/commits/${c.sha}`,
      "--jq", ".files[] | {path: .filename, previous: .previous_filename, status, additions, deletions, patch}",
    ]).split("\n").filter(Boolean).map((l) => JSON.parse(l));
    const login = c.author?.login ?? "unattributed";
    const verdict = classify({ author: login, branch: "", title: subject, files, commitAuthors: [login] }, codeowners);
    return {
      number: /\(#(\d+)\)\s*$/.exec(subject)?.[1] ?? null,
      sha: c.sha,
      subject,
      body: rest.join("\n"),
      at: Date.parse(c.commit.committer.date),
      tier: verdict.tier,
      authorType: verdict.authorType,
      files: files.flatMap((f) => [f.path, f.previous].filter(Boolean)),
    };
  });
}

const MARKER = /<!--\s*breaker-evidence:\s*([0-9,\s]*)-->/;

/** The PR numbers of the merges a verdict calls bad, from "#N subject". */
export const evidenceOf = (verdict) => verdict.details.map((d) => /^#(\d+)/.exec(d.merge)?.[1]).filter(Boolean);

/** Is there a bad merge that the most recently closed breaker issue did not already list? */
export function hasNewEvidence(verdict, lastClosedBody) {
  if (lastClosedBody == null) return true; // never tripped before
  const listed = new Set((MARKER.exec(lastClosedBody)?.[1] ?? "").split(",").map((x) => x.trim()).filter(Boolean));
  return evidenceOf(verdict).some((n) => !listed.has(n));
}

export function issueBody(verdict) {
  const lines = verdict.details.map((d) => `- ${d.merge}, followed by ${d.followups.join(", ")}`);
  return [
    `The merge breaker tripped: ${verdict.bad} of ${verdict.considered} agent-lane merges in the last ${verdict.window} on main were followed within ${FOLLOWUP_DAYS} days by a revert or a fix to the same files (it trips at ${Math.round(verdict.ratio * 100)}% with at least ${verdict.minSample} such merges).`,
    "",
    ...lines,
    "",
    "While this issue is open, `scripts/as-agent.sh` refuses `gh pr merge`, so no agent merges, and every PR with auto-merge armed is disarmed. **Close this issue to re-enable merging.** It will not be reopened unless a merge that is not listed here goes bad. See docs/review/rollback.md for undoing a bad merge.",
    "",
    "This is a heuristic (scripts/review-breaker.mjs): a fix to the same files can be a coincidence.",
    "",
    `<!-- breaker-evidence: ${evidenceOf(verdict).join(",")} -->`,
  ].join("\n");
}

/**
 * What to do about a verdict: file an issue if it tripped on NEW evidence, and while
 * paused (an issue open, or just filed) disarm every armed auto-merge. Returns what
 * it did, so a test can see it. `run` is the gh runner.
 */
export function act(verdict, repo, run = gh) {
  const did = [];
  const open = JSON.parse(run(["issue", "list", "--repo", repo, "--label", LABEL, "--state", "open", "--json", "number"]));
  let paused = open.length > 0;
  if (verdict.tripped && !open.length) {
    const closed = JSON.parse(run(["issue", "list", "--repo", repo, "--label", LABEL, "--state", "closed", "--limit", "1", "--json", "number,body,closedAt"]));
    if (hasNewEvidence(verdict, closed[0]?.body ?? null)) {
      run(["label", "create", LABEL, "--repo", repo, "--force", "--color", "b60205", "--description", "Agent merging is paused until this is closed"]);
      run(["issue", "create", "--repo", repo, "--label", LABEL, "--title", "Agent merging paused: too many recent merges were undone or patched", "--body", issueBody(verdict)]);
      did.push("opened issue");
      paused = true;
    } else {
      did.push("tripped, but only on merges the last closed issue already listed; not filing again");
    }
  } else if (open.length) {
    did.push(`issue #${open[0].number} already open`);
  }
  if (paused) {
    const armed = JSON.parse(run(["pr", "list", "--repo", repo, "--state", "open", "--json", "number,autoMergeRequest"]))
      .filter((pr) => pr.autoMergeRequest);
    for (const pr of armed) {
      run(["pr", "merge", String(pr.number), "--repo", repo, "--disable-auto"]);
      did.push(`disarmed auto-merge on #${pr.number}`);
    }
  }
  return did;
}

function main(argv) {
  const repo = arg(argv, "--repo") ?? gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"]).trim();
  const window = Number(arg(argv, "--window") ?? WINDOW);
  if (!Number.isInteger(window) || window < 1) {
    console.error("usage: review-breaker.mjs [--repo o/r] [--window n] [--open-issue]");
    return 4;
  }
  const codeowners = readFileSync(resolve(arg(argv, "--codeowners") ?? ".github/CODEOWNERS"), "utf8");
  const verdict = assess(loadMerges(repo, window, codeowners), { window });
  console.log(JSON.stringify(verdict, null, 2));
  if (argv.includes("--open-issue")) for (const line of act(verdict, repo)) console.error(line);
  return 0;
}

if (resolve(process.argv[1] ?? "") === resolve(dirname(fileURLToPath(import.meta.url)), "review-breaker.mjs")) {
  process.exit(main(process.argv.slice(2)));
}
