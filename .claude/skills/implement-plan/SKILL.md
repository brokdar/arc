---
name: implement-plan
description: >
  Build a feature plan's pull requests — each in its own worktree, developed TDD
  against its acceptance criteria and edge cases, gated by an independent
  reviewer, then pushed as its own PR with CI driven green. PRs others depend on
  auto-merge so the next group can start; leaf PRs stay open for you. Use when
  asked to implement or build out a plan, or ship a feature's PRs. (To write the
  plan first, that is `feature-plan`.)
argument-hint: "[<slug>-plan.md] [branch to build | verify]"
allowed-tools: Read, Bash(git status:*), Bash(git worktree:*), Bash(git log:*), Bash(gh pr list:*), Workflow, TaskOutput
---

# Build a plan's pull requests

This launches the `implement-plan` workflow, which runs the pipeline you cannot run in your head.
Per PR: a fresh developer implements it TDD in its own worktree, a **separate agent with no write
tools** judges the diff against that PR's acceptance criteria and their edge cases, a **third** agent
fixes only what was rejected, and the review repeats — bounded at two cycles, then a hard stop with
no PR opened. Only then is it committed, pushed and opened, and only then is CI driven green.

No agent ever reviews or fixes its own work. That is structural: every seat is a fresh context, and
`arc-reviewer` has `tools: Read, Bash, Glob, Grep` — it *cannot* edit what it finds.

## Before you launch

1. **There must be a plan.** This skill executes one; it does not write one. If `$ARGUMENTS` names no
   plan, ask for the path — or point at `/feature-plan` if they want one written first.
2. **Build the args as a JSON object yourself.** Never pass `$ARGUMENTS` through as a string. The
   script parses a bare string as a safety net, but the blast radius of a mis-parse here is real PRs
   on a real remote.
   - `plan` (**required**) — `<slug>-plan.md` in the repository root.
   - `onlyBranch: "feat/…"` — build one PR only. This is the re-entry after a hard stop.
   - `verifyFeature: true` — implement nothing; verify the feature-scoped criteria against `main`.
     Requires every PR merged.
   - `autoMerge: false` — never merge anything, even a prerequisite. The DAG then stops after the
     first group and waits for you.
3. **Check the tree.** `git status` in the main checkout and `git worktree list`. Each PR lives in
   `.claude/worktrees/<branch>`; a hard-stopped earlier run leaves partial work there, so read the
   previous run's `recovery` line before re-launching the same branch.
4. **Say which PRs will be built, and that some will auto-merge**, then launch. This one touches the
   remote and `main`.

## Launch

```
Workflow({
  scriptPath: ".claude/workflows/implement-plan.js",
  args: { plan: "wellness-plan.md" }
})
```

It runs in the background; `/workflows` shows live progress. The first line logged is
`Launch: raw=… parsed=… plan=…`, followed by the derived groups — for anything narrower than the
whole plan, a quick non-blocking `TaskOutput` a few seconds in confirms the scope.

## What merges itself, and what waits for you

Concurrency is **derived from each PR's `Depends:`**, never hand-numbered. A PR that others depend on
blocks the whole graph, so once it is review-approved and CI-verified it is squash-merged
automatically and the workflow waits for the merge to land before cutting the next group from `main`.
A **leaf** PR — nothing depends on it — is never merged; it stays open for you to read.

If a prerequisite will not merge, the workflow stops rather than build the next group off a `main`
that lacks what it needs.

When the last PR is merged, launch once more with `verifyFeature: true`. That pass is not
bookkeeping: per-PR review structurally cannot see cross-PR gaps — duplicated work, one surface
contradicting another, a capability that reached the API but not MCP. It runs against integrated
`main` and is the only gate that catches them.

## When CI has no budget

`gh pr checks` reporting no runs, jobs that never start, or a spending-limit error is classified
`NO_BUDGET` — deliberately distinct from `RED`, because nothing is wrong with the code and sending a
fix agent to hunt a defect that does not exist wastes a cycle. The workflow instead verifies the PR
locally against everything CI would have run (`just check`, `just test-int`, `just e2e`, `just smoke`,
schemathesis where the PR touched a route or schema) and posts one comment on the PR recording the
commit SHA and each check as PASS / FAIL / **NOT_RUN with the reason**.

That honesty matters more than the coverage. `just smoke` needs `E2E_PASSWORD` to match the bcrypt
hash in `.env`; without it the login step fails and the check is recorded NOT_RUN, never passed. A
comment standing in for CI on a public PR that claims coverage it did not achieve is worse than no
comment. If you have the password, pass it in the environment before launching so smoke can run.

These checks are Docker-bound, so they are deferred out of the concurrent batch and run one at a
time — `test-int` and `smoke` bind fixed host ports and share one compose project name across every
checkout.

## After it returns

- **`merged`** — prerequisites that squash-merged. Relay number, URL, title.
- **`open`** — PRs awaiting your review, with `reviewLoops` / `ciLoops` and `ciMode`. A PR that
  needed two review cycles deserves a closer human read than one that passed first time, and
  `ciMode: "local"` means CI never ran and the evidence is the comment on the PR — check its
  `localCi` list for anything NOT_RUN.
- **`stopped`** — each carries a `reason` and a `recovery` line written for the human who decides.
  Relay both verbatim.
  - `review rejected …` — criteria unmet after two fix cycles. **No PR was opened.** Surface `gaps`.
    Do not silently re-launch; this needs a decision about whether the code or the criterion is wrong.
  - `CI RED/UNKNOWN …` — the PR is open but red. CI runs the tier `just check` omits, so this is
    usually a real defect.
  - `local-ci-red` / `local-ci-failed` — the budget-exhausted fallback found a failure, or could not run.
  - `implement-failed` / `pr-failed` / `worktree-setup-failed` — usually transient. Check the
    worktree and `gh pr list` first: partial work left behind must be finished or reset, or the
    re-run builds on top of it.
- **Plan defects caught before any agent ran** — `open-questions`, `no-why`, `bad-pr-title`,
  `positional-label`, `unknown-dependency`, `dependency-cycle`. Fix the plan and re-launch.

Do not re-run a PR that is already open and green. To redo one, close its PR and delete the branch.

## Cleaning up

After PRs merge, their worktrees are stale. `/clean-gone` deletes local branches whose upstream is
gone — after a squash merge, exactly the merged ones — along with their worktrees, confirming first.
