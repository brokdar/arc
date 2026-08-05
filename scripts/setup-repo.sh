#!/usr/bin/env bash
# Apply the GitHub repository settings that template repositories can NOT
# copy (they only copy files): branch rulesets, merge settings, Actions
# permissions. Requires: gh auth login with admin rights on the repo.
set -euo pipefail

REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
echo "Configuring $REPO..."

# squash_merge_commit_title/message are load-bearing, not cosmetic (D19): the
# squashed commit's subject becomes the changelog entry, so it must come from
# the PR title that `pr-title.yml` lints, and its body from the curated PR
# description rather than a bullet dump of branch commits. GitHub rejects
# PR_BODY unless the title is PR_TITLE.
echo "Setting merge strategy (squash only, PR title/body, auto-delete branches)..."
gh api -X PATCH "repos/$REPO" \
  -f allow_merge_commit=false \
  -f allow_rebase_merge=false \
  -f allow_squash_merge=true \
  -f squash_merge_commit_title=PR_TITLE \
  -f squash_merge_commit_message=PR_BODY \
  -f delete_branch_on_merge=true \
  -f allow_auto_merge=true >/dev/null

# The required "pr-title" context is the JOB NAME in
# .github/workflows/pr-title.yml (`name: pr-title`), not the workflow name.
# Renaming that job silently drops the requirement — change both together.
#
# Note this POSTs a new ruleset: if `protect-main` already exists it is skipped,
# not updated. To change an existing one, PATCH it by id
# (`gh api repos/$REPO/rulesets` to find it) or delete it first.
echo "Creating branch ruleset for main..."
gh api -X POST "repos/$REPO/rulesets" --input - >/dev/null <<'JSON' || \
  echo "  (ruleset may already exist — skipping, NOT updating)"
{
  "name": "protect-main",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] }
  },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": true,
        "allowed_merge_methods": ["squash"]
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "do_not_enforce_on_create": false,
        "strict_required_status_checks_policy": false,
        "required_status_checks": [{ "context": "pr-title" }]
      }
    }
  ]
}
JSON

echo "Enabling secret scanning with push protection..."
gh api -X PATCH "repos/$REPO" --input - >/dev/null <<'JSON' || \
  echo "  (requires a public repo or GitHub Advanced Security — skipped)"
{
  "security_and_analysis": {
    "secret_scanning": { "status": "enabled" },
    "secret_scanning_push_protection": { "status": "enabled" }
  }
}
JSON

echo "Restricting default GITHUB_TOKEN permissions to read-only..."
gh api -X PUT "repos/$REPO/actions/permissions/workflow" \
  -f default_workflow_permissions=read \
  -F can_approve_pull_request_reviews=false >/dev/null

echo "✅ Repository configured."
