#!/usr/bin/env node
// Classify a pull request's CI: green, red, never-registered, or unknown.
//
// WHY THIS IS A SCRIPT AND NOT AN AGENT'S JUDGEMENT. Two failure modes, both
// observed on 16 Aug 2026:
//
// 1. `gh pr checks --watch` never returns. Run wf_0fddad15 issued it at
//    14:15:33 for PRs #56 and #57; CI finished within ~7 minutes; both tool
//    results were delivered at 16:50:52 — the moment the operator typed into the
//    parent session. 155 of that run's 196 minutes were a finished workflow that
//    did not know it. So: every invocation here is BOUNDED and returns. The
//    caller re-runs it; nothing blocks past a tool timeout.
//
// 2. "The Actions budget is exhausted" was inferred from prose. Run wf_e0fcc017
//    spent 90 turns and 45 shell calls on it and concluded with "no literal
//    billing-error text was retrievable". The observable fact is simpler and
//    needs no billing scope: *zero workflow runs registered for the head SHA,
//    long after it was pushed*. That is one API call.
//
// Exit codes are the interface:
//   0 GREEN    every check that exists concluded, none failed
//   1 RED      at least one check failed — names and log URLs on stdout
//   2 NO_RUNS  no run has ever registered for this head SHA, and this invocation
//              watched that for long enough to say so. Nothing is wrong with the
//              code; verify it locally.
//   3 UNKNOWN  still pending at the deadline, or a check was cancelled
//   4 usage / the PR could not be read
//
// Usage:
//   node scripts/ci-status.mjs <pr> [--deadline 480] [--interval 30] [--grace 300]
//   node scripts/ci-status.mjs <pr> --fixture <checks.json> [--runs N|null] [--zero-for S] [--head-age S]

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

// ── The pure classifier ──────────────────────────────────────────────────────
// `scripts/ci-status.test.mjs` imports this and proves every branch. Buckets
// come from `gh pr checks --json bucket`: pass | fail | pending | skipping |
// cancel.

const KNOWN_BUCKETS = ["pass", "fail", "pending", "skipping", "cancel"];
// The shortest run of consecutive zero-run observations that may conclude NO_RUNS.
// Never zero: the Actions API takes seconds to register a run after a push, and
// this script is called immediately after one.
const MIN_ZERO_OBSERVATION_S = 60;

/**
 * @param checks   what `gh pr checks --json` reported
 * @param runCount workflow runs for the head SHA — `null` means COULD NOT COUNT,
 *                 which is not the same as zero and must never conclude NO_RUNS
 * @param obs      {zeroForS, headAgeS, graceS} — `zeroForS` is how long THIS
 *                 invocation has observed zero runs, which is the only clock the
 *                 script controls. `headAgeS` is secondary evidence: the commit is
 *                 made by the gate seat and pushed after a review, so it is
 *                 routinely 10–40 minutes old by the time this runs and a grace
 *                 window measured from it has always already expired.
 */
export function classify(checks, runCount, obs) {
  const { zeroForS = 0, headAgeS = 0, graceS = 300 } = obs || {};
  const named = (bs) => checks.filter((c) => bs.includes(c.bucket)).map((c) => c.name);

  if (!checks.length) {
    if (runCount === null) {
      return { status: "PENDING", detail: "could not count workflow runs for the head SHA" };
    }
    // Runs exist but have not reported a check yet — that is pending, not absent.
    if (runCount > 0) return { status: "PENDING", detail: `${runCount} run(s) registered, no checks reported yet` };
    const settled = zeroForS >= MIN_ZERO_OBSERVATION_S && (zeroForS >= graceS || headAgeS >= graceS);
    if (settled) {
      return {
        status: "NO_RUNS",
        detail:
          `no workflow run has registered for this head SHA — observed zero for ${Math.round(zeroForS)}s, ` +
          `and the head commit is ${Math.round(headAgeS / 60)} minute(s) old. Actions did not start ` +
          `(budget, spending limit, or workflows disabled)`,
      };
    }
    const need =
      headAgeS >= graceS
        ? `${MIN_ZERO_OBSERVATION_S}s of observation (the head is already older than the ${graceS}s grace)`
        : `${graceS}s of observation, or a head older than ${graceS}s`;
    return {
      status: "PENDING",
      detail: `no checks and no runs yet — observed zero for ${Math.round(zeroForS)}s, need ${need}`,
    };
  }

  // A bucket this script does not know must never fall through to "passed": the
  // GREEN branch below is computed by exclusion, so an unrecognised value used to
  // read as success in a merge gate.
  const strange = checks.filter((c) => !KNOWN_BUCKETS.includes(c.bucket));
  if (strange.length) {
    return {
      status: "UNKNOWN",
      failing: strange.map((c) => c.name),
      detail: `unrecognised check state(s): ${strange.map((c) => `${c.name}=${c.bucket}`).join(", ")}`,
    };
  }

  const failed = named(["fail"]);
  if (failed.length) {
    return {
      status: "RED",
      failing: failed,
      detail: `${failed.length} check(s) failed: ${failed.join(", ")}`,
    };
  }
  // A cancelled check is NOT a defect to hunt — it is usually a superseded run.
  // Reporting it RED would send a fix agent after a bug that does not exist,
  // which is the exact waste the NO_RUNS split exists to avoid.
  const cancelled = named(["cancel"]);
  if (cancelled.length) {
    return { status: "UNKNOWN", failing: cancelled, detail: `check(s) cancelled: ${cancelled.join(", ")}` };
  }
  const pending = named(["pending"]);
  if (pending.length) {
    return { status: "PENDING", detail: `${pending.length} pending: ${pending.join(", ")}` };
  }
  const skipped = named(["skipping"]);
  return {
    status: "GREEN",
    detail:
      `${checks.length - skipped.length} check(s) passed` +
      (skipped.length ? `, ${skipped.length} skipped (${skipped.join(", ")})` : ""),
  };
}

export const EXIT = { GREEN: 0, RED: 1, NO_RUNS: 2, UNKNOWN: 3, USAGE: 4 };
// One mapping, used by both the real path and `--fixture`, so what the tests
// exercise is what ships. PENDING is not an outcome the caller can act on: at the
// deadline it is UNKNOWN.
export const exitFor = (status) => EXIT[status === "PENDING" ? "UNKNOWN" : status];

// ── gh plumbing ──────────────────────────────────────────────────────────────

function gh(args) {
  try {
    return { ok: true, out: execFileSync("gh", args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }) };
  } catch (e) {
    return { ok: false, out: e.stdout || "", err: String((e && e.stderr) || (e && e.message) || e) };
  }
}

/** `gh pr checks` exits non-zero for pending (8), failures (1) and "no checks reported" (1). */
function fetchChecks(pr) {
  const r = gh(["pr", "checks", String(pr), "--json", "name,state,bucket,link,workflow"]);
  const raw = (r.out || "").trim();
  if (!raw) {
    if (!r.ok && /no checks reported/i.test(r.err || "")) return { ok: true, checks: [] };
    return r.ok ? { ok: true, checks: [] } : { ok: false, err: r.err };
  }
  try {
    return { ok: true, checks: JSON.parse(raw) };
  } catch {
    return { ok: false, err: `unparseable JSON from gh pr checks: ${raw.slice(0, 200)}` };
  }
}

/** `null` means "could not count" — deliberately distinct from 0, because 0 is
 * the whole evidence for NO_RUNS and a failed API call must never fabricate it. */
function fetchRunCount(sha) {
  const r = gh(["api", `repos/{owner}/{repo}/actions/runs?head_sha=${sha}&per_page=1`, "--jq", ".total_count"]);
  if (!r.ok) return null;
  const n = Number((r.out || "").trim());
  return Number.isFinite(n) ? n : null;
}

function fetchHead(pr) {
  const r = gh(["pr", "view", String(pr), "--json", "headRefOid,headRefName,state,url"]);
  if (!r.ok) return null;
  try {
    return JSON.parse(r.out);
  } catch {
    return null;
  }
}

function fetchHeadAgeS(sha, nowMs) {
  const r = gh(["api", `repos/{owner}/{repo}/commits/${sha}`, "--jq", ".commit.committer.date"]);
  if (!r.ok) return 0;
  const t = Date.parse((r.out || "").trim());
  return Number.isFinite(t) ? Math.max(0, (nowMs - t) / 1000) : 0;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function report(pr, head, verdict, checks) {
  const lines = [
    `PR #${pr} ${head ? head.url : ""} head ${head ? head.headRefOid.slice(0, 9) : "?"}`,
    `STATUS ${verdict.status} — ${verdict.detail}`,
  ];
  for (const c of checks) {
    lines.push(`  ${String(c.bucket).padEnd(8)} ${c.name}${c.bucket === "fail" && c.link ? `  ${c.link}` : ""}`);
  }
  process.stdout.write(lines.join("\n") + "\n");
}

const USAGE =
  "usage: ci-status.mjs <pr> [--deadline 480] [--interval 30] [--grace 300]\n" +
  "       ci-status.mjs <pr> --fixture <checks.json> [--runs N] [--zero-for S] [--head-age S]\n";

async function main(argv) {
  // An option's VALUE is not a positional argument: `--deadline 480 55` used to
  // poll PR 480. The PR is the first token that is not a flag and not consumed by
  // one.
  const FLAGS_WITH_VALUES = ["deadline", "interval", "grace", "fixture", "runs", "head-age", "zero-for"];
  const positional = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) {
      if (FLAGS_WITH_VALUES.includes(a.slice(2).split("=")[0]) && !a.includes("=")) i++;
      continue;
    }
    positional.push(a);
  }
  const opt = (name, dflt) => {
    const eq = argv.find((a) => a.startsWith(`--${name}=`));
    if (eq) return eq.slice(name.length + 3);
    const i = argv.indexOf(`--${name}`);
    if (i === -1) return dflt;
    const v = argv[i + 1];
    // A flag with no value, or followed by another flag, is a usage error rather
    // than `undefined` → NaN → a comparison that is always false → an UNBOUNDED
    // POLL LOOP. That is the exact defect this script exists to remove.
    if (v === undefined || v.startsWith("--")) return { missing: name };
    return v;
  };
  const num = (name, dflt) => {
    const v = opt(name, dflt);
    if (v && v.missing) return { missing: name };
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? n : { bad: name, value: String(v) };
  };

  const pr = positional[0];
  if (!pr) {
    process.stderr.write(USAGE);
    return EXIT.USAGE;
  }
  const deadlineS = num("deadline", 480);
  const intervalS = num("interval", 30);
  const graceS = num("grace", 300);
  for (const v of [deadlineS, intervalS, graceS]) {
    if (typeof v !== "number") {
      process.stderr.write(
        (v.missing ? `--${v.missing} needs a value\n` : `--${v.bad} must be a positive number, got "${v.value}"\n`) + USAGE,
      );
      return EXIT.USAGE;
    }
  }

  // Fixture mode: one pass over canned data, no network, no sleeping — and it
  // exits through the SAME mapping as the real path, so the tests exercise what
  // ships rather than a parallel copy.
  const fixture = opt("fixture", null);
  if (fixture && !fixture.missing) {
    let checks;
    try {
      checks = JSON.parse(readFileSync(fixture, "utf8"));
    } catch (e) {
      process.stderr.write(`could not read fixture ${fixture}: ${e.message}\n`);
      return EXIT.USAGE;
    }
    const runsOpt = opt("runs", "0");
    const verdict = classify(Array.isArray(checks) ? checks : [], runsOpt === "null" ? null : Number(runsOpt), {
      zeroForS: Number(opt("zero-for", 9999)),
      headAgeS: Number(opt("head-age", 9999)),
      graceS,
    });
    report(pr, null, verdict, Array.isArray(checks) ? checks : []);
    return exitFor(verdict.status);
  }

  const head = fetchHead(pr);
  if (!head) {
    process.stderr.write(`Could not read PR #${pr} with gh. Is it open, and is gh authenticated?\n`);
    return EXIT.USAGE;
  }
  const startedMs = Date.now();
  const headAgeS = fetchHeadAgeS(head.headRefOid, startedMs);
  let verdict = { status: "UNKNOWN", detail: "no observation made" };
  let checks = [];
  // When the first zero-run observation happened, so the grace window is measured
  // against a clock this invocation controls.
  let zeroSinceMs = null;

  for (;;) {
    const c = fetchChecks(pr);
    if (!c.ok) {
      process.stderr.write(`gh pr checks failed: ${c.err}\n`);
      return EXIT.UNKNOWN;
    }
    checks = c.checks;
    // Re-read the head each pass: a push mid-poll otherwise mixes one commit's
    // checks with another commit's run count, and a GREEN could be attributed to
    // a head that is no longer current.
    const now = fetchHead(pr);
    if (now && now.headRefOid !== head.headRefOid) {
      report(pr, head, {
        status: "UNKNOWN",
        detail: `the head moved while polling (${head.headRefOid.slice(0, 9)} → ${now.headRefOid.slice(0, 9)}); re-run against the new head`,
      }, checks);
      return EXIT.UNKNOWN;
    }
    const runCount = checks.length ? 1 : fetchRunCount(head.headRefOid);
    if (runCount === 0) zeroSinceMs = zeroSinceMs ?? Date.now();
    else zeroSinceMs = null;
    const elapsedS = (Date.now() - startedMs) / 1000;
    verdict = classify(checks, runCount, {
      zeroForS: zeroSinceMs === null ? 0 : (Date.now() - zeroSinceMs) / 1000,
      headAgeS: headAgeS + elapsedS,
      graceS,
    });
    if (verdict.status !== "PENDING") break;
    if (elapsedS + intervalS > deadlineS) {
      verdict = {
        status: "UNKNOWN",
        detail: `still pending after ${Math.round(elapsedS)}s (deadline ${deadlineS}s) — ${verdict.detail}`,
      };
      break;
    }
    await sleep(intervalS * 1000);
  }

  report(pr, head, verdict, checks);
  return exitFor(verdict.status);
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  // EXIT.RED is 1, which is also node's exit code for an uncaught throw — so a
  // crash in here used to read as "CI is red" and dispatch a fix agent after a
  // defect that did not exist. Every abnormal exit is a usage error instead.
  main(process.argv.slice(2))
    .then((code) => process.exit(code))
    .catch((e) => {
      process.stderr.write(`ci-status.mjs failed: ${(e && e.stack) || e}\n`);
      process.exit(EXIT.USAGE);
    });
}
