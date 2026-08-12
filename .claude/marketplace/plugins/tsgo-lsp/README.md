# tsgo-lsp

Local plugin. Registers `tsgo` as the TypeScript/JavaScript language server for
Claude Code.

The official `typescript-lsp` plugin is deliberately **not** used here: it runs
`typescript-language-server`, which drives **tsserver** from the TypeScript 5.9
workspace package, while this repo type-checks with `tsgo` (`bun run
type-check`, `just typecheck`, the pre-push hook, and CI). Two
compilers means editor diagnostics that disagree with the build. Same reasoning
as `pyrefly-lsp`, applied to the other language.

It also could not work as installed: `typescript-language-server` starts at the
repo root, where there is no `node_modules`, and exits with "Could not find a
valid TypeScript installation" — TypeScript lives in `frontend/node_modules`.

Needs no extra binary. `frontend/node_modules/.bin/tsgo` comes from
`@typescript/native-preview`, already a devDependency, so the language server
and the build are the same compiler at the same version. `--lsp` is the native
compiler's own language-server mode; `workspaceFolder` points at `frontend/` so
the server picks up `frontend/tsconfig.json`.

## What this does and does not deliver

**Code intelligence works**: go-to-definition, find-references, hover, document
and workspace symbols, implementations, call hierarchy. Verified against
`frontend/lib/api/client.ts`.

**Post-edit diagnostics do not arrive.** tsgo implements LSP 3.17 *pull*
diagnostics — it advertises `diagnosticProvider` and answers
`textDocument/diagnostic` on request — while Claude Code registers a
`textDocument/publishDiagnostics` handler and consumes *push* only (confirmed in
`claude --debug`, v2.1.221). No handler, no diagnostics. `pyrefly` pushes, which
is why the Python side surfaces type errors after an edit and this one doesn't.

That gap is not worth closing by swapping in `typescript-language-server`: it
would push diagnostics, but from tsserver on TypeScript 5.9 — a different
compiler from the one that gates CI, which is exactly the drift this plugin
exists to prevent. TypeScript type errors are caught by `bun run type-check`
(pre-push hook and CI). Recheck when either tsgo starts pushing or Claude Code
learns to pull.

## Start Claude Code from the repo root

`${CLAUDE_PROJECT_DIR}` is the directory Claude Code was started in, not the git
root, and `.claude/settings.json` is only applied when the session starts at the
repo root. Started from `frontend/` or `backend/`, the configured paths point one
level too deep. That is a hard failure by design — the launcher `cd`s first and
the spawn dies on the missing directory — rather than a language server quietly
answering for the wrong project.

The quiet version is what this replaced. With the official `typescript-lsp`
installed and enabled at user scope, a subdirectory session loaded it instead
(the first server registered for an extension wins) and it answered from a
tsserver with no `tsconfig.json` in scope: `const apiClient: any` where the real
type is `Client<paths, ...>`. Wrong, and silent about it. `typescript-lsp` and
`pyright-lsp` are uninstalled here; if either is reinstalled and enabled at user
scope it will contend for `.ts` and `.py` again, and the way to settle it is
`claude plugin disable <name>`, not a change in this repo.

## Caveat

tsgo reads the same generated files `bun run type-check` does, including
`.next/types/`. A stale `.next` (the devcontainer keeps it in a named volume)
therefore misleads the language server too. `rm -rf frontend/.next` clears it.
