# byolsp

Build Your Own LSP: a small CLI for customizing ast-grep rules, sharing them
across repositories, and integrating them with AI-agent hooks.

[ast-grep](https://ast-grep.github.io) is the engine. `ast-grep scan` is the
human CLI, and `ast-grep lsp` is the editor integration. BYOLSP wraps neither:
it arranges plain rule files and plain configuration so the normal ast-grep
tools already know what to do. No daemon, no second rule language, no custom
editor protocol — write an ast-grep rule once and it becomes a diagnostic in
your terminal, your editor, and your AI agent's feedback loop.

## Requirements

- Python 3.11+ (run via [uv](https://docs.astral.sh/uv/): `uvx byolsp`)
- [ast-grep](https://ast-grep.github.io/guide/quick-start.html) 0.43.0 or
  newer on `PATH` (`brew install ast-grep`), installed separately

## Quickstart

```bash
uvx byolsp init
ast-grep scan
ast-grep lsp
byolsp add --scope global --edit
byolsp sync --all
byolsp agent-check --files src/example.py
```

`init` creates `sgconfig.yml`, the `.byolsp/` rule directories, a git ignore
block, and AI agent instructions — after which `ast-grep scan` and
`ast-grep lsp` work directly, with no byolsp in the loop.

## Rule scopes

| Scope | Lives in | Shared with |
| --- | --- | --- |
| `project` | `.byolsp/rules/project/` | The team (committed) |
| `local` | `.byolsp/rules/personal/local/` | You, this repo only |
| `global` | `~/.config/byolsp/rules/` | You, every registered repo |

Project and local rules override global rules by ID. See
[docs/rules.md](docs/rules.md) for the rule format, metadata, and the
`add`/`edit`/`promote`/`exclude` workflow.

## Why copies, not symlinks

Global rules are canonical in `~/.config/byolsp/rules/` and copied into each
repo's `.byolsp/rules/personal/global/`. Copies, because ast-grep follows a
`ruleDirs` entry that is itself a symlink but does not load symlinked files or
symlinked child directories inside a rule directory, and `ruleDirs` does not
accept globs. Plain `ast-grep scan` and `ast-grep lsp` need plain files in
plain `ruleDirs`, so BYOLSP copies. The cost is duplication; the benefit is
compatibility.

Stale copies are self-healing, not prevented by user discipline: every byolsp
command syncs the current repo first, and `byolsp sync --all` heals every
registered repo. See [docs/sync-model.md](docs/sync-model.md).

## Commands

```text
byolsp init           Initialize BYOLSP in a repository
byolsp sync           Mirror enabled global rules into the repository
byolsp doctor         Validate installation health
byolsp add            Create a new rule in a scope
byolsp edit           Open an existing rule in $EDITOR
byolsp promote        Move a personal rule into shared project rules
byolsp exclude        Disable a global rule in this repository
byolsp include        Re-enable a previously excluded global rule
byolsp list           Show rules and where they come from
byolsp agent-check    Run ast-grep on changed files and render agent feedback
byolsp hook           Install or uninstall AI agent integrations
```

Every command takes `--help`, and repo-operating commands take `--repo PATH`
(default: search upward from the current directory).

## Documentation

- [docs/rules.md](docs/rules.md) — rule format, scopes, and the rule workflow
- [docs/ai-agents.md](docs/ai-agents.md) — AI agent integration and `agent-check`
- [docs/sync-model.md](docs/sync-model.md) — why copies, self-healing, git hooks
