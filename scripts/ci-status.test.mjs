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

import { EXIT, classify } from "./ci-status.mjs";

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
  const v = classify([check("check", "pass"), check("integration", "pass")], 1, 600, GRACE);
  ok("all passing is GREEN", v.status === "GREEN", JSON.stringify(v));
  ok("the count is reported", v.detail.includes("2 check(s) passed"));

  const s = classify([check("check", "pass"), check("e2e", "skipping")], 1, 600, GRACE);
  ok("a skipped check does not block green", s.status === "GREEN", JSON.stringify(s));
  ok("but it is named, so a skip is never invisible", s.detail.includes("skipped (e2e)"), s.detail);
}

group("red — a real defect, and the fix agent needs the name");
{
  const v = classify([check("check", "pass"), check("fuzz", "fail", "https://x/run/1")], 1, 600, GRACE);
  ok("one failure is RED", v.status === "RED");
  ok("only the failing name is listed", v.failing.join() === "fuzz");
  ok("the detail names it", v.detail.includes("fuzz"));
  ok(
    "a failure outranks a pending sibling",
    classify([check("a", "fail"), check("b", "pending")], 1, 600, GRACE).status === "RED",
  );
}

group("no runs — the budget case, distinguished from red");
{
  const v = classify([], 0, 600, GRACE);
  ok("no checks and no runs past grace is NO_RUNS", v.status === "NO_RUNS", JSON.stringify(v));
  ok("the detail says what was observed, not what was guessed", v.detail.includes("no workflow run has registered"));
  ok(
    "inside the grace window it is still PENDING, not NO_RUNS",
    classify([], 0, 120, GRACE).status === "PENDING",
  );
  ok(
    "a registered run with no checks yet is PENDING, not NO_RUNS",
    classify([], 2, 9999, GRACE).status === "PENDING",
  );
  ok("grace is a boundary, not a range", classify([], 0, GRACE, GRACE).status === "NO_RUNS");
}

group("pending and unknown");
{
  ok("a pending check is PENDING", classify([check("a", "pending")], 1, 600, GRACE).status === "PENDING");
  const c = classify([check("a", "pass"), check("b", "cancel")], 1, 600, GRACE);
  ok("a cancelled check is UNKNOWN, never RED", c.status === "UNKNOWN", JSON.stringify(c));
  ok("and it is named so a human can look", c.failing.join() === "b");
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
}

console.log(`\n  ${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
