# byor

Build Your Own Rules: a small CLI for customizing ast-grep rules, sharing them
across repositories, and integrating them with AI-agent hooks.

[ast-grep](https://ast-grep.github.io) is the engine. `ast-grep scan` is the
human CLI, and `ast-grep lsp` is the editor integration. BYOR wraps neither:
it arranges plain rule files and plain configuration so the normal ast-grep
tools already know what to do. No daemon, no second rule language, no custom
editor protocol — write an ast-grep rule once and it becomes a diagnostic in
your terminal, your editor, and your AI agent's feedback loop.

## Requirements

- Python 3.11+ (run via [uv](https://docs.astral.sh/uv/): `uvx byor`)

[ast-grep](https://ast-grep.github.io) ships with byor (the `ast-grep-cli`
dependency), so a plain install carries everything. To use a specific build
instead, put `ast-grep` on `PATH` or point `$BYOR_AST_GREP` at it — byor
prefers that over the bundled one.

## Quickstart

```bash
uvx byor init
ast-grep scan
ast-grep lsp
byor add --scope global --edit
byor sync --all
byor agent-check --files src/example.py
```

`init` creates `sgconfig.yml`, the `.byor/` rule directories, and a git ignore
block — after which `ast-grep scan` and `ast-grep lsp` work directly, with no
byor in the loop.

## Rule scopes

| Scope | Lives in | Shared with |
| --- | --- | --- |
| `project` | `.byor/rules/project/` | The team (committed) |
| `local` | `.byor/rules/personal/local/` | You, this repo only |
| `global` | `~/.config/byor/rules/` | You, every registered repo |

Project and local rules override global rules by ID. See
[docs/rules.md](docs/rules.md) for the rule format, metadata, and the
`add`/`edit`/`promote`/`exclude` workflow.

## AI agents

Agents don't just obey your rules — they can capture them. Post-edit hooks
feed `byor agent-check` diagnostics straight back into the agent loop, and
the `byor` skill (installed by `init`) turns durable feedback like "never
do this" into an ast-grep rule: the agent drafts the rule, confirms with one
question, then creates it with `byor add`. See
[docs/ai-agents.md](docs/ai-agents.md).

`byor hook install --agent X --hook-scope project|global` registers a real
post-edit hook; `--scope edit|diff|file` controls which lines a diagnostic
must touch to be reported (hooks default to `edit`, exactly the lines the
agent just changed).

Beyond ast-grep rules, a `checks:` section in `.byor/config.yml` or the
global config runs extra command-line linters (e.g. `ruff`) on the in-scope
files and folds their failures into the same agent feedback. The global config
also carries an `init:` section whose defaults seed `byor init`'s prompts and
`--non-interactive` answers. See [docs/ai-agents.md](docs/ai-agents.md).

| Harness | Skill | Real hook | Payload precision | Registration |
| --- | --- | --- | --- | --- |
| Claude Code | yes | `PostToolUse` | edited lines | project, global, local |
| Codex | yes | `PostToolUse` | apply_patch added lines | project, global |
| Copilot CLI | yes | `postToolUse` | best-effort path | project, global |
| Cursor | yes | `postToolUse` | edited lines | project, global |
| OpenCode | yes | `tool.execute.after` plugin | changed file | project, global |

## Why copies, not symlinks

Global rules are canonical in `~/.config/byor/rules/` and copied into each
repo's `.byor/rules/personal/global/`. Copies, because ast-grep follows a
`ruleDirs` entry that is itself a symlink but does not load symlinked files or
symlinked child directories inside a rule directory, and `ruleDirs` does not
accept globs. Plain `ast-grep scan` and `ast-grep lsp` need plain files in
plain `ruleDirs`, so BYOR copies. The cost is duplication; the benefit is
compatibility.

Stale copies are self-healing, not prevented by user discipline: every byor
command syncs the current repo first, and `byor sync --all` heals every
registered repo. See [docs/sync-model.md](docs/sync-model.md).

## Continuous integration

Project rules are tracked plain files, so CI gates on them with zero byor
installed — a fresh clone already carries everything `ast-grep scan` needs.
Install ast-grep, then scan with `--error` so warning severities fail the
build (a plain `ast-grep scan` exits 0 on warnings):

```yaml
- run: npm install -g @ast-grep/cli
- run: ast-grep scan --error
```

See [docs/sync-model.md](docs/sync-model.md) for the full workflow file and
why a fresh clone needs no byor.

## Commands

```text
byor init           Initialize BYOR in a repository
byor sync           Mirror enabled global rules into the repository
byor doctor         Validate installation health
byor add            Create a new rule in a scope
byor edit           Open an existing rule in $EDITOR
byor remove         Delete a rule from its scope
byor promote        Move a personal rule into shared project rules
byor exclude        Disable a global rule in this repository
byor include        Re-enable a previously excluded global rule
byor list           Show rules and where they come from
byor agent-check    Run ast-grep on changed files and render agent feedback
byor hook           Install or uninstall AI agent integrations
```

Every command takes `--help`, and repo-operating commands take `--repo PATH`
(default: search upward from the current directory).

## Documentation

- [docs/rules.md](docs/rules.md) — rule format, scopes, and the rule workflow
- [docs/ai-agents.md](docs/ai-agents.md) — AI agent integration and `agent-check`
- [docs/sync-model.md](docs/sync-model.md) — why copies, self-healing, git hooks
