# Install

## Requirements

| Requirement | Purpose |
| --- | --- |
| Python 3.11+ | Required runtime (`>=3.11`) |
| [uv](https://docs.astral.sh/uv/) | Installs `byor` and optional `ast-grep` executables |
| `ast-grep` | Bundled via `ast-grep-cli`; exposed on `PATH` for `ast-grep scan` and `ast-grep lsp` |

## Machine install

Install `byor` once per machine and register your editor and AI agent
integrations:

```sh
uv tool install byor --with-executables-from ast-grep-cli
byor install
```

byor bundles `ast-grep-cli` and locates it automatically. Passing
`--with-executables-from ast-grep-cli` also puts `ast-grep` on your `PATH` so
you can run `ast-grep scan` and editor
[`ast-grep lsp`](https://ast-grep.github.io/guide/tools/editors.html) directly.

`byor install` does three things:

1. Writes `~/sgconfig.yml` pointing at `~/.config/byor/rules/` so global rules
   apply even in repositories that have not run `byor init`.
2. Installs the hooks or plugin for the AI agents you select, plus the
   cross-agent `byor` rule-capture skill.
3. Records your selected agents in `~/.local/state/byor/state.yml`.

To select agents non-interactively, or add and remove one later:

```sh
byor install --agents claude-code,codex
byor hook install --agent copilot
byor hook uninstall --agent copilot
```

Supported agent names: `claude-code`, `codex`, `copilot`, `antigravity`,
`opencode`, `pi`, and `skill`. Codex requires trusting new or updated hooks once
by running `/hooks` inside a Codex session. See
[Harnesses](../reference/harnesses.md) for details.

## Onboard through your agent

After `byor install`, open your coding agent and say "set up byor". The bundled
`byor` skill checks your install with `byor doctor`, offers to run `byor init`
for repository-scoped rules, and can scan existing instruction files
(`CLAUDE.md`, `AGENTS.md`, `copilot-instructions.md`) to propose mechanical
rules for any durable preferences it finds. It never edits your instruction
files unless you ask.

## Initialize a repository

Global rules and checks work in any repository immediately after `byor install`.
Run `byor init` inside a repository when you want committed team rules, local
exclusions, opt-in packages, or a blocking pre-commit and CI gate:

```sh
cd /path/to/repo
byor init
```

Common non-interactive flags:

```sh
byor init --non-interactive --private               # keep all byor files untracked in .git/info/exclude
byor init --non-interactive --gate                  # promote rules/checks and generate pre-commit + CI gates
byor init --non-interactive --git-hooks             # install post-merge / post-checkout sync shims
byor init --non-interactive --packages craft python # opt into global packages on init
```

`byor init` refuses to run in `$HOME` directly because `~/sgconfig.yml` holds
your global rules.

## Verify the setup

```sh
byor doctor        # read-only check of machine and repository health
byor list          # list active rules and checks with their scope origins
```

## Real-world reference

For a complete personal setup you can browse or adapt, see
[RyanSaxe/dotfiles `config/byor`](https://github.com/RyanSaxe/dotfiles/tree/main/config/byor).
It pairs global command safety rules and a read-only multi-language linter
runner with committed per-repo checks and opt-in `craft`, `python`, and `web`
packages.

## Next

Read [Architecture](../concepts/architecture.md) to see how scopes and
self-healing sync work, or jump to
[Authoring rules](../guides/authoring-rules.md) to write your first rule or
check.
