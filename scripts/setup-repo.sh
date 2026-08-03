#!/usr/bin/env bash
# Apply the GitHub repository settings that template repositories can NOT
# copy (they only copy files): branch rulesets, merge settings, Actions
# permissions. Requires: gh auth login with admin rights on the repo.
set -euo pipefail

REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
echo "Configuring $REPO..."

echo "Setting merge strategy (squash only, auto-delete branches)..."
gh api -X PATCH "repos/$REPO" \
  -f allow_merge_commit=false \
  -f allow_rebase_merge=false \
  -f allow_squash_merge=true \
  -f delete_branch_on_merge=true \
  -f allow_auto_merge=true >/dev/null

echo "Creating branch ruleset for main..."
gh api -X POST "repos/$REPO/rulesets" --input - >/dev/null <<'JSON' || \
  echo "  (ruleset may already exist — skipping)"
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
