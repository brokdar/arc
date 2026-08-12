# pyrefly-lsp

Local plugin. Registers pyrefly as the Python language server for Claude Code.

The official `pyright-lsp` plugin is deliberately **not** used here: this repo
type-checks with pyrefly (`just typecheck`, the pre-push hook, and CI), and
pyright infers differently. Running both means editor diagnostics that disagree
with the build — errors that CI never reports, and misses that it does.

Needs no extra binary. `uv run --project backend pyrefly lsp` resolves pyrefly
from `backend/.venv`, which `uv sync` in `.devcontainer/startup.sh` already
populates, so the language server and the build always use the same version.

Three mechanics are load-bearing here, and each cost a debugging session:

- **`lspServers` lives in this plugin's `plugin.json`, not in the marketplace
  entry.** Under `strict: true` (the default) `plugin.json` is the authority
  for component definitions, and an `lspServers` block in the marketplace entry
  is silently ignored — the plugin still lists the server in `claude plugin
  details` while `claude --debug` shows it was never loaded. The official
  plugins get away with the marketplace entry because they set `strict: false`.
- **The plugin cache is keyed by version.** Editing this plugin in place
  changes nothing until the version in `plugin.json` is bumped and `claude
  plugin update` runs; until then the stale cached copy keeps loading, and the
  edit looks like it had no effect.
- **`${CLAUDE_PROJECT_DIR}` is the launch directory, not the git root**, and
  `.claude/settings.json` applies only when Claude Code starts at the repo
  root. Started from `frontend/`, a user-scope TypeScript server can claim
  `.ts` before tsgo (first server registered for an extension wins) and answer
  hovers as `any`. Both servers therefore start through `bash -c` wrappers that
  `cd` first, so a subdirectory session fails at spawn instead of silently
  serving the wrong project.

Accepted limitation: tsgo implements LSP 3.17 *pull* diagnostics and Claude Code
consumes *push* only, so TypeScript errors do not appear after an edit the way
pyrefly's do. Navigation is unaffected, and type errors are still caught by
`bun run type-check` in the pre-push hook and CI.
