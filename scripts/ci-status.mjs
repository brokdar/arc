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
//   2 NO_RUNS  no run has ever registered for this head SHA, past the grace
//              window. Nothing is wrong with the code; verify it locally.
//   3 UNKNOWN  still pending at the deadline, or a check was cancelled
//   4 usage / the PR could not be read
//
// Usage:
//   node scripts/ci-status.mjs <pr> [--deadline 480] [--interval 30] [--grace 300]
//   node scripts/ci-status.mjs <pr> --fixture <checks.json> [--runs N] [--head-age S]

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

// ── The pure classifier ──────────────────────────────────────────────────────
// `scripts/ci-status.test.mjs` imports this and proves every branch. Buckets
// come from `gh pr checks --json bucket`: pass | fail | pending | skipping |
// cancel.

export function classify(checks, runCount, headAgeS, graceS) {
  const named = (bs) => checks.filter((c) => bs.includes(c.bucket)).map((c) => c.name);

  if (!checks.length) {
    // Runs exist but have not reported a check yet — that is pending, not absent.
    if (runCount > 0) return { status: "PENDING", detail: `${runCount} run(s) registered, no checks reported yet` };
    if (headAgeS >= graceS) {
      return {
        status: "NO_RUNS",
        detail:
          `no workflow run has registered for this head SHA ${Math.round(headAgeS / 60)} minute(s) after ` +
          `it was pushed — Actions did not start (budget, spending limit, or workflows disabled)`,
      };
    }
    return { status: "PENDING", detail: `no checks yet, ${Math.round(headAgeS)}s after push (grace ${graceS}s)` };
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
    lines.push(`  ${c.bucket.padEnd(8)} ${c.name}${c.bucket === "fail" && c.link ? `  ${c.link}` : ""}`);
  }
  process.stdout.write(lines.join("\n") + "\n");
}

async function main(argv) {
  const positional = argv.filter((a) => !a.startsWith("--"));
  const opt = (name, dflt) => {
    const i = argv.indexOf(`--${name}`);
    return i === -1 ? dflt : argv[i + 1];
  };
  const pr = positional[0];
  if (!pr) {
    process.stderr.write("usage: ci-status.mjs <pr> [--deadline 480] [--interval 30] [--grace 300]\n");
    return EXIT.USAGE;
  }
  const deadlineS = Number(opt("deadline", 480));
  const intervalS = Number(opt("interval", 30));
  const graceS = Number(opt("grace", 300));

  // Fixture mode: one pass over canned data, no network, no sleeping. This is
  // what makes the classifier's wiring testable rather than only its logic.
  const fixture = opt("fixture", null);
  if (fixture) {
    const checks = JSON.parse(readFileSync(fixture, "utf8"));
    const verdict = classify(checks, Number(opt("runs", 0)), Number(opt("head-age", 9999)), graceS);
    report(pr, null, verdict, checks);
    return EXIT[verdict.status === "PENDING" ? "UNKNOWN" : verdict.status];
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

  for (;;) {
    const c = fetchChecks(pr);
    if (!c.ok) {
      process.stderr.write(`gh pr checks failed: ${c.err}\n`);
      return EXIT.UNKNOWN;
    }
    checks = c.checks;
    const runCount = checks.length ? 1 : (fetchRunCount(head.headRefOid) ?? 0);
    const elapsedS = (Date.now() - startedMs) / 1000;
    verdict = classify(checks, runCount, headAgeS + elapsedS, graceS);
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
  return EXIT[verdict.status];
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  main(process.argv.slice(2)).then((code) => process.exit(code));
}
