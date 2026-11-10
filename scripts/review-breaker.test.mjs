#!/usr/bin/env node
// Unit tests for scripts/review-breaker.mjs.
//
// WHY THIS EXISTS. The breaker is the only thing that stops agents merging after
// they have started getting it wrong, and both of its failure modes are silent: a
// breaker that never trips is no breaker, and one that trips on noise gets its
// issue closed unread until nobody believes it. It is a pure function over a list
// of merges, so every rule is a case here — including the ORDER rule: "later"
// means nearer the head of main, not a later timestamp, because commit dates come
// from several machines and the dev host's clock steps backwards
// (.claude/rules/clock-monotonicity.md).
//
// Run: node scripts/review-breaker.test.mjs

import {
  LABEL, MIN_SAMPLE, TRIP_RATIO, WINDOW, act, assess, evidenceOf, hasNewEvidence, isFix, isRevert, issueBody, loadMerges, reverts,
} from "./review-breaker.mjs";

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

const DAY = 24 * 60 * 60 * 1000;
const T0 = Date.parse("2026-11-01T12:00:00Z");
let n = 100;
/** A merge. Newest-first lists are built by the caller; `daysAgo` only sets the timestamp. */
const merge = (subject, files, over = {}) => {
  n += 1;
  return { number: String(n), sha: n.toString(16).padStart(40, "a"), subject: `${subject} (#${n})`, body: "", at: T0, tier: "agent", authorType: "agent", files, ...over };
};
const at = (m, daysFromT0) => ({ ...m, at: T0 + daysFromT0 * DAY });

group("what counts as a revert or a fix");
{
  ok("GitHub's default revert title", isRevert('Revert "feat(x): y" (#5)'));
  ok("a conventional revert", isRevert("revert: put the old loop back (#5)"));
  ok("…with a scope", isRevert("revert(api): the 405 change"));
  ok("a feature is not a revert", !isRevert("feat(x): revert the default to 5"));
  ok("a fix", isFix("fix(api): a 405 names every method"));
  ok("a breaking fix", isFix("fix!: x"));
  ok("a feature is not a fix", !isFix("feat(x): fix the thing"));
  ok("a test is not a fix", !isFix("test(x): fix flaky"));
}

group("a revert names the merge it undoes");
{
  const m = { number: "42", sha: "abcdef0123456789".padEnd(40, "0"), subject: "feat(sessions): correctable notes (#42)" };
  ok("by PR number", reverts({ subject: "revert: notes", body: "Reverts #42" }, m));
  ok("by PR number in the title", reverts({ subject: "revert: #42", body: "" }, m));
  ok("a different PR number does not match", !reverts({ subject: "revert: x", body: "Reverts #421" }, m));
  ok("…nor a prefix of one", !reverts({ subject: "revert: x", body: "Reverts #4" }, m));
  ok("by short commit hash", reverts({ subject: "revert: x", body: "This reverts commit abcdef0." }, m));
  ok("by the full hash", reverts({ subject: "revert: x", body: `This reverts commit ${m.sha}` }, m));
  ok("by the reverted title", reverts({ subject: 'Revert "feat(sessions): correctable notes"', body: "" }, m));
  ok("a revert of something else does not match", !reverts({ subject: "revert: put the old loop back", body: "" }, m));
  ok("a very short title is not matched by substring", !reverts({ subject: "revert: the fix thing", body: "" }, { ...m, subject: "fix: it (#42)" }));
}

group("bad merges: followed within 7 days by a revert, or by a fix to the same files");
{
  const feat = merge("feat(a): the thing", ["backend/app/a.py"]);
  const revert = merge("revert: the thing", ["backend/app/a.py"], { body: `Reverts #${feat.number}` });
  const v = assess([revert, feat]);
  ok("a feature followed by a revert naming it is bad", v.bad === 1 && v.considered === 1, JSON.stringify(v));
  ok("the revert itself is not a candidate", v.considered === 1);

  const fix = merge("fix(a): the thing was wrong", ["backend/app/a.py", "backend/tests/x.py"]);
  ok("followed by a fix touching the same file is bad", assess([fix, feat]).bad === 1);
  ok("…a fix is evidence about other merges, not a candidate itself", assess([fix, feat]).considered === 1);
  const unrelated = merge("fix(b): something else", ["backend/app/b.py"]);
  ok("a fix to unrelated files is NOT bad", assess([unrelated, feat]).bad === 0);
  const featFix = merge("feat(c): another", ["backend/app/a.py"]);
  ok("a feature touching the same file is not a fix", assess([featFix, feat]).bad === 0);
  const rename = merge("fix(a): moved", ["backend/app/b.py", "backend/app/a.py"]);
  ok("a fix counts through a rename's old path", assess([rename, feat]).bad === 1);
}

group("the 7-day window");
{
  const feat = at(merge("feat(a): x", ["a.py"]), 0);
  const fixIn = at(merge("fix(a): y", ["a.py"]), 7);
  const fixLate = at(merge("fix(a): y", ["a.py"]), 7.01);
  ok("a fix exactly 7 days later counts", assess([fixIn, feat]).bad === 1);
  ok("a fix just past 7 days does not", assess([fixLate, feat]).bad === 0);
}

group("order is position on main, never the timestamp");
{
  // The clock stepped back: the FIX is nearer the head of main but carries an EARLIER timestamp.
  const feat = at(merge("feat(a): x", ["a.py"]), 3);
  const fix = at(merge("fix(a): y", ["a.py"]), 3 - 0.0000046);
  ok("a fix stamped slightly BEFORE its feature, but after it on main, still counts", assess([fix, feat]).bad === 1);
  // The reverse: an OLDER merge (further down main) with a LATER timestamp is not a follow-up.
  const older = at(merge("fix(a): y", ["a.py"]), 5);
  const newer = at(merge("feat(a): x", ["a.py"]), 4);
  ok("an older merge stamped later is not a follow-up of a newer one", assess([newer, older]).bad === 0);
}

group("lanes: only auto and agent merges are evidence about agent judgement");
{
  const owner = merge("feat(a): x", ["a.py"], { tier: "owner" });
  const fix = merge("fix(a): y", ["a.py"]);
  ok("an owner-lane merge is never a candidate", assess([fix, owner]).considered === 0 && assess([fix, owner]).bad === 0);
  const auto = merge("chore(deps): bump", ["a.py"], { tier: "auto" });
  ok("an auto-lane merge is", assess([fix, auto]).bad === 1);
}

group("when it trips");
{
  const bad = (i) => {
    const feat = merge(`feat(f${i}): x`, [`f${i}.py`]);
    return [merge(`fix(f${i}): y`, [`f${i}.py`]), feat]; // newest first: the fix, then its feature
  };
  const clean = (i) => merge(`feat(c${i}): x`, [`c${i}.py`]);
  const show = (v) => JSON.stringify({ ...v, details: undefined });

  const tripped = assess([...bad(1), ...bad(2), ...bad(3), clean(1), clean(2)]);
  ok(`${MIN_SAMPLE}+ merges with at least ${TRIP_RATIO * 100}% bad trips`, tripped.tripped && tripped.bad === 3 && tripped.considered === 5, show(tripped));
  const below = assess([...bad(1), clean(1), clean(2), clean(3), clean(4)]);
  ok("a minority bad does not", !below.tripped && below.bad === 1 && below.considered === 5, show(below));
  const half = assess([...bad(1), ...bad(2), clean(1), clean(2), clean(3), clean(4)]);
  ok("just under the ratio does not", !half.tripped && half.bad === 2, show(half));
  const small = assess([...bad(1), ...bad(2), ...bad(3), ...bad(4)]);
  ok("fewer than MIN_SAMPLE merges never trips, however bad", !small.tripped && small.bad === 4 && small.considered === 4, show(small));
  ok("no merges at all does not trip", !assess([]).tripped && assess([]).considered === 0);
  ok("the verdict names what it found", tripped.details.length === tripped.bad && tripped.details[0].followups.length > 0);
}

group("the window");
{
  const old = Array.from({ length: 10 }, (_, i) => merge(`feat(o${i}): x`, ["o.py"]));
  const feat = merge("feat(a): x", ["a.py"]);
  const fix = merge("fix(a): y", ["a.py"]);
  // 20 newer, clean merges push the feature out of the window: it is not examined.
  const newer = Array.from({ length: WINDOW }, (_, i) => merge(`feat(n${i}): x`, [`n${i}.py`]));
  const v = assess([...newer, fix, feat, ...old]);
  ok(`only the newest ${WINDOW} merges are examined`, v.considered === WINDOW && v.bad === 0, JSON.stringify({ ...v, details: undefined }));
  ok("a smaller window is honoured", assess([...newer, fix, feat], { window: 3 }).considered === 3);
}

group("whose merges are judged");
{
  const human = merge("feat(a): x", ["a.py"], { authorType: "human" });
  const fix = merge("fix(a): y", ["a.py"]);
  ok("the operator's own merge is not judged", assess([fix, human]).considered === 0);
  const dep = merge("chore(deps): bump", ["a.py"], { authorType: "dependabot", tier: "auto" });
  ok("a dependabot merge is", assess([fix, dep]).bad === 1);
  const agentFeat = merge("feat(a): x", ["a.py"]);
  const humanFix = merge("fix(a): y", ["a.py"], { authorType: "human" });
  ok("but anyone's fix is evidence against an agent's merge", assess([humanFix, agentFeat]).bad === 1);
  const humanRevert = merge("revert: x", ["a.py"], { authorType: "human", body: `Reverts #${agentFeat.number}` });
  ok("…and so is anyone's revert", assess([humanRevert, agentFeat]).bad === 1);
}

group("closing the issue sticks: file only on NEW evidence");
{
  const feat = (i) => merge(`feat(f${i}): x`, [`f${i}.py`]);
  const bad = (i) => { const f = feat(i); return [merge(`fix(f${i}): y`, [`f${i}.py`]), f]; };
  const verdict = assess([...bad(1), ...bad(2), ...bad(3), feat(8), feat(9)]);
  ok("the fixture trips", verdict.tripped, JSON.stringify({ ...verdict, details: undefined }));
  const ids = evidenceOf(verdict);
  ok("evidence is the bad merges' PR numbers", ids.length === 3 && ids.every((x) => /^\d+$/.test(x)), ids.join(","));
  ok("the issue body carries them as a marker", issueBody(verdict).includes(`<!-- breaker-evidence: ${ids.join(",")} -->`));
  ok("the body says closing it re-enables merging", /Close this issue to re-enable merging/.test(issueBody(verdict)));
  ok("never tripped before: new evidence", hasNewEvidence(verdict, null));
  ok("the last closed issue listed the same merges: NOT new", !hasNewEvidence(verdict, issueBody(verdict)));
  ok("…one more bad merge since: new", hasNewEvidence(verdict, issueBody({ ...verdict, details: verdict.details.slice(1) })));
  ok("a closed issue with no marker counts as new evidence", hasNewEvidence(verdict, "closed by hand, no marker"));
}

group("act(): what the daily run does, through a fake gh");
{
  const tripped = assess([...[1, 2, 3].flatMap((i) => { const f = merge(`feat(t${i}): x`, [`t${i}.py`]); return [merge(`fix(t${i}): y`, [`t${i}.py`]), f]; }), merge("feat(a): x", ["a.py"]), merge("feat(b): y", ["b.py"])]);
  const calm = assess([merge("feat(a): x", ["a.py"])]);
  const fake = ({ open = [], closed = [], armed = [] } = {}) => {
    const calls = [];
    const run = (args) => {
      calls.push(args.join(" "));
      if (args[0] === "issue" && args[1] === "list") return JSON.stringify(args.includes("open") ? open : closed);
      if (args[0] === "pr" && args[1] === "list") return JSON.stringify(armed);
      return "";
    };
    return { run, calls };
  };
  const did = (calls, what) => calls.filter((c) => c.startsWith(what));

  let f = fake();
  let out = act(tripped, "o/r", f.run);
  ok("tripped, nothing open, never closed before: files an issue", out.includes("opened issue") && did(f.calls, "issue create").length === 1, out.join("|"));
  ok("…labelled", did(f.calls, "issue create")[0].includes(`--label ${LABEL}`));
  ok("…and creates the label first", did(f.calls, "label create").length === 1);

  f = fake({ open: [{ number: 12 }] });
  out = act(tripped, "o/r", f.run);
  ok("an issue is already open: files nothing", did(f.calls, "issue create").length === 0 && out[0] === "issue #12 already open", out.join("|"));

  f = fake({ closed: [{ number: 9, body: issueBody(tripped) }] });
  out = act(tripped, "o/r", f.run);
  ok("closed by the operator, same evidence: does NOT file again", did(f.calls, "issue create").length === 0 && /not filing again/.test(out[0]), out.join("|"));

  f = fake({ closed: [{ number: 9, body: "<!-- breaker-evidence: 1 -->" }] });
  out = act(tripped, "o/r", f.run);
  ok("closed, but there is new evidence: files again", did(f.calls, "issue create").length === 1, out.join("|"));

  f = fake();
  out = act(calm, "o/r", f.run);
  ok("not tripped, nothing open: does nothing at all", out.length === 0 && did(f.calls, "issue create").length === 0 && did(f.calls, "pr merge").length === 0, out.join("|"));

  // Armed auto-merges land with no further `gh pr merge` call, so pausing has to disarm them.
  f = fake({ armed: [{ number: 7, autoMergeRequest: { mergeMethod: "SQUASH" } }, { number: 8, autoMergeRequest: null }] });
  out = act(tripped, "o/r", f.run);
  ok("tripping disarms an armed auto-merge", did(f.calls, "pr merge").length === 1 && did(f.calls, "pr merge")[0] === "pr merge 7 --repo o/r --disable-auto", f.calls.join(" ; "));
  ok("…and leaves a PR that is not armed alone", !f.calls.some((c) => c.startsWith("pr merge 8")));
  f = fake({ open: [{ number: 12 }], armed: [{ number: 7, autoMergeRequest: { mergeMethod: "SQUASH" } }] });
  act(calm, "o/r", f.run);
  ok("an issue that is still open keeps disarming, even when not tripped today", did(f.calls, "pr merge").length === 1);
  f = fake({ armed: [{ number: 7, autoMergeRequest: { mergeMethod: "SQUASH" } }] });
  act(calm, "o/r", f.run);
  ok("not paused: armed auto-merges are left alone", did(f.calls, "pr merge").length === 0);
  f = fake({ closed: [{ number: 9, body: issueBody(tripped) }], armed: [{ number: 7, autoMergeRequest: { mergeMethod: "SQUASH" } }] });
  act(tripped, "o/r", f.run);
  ok("after the operator closed it (same evidence) nothing is disarmed", did(f.calls, "pr merge").length === 0);
}

group("loadMerges(): reading main through a fake gh");
{
  const commit = (sha, message, login, date = "2026-11-05T10:00:00Z") => ({ sha, commit: { message, committer: { date } }, author: login === null ? null : { login } });
  const owners = "/justfile @brokdar\n";
  const file = (path, over = {}) => JSON.stringify({ path, previous: null, status: "modified", additions: 3, deletions: 1, patch: "@@", ...over });
  const commits = [
    commit("a".repeat(40), "feat(x): a thing (#41)\n\nbody line", "brokdar-agent[bot]"),
    commit("b".repeat(40), "fix: no pr number", null),
    commit("c".repeat(40), "chore(deps): bump (#39)", "dependabot[bot]"),
  ];
  const filesBySha = {
    ["a".repeat(40)]: [file("backend/app/x.py"), file("justfile")].join("\n"),
    ["b".repeat(40)]: file("docs/a.md", { previous: "docs/b.md", status: "renamed" }),
    ["c".repeat(40)]: file("backend/uv.lock"),
  };
  const seen = [];
  const run = (args) => {
    seen.push(args.join(" "));
    if (args[1].startsWith("repos/o/r/commits?")) return JSON.stringify(commits);
    const sha = /commits\/([0-9a-f]{40})/.exec(args.join(" "))[1];
    return filesBySha[sha];
  };
  const merges = loadMerges("o/r", 3, owners, run);
  ok("one entry per commit, newest first as given", merges.length === 3 && merges[0].sha.startsWith("aaa"));
  ok("the PR number comes from the subject", merges[0].number === "41" && merges[1].number === null && merges[2].number === "39");
  ok("the body is the rest of the message", merges[0].body === "\nbody line");
  ok("an owned path makes the merge owner lane", merges[0].tier === "owner");
  ok("the author type comes from the commit author", merges[0].authorType === "agent" && merges[2].authorType === "dependabot");
  ok("a commit with no author is human, never silently an agent", merges[1].authorType === "human");
  ok("both ends of a rename are in the file list", merges[1].files.includes("docs/a.md") && merges[1].files.includes("docs/b.md"));
  ok("every commit's files are fetched with --paginate", seen.filter((c) => c.includes("--paginate")).length === 3);
  ok("the timestamp is a number", Number.isFinite(merges[0].at));
}

console.log(`\n  ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
