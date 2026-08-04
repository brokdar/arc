# pyrefly-lsp

Local plugin. Registers pyrefly as the Python language server for Claude Code.

The official `pyright-lsp` plugin is deliberately **not** used here: this repo
type-checks with pyrefly (`just typecheck`, the pre-push hook, and CI), and
pyright infers differently. Running both means editor diagnostics that disagree
with the build — errors that CI never reports, and misses that it does.

Needs no extra binary. `uv run --project backend pyrefly lsp` resolves pyrefly
from `backend/.venv`, which `uv sync` in `.devcontainer/startup.sh` already
populates, so the language server and the build always use the same version.

`lspServers` lives in this plugin's `plugin.json`, **not** in the marketplace
entry. Under `strict: true` (the default) `plugin.json` is the authority for
component definitions, and an `lspServers` block in the marketplace entry is
silently ignored — the plugin still lists the server in `claude plugin details`
while `claude --debug` shows it was never loaded. See D21.
