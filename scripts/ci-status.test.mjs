#!/usr/bin/env node
// Unit tests for scripts/ci-status.mjs.
//
// WHY THIS EXISTS. This script decides whether a fix agent is dispatched, whether
// a prerequisite may merge, and whether the expensive local-verification fallback
// runs. Each branch is a different hour of machine time, and the distinction that
// matters most — RED (a real defect) versus NO_RUNS (nothing wrong, CI never
// started) — was previously inferred by a model and got it right only after 45
// shell calls. Here it is a pure function with cases.
//
// Run: node scripts/ci-status.test.mjs

import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { EXIT, classify, exitFor } from "./ci-status.mjs";

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

const check = (name, bucket, link = "") => ({ name, bucket, state: bucket.toUpperCase(), link });
const GRACE = 300;

group("green");
{
  const v = classify([check("check", "pass"), check("integration", "pass")], 1, { zeroForS: 600, headAgeS: 600, graceS: GRACE });
  ok("all passing is GREEN", v.status === "GREEN", JSON.stringify(v));
  ok("the count is reported", v.detail.includes("2 check(s) passed"));

  const s = classify([check("check", "pass"), check("e2e", "skipping")], 1, { zeroForS: 600, headAgeS: 600, graceS: GRACE });
  ok("a skipped check does not block green", s.status === "GREEN", JSON.stringify(s));
  ok("but it is named, so a skip is never invisible", s.detail.includes("skipped (e2e)"), s.detail);
}

group("red — a real defect, and the fix agent needs the name");
{
  const v = classify([check("check", "pass"), check("fuzz", "fail", "https://x/run/1")], 1, { zeroForS: 600, headAgeS: 600, graceS: GRACE });
  ok("one failure is RED", v.status === "RED");
  ok("only the failing name is listed", v.failing.join() === "fuzz");
  ok("the detail names it", v.detail.includes("fuzz"));
  ok(
    "a failure outranks a pending sibling",
    classify([check("a", "fail"), check("b", "pending")], 1, { zeroForS: 600, headAgeS: 600, graceS: GRACE }).status === "RED",
  );
}

group("no runs — the budget case, distinguished from red");
{
  const v = classify([], 0, { zeroForS: 600, headAgeS: 600, graceS: GRACE });
  ok("no checks and no runs past grace is NO_RUNS", v.status === "NO_RUNS", JSON.stringify(v));
  ok("the detail says what was observed, not what was guessed", v.detail.includes("no workflow run has registered"));
  ok(
    "inside the grace window it is still PENDING, not NO_RUNS",
    classify([], 0, { zeroForS: 120, headAgeS: 120, graceS: GRACE }).status === "PENDING",
  );
  ok(
    "a registered run with no checks yet is PENDING, not NO_RUNS",
    classify([], 2, { zeroForS: 0, headAgeS: 9999, graceS: GRACE }).status === "PENDING",
  );
  ok("grace is a boundary, not a range", classify([], 0, { zeroForS: GRACE, headAgeS: GRACE, graceS: GRACE }).status === "NO_RUNS");
}

group("pending and unknown");
{
  ok("a pending check is PENDING", classify([check("a", "pending")], 1, { zeroForS: 600, headAgeS: 600, graceS: GRACE }).status === "PENDING");
  const c = classify([check("a", "pass"), check("b", "cancel")], 1, { zeroForS: 600, headAgeS: 600, graceS: GRACE });
  ok("a cancelled check is UNKNOWN, never RED", c.status === "UNKNOWN", JSON.stringify(c));
  ok("and it is named so a human can look", c.failing.join() === "b");
}

group("a failed run count is not zero runs");
{
  // `null` means "the API call failed", and collapsing that to 0 fabricated the
  // one verdict that has no retry path and routes to the Docker-bound local
  // verification.
  const v = classify([], null, { zeroForS: 9999, headAgeS: 9999, graceS: GRACE });
  ok("an uncountable run count stays PENDING", v.status === "PENDING", JSON.stringify(v));
  ok("and says so", /could not count/.test(v.detail));
}

group("NO_RUNS needs a window this invocation actually observed");
{
  // The commit is made by the gate seat and pushed after a review, so the head is
  // routinely 10–40 minutes old here: a grace window measured from the commit date
  // had always already expired, and the first poll concluded NO_RUNS.
  ok(
    "an old head alone does not conclude NO_RUNS on the first observation",
    classify([], 0, { zeroForS: 0, headAgeS: 3600, graceS: GRACE }).status === "PENDING",
  );
  ok(
    "a short observation of an old head does",
    classify([], 0, { zeroForS: 90, headAgeS: 3600, graceS: GRACE }).status === "NO_RUNS",
  );
  ok(
    "a fresh head needs the full grace window",
    classify([], 0, { zeroForS: 90, headAgeS: 90, graceS: GRACE }).status === "PENDING",
  );
  ok(
    "…and gets NO_RUNS once it has it",
    classify([], 0, { zeroForS: 301, headAgeS: 301, graceS: GRACE }).status === "NO_RUNS",
  );
  ok("defaults are safe when obs is omitted", classify([], 0).status === "PENDING");
}

group("an unrecognised check state is never green");
{
  const v = classify([check("check", "pass"), { name: "weird", bucket: "neutral", state: "NEUTRAL" }], 1, { zeroForS: 0, headAgeS: 600, graceS: GRACE });
  ok("it is UNKNOWN, not GREEN", v.status === "UNKNOWN", JSON.stringify(v));
  ok("and names the state it did not understand", /weird=neutral/.test(v.detail));
  ok(
    "a missing bucket is caught too",
    classify([{ name: "x" }], 1, { zeroForS: 0, headAgeS: 600, graceS: GRACE }).status === "UNKNOWN",
  );
}

group("exitFor is the single mapping both paths use");
{
  ok("PENDING exits as UNKNOWN", exitFor("PENDING") === EXIT.UNKNOWN);
  for (const st of ["GREEN", "RED", "NO_RUNS", "UNKNOWN"]) ok(`${st} maps to its own code`, exitFor(st) === EXIT[st]);
}

group("exit codes are the interface");
{
  ok("GREEN is 0", EXIT.GREEN === 0);
  ok("RED is 1", EXIT.RED === 1);
  ok("NO_RUNS is 2", EXIT.NO_RUNS === 2);
  ok("UNKNOWN is 3", EXIT.UNKNOWN === 3);
}

group("CLI wiring, via --fixture (no network, no sleeping)");
{
  const dir = mkdtempSync(join(tmpdir(), "ci-status-"));
  const script = join(here, "ci-status.mjs");
  const call = (checks, extra = []) => {
    const f = join(dir, `checks-${Math.abs(JSON.stringify(checks).length)}-${extra.join("")}.json`);
    writeFileSync(f, JSON.stringify(checks));
    try {
      return {
        code: 0,
        out: execFileSync("node", [script, "99", "--fixture", f, ...extra], {
          encoding: "utf8",
          stdio: ["ignore", "pipe", "pipe"],
        }),
      };
    } catch (e) {
      return { code: e.status, out: e.stdout || "", err: e.stderr || "" };
    }
  };

  const green = call([check("check", "pass")]);
  ok("green exits 0", green.code === 0, JSON.stringify(green));
  ok("the report leads with the status line", /STATUS GREEN/.test(green.out), green.out);

  const red = call([check("fuzz", "fail", "https://x/run/1")]);
  ok("red exits 1", red.code === 1);
  ok("the failing check's log URL is printed for the fix agent", red.out.includes("https://x/run/1"));

  ok("no checks and no runs exits 2", call([], ["--runs", "0"]).code === 2);
  ok("no checks with a run registered exits 3, not 2", call([], ["--runs", "1"]).code === 3);
  ok("a pending check exits 3", call([check("a", "pending")]).code === 3);
  ok("a cancelled check exits 3", call([check("a", "cancel")]).code === 3);

  try {
    execFileSync("node", [script], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
    ok("no argument exits 4", false, "it exited 0");
  } catch (e) {
    ok("no argument exits 4", e.status === 4);
  }

  // A non-numeric or missing option value used to become NaN, and every
  // comparison with NaN is false — so the poll loop never terminated. The
  // 155-minute hang this script exists to prevent, reintroduced by a typo.
  const bad = (args) => {
    try {
      execFileSync("node", [script, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 10_000 });
      return 0;
    } catch (e) {
      return e.status === null ? "TIMED OUT" : e.status;
    }
  };
  for (const args of [
    ["55", "--deadline"],
    ["55", "--deadline", "8m"],
    ["55", "--deadline", "--interval", "30"],
    ["55", "--interval", "abc"],
    ["55", "--grace", "0"],
    ["55", "--deadline", "-5"],
  ]) {
    ok(`refuses ${args.slice(1).join(" ") || "(nothing)"} with exit 4`, bad(args) === 4, String(bad(args)));
  }

  // A crash must not read as RED (exit 1), which dispatches a fix agent.
  const f = join(dir, "not-json.json");
  writeFileSync(f, "{{{ not json");
  ok("a malformed fixture exits 4, not 1", bad(["55", "--fixture", f]) === 4);
  ok("a missing fixture exits 4, not 1", bad(["55", "--fixture", join(dir, "nope.json")]) === 4);

  // An option value is not the PR number.
  const okRun = (args) => {
    try {
      return { code: 0, out: execFileSync("node", [script, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }) };
    } catch (e) {
      return { code: e.status, out: e.stdout || "" };
    }
  };
  const g = join(dir, "green.json");
  writeFileSync(g, JSON.stringify([check("check", "pass")]));
  const r2 = okRun(["--fixture", g, "--deadline", "480", "55"]);
  ok("the PR is read past an option value", /PR #55/.test(r2.out), r2.out);
  const empty = join(dir, "empty.json");
  writeFileSync(empty, "[]");
  const uncountable = okRun(["55", "--fixture", empty, "--runs", "null"]);
  ok("an uncountable run count exits 3, not 2", uncountable.code === 3, JSON.stringify(uncountable));
  ok("  …and says so rather than claiming no runs", /could not count/.test(uncountable.out), uncountable.out);
  const notSettled = okRun(["55", "--fixture", empty, "--runs", "0", "--zero-for", "10", "--head-age", "10"]);
  ok("zero runs but no observed window exits 3, not 2", notSettled.code === 3, JSON.stringify(notSettled));
}

console.log(`\n  ${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
