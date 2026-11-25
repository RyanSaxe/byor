# Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). byor bundles
`ast-grep-cli`; `--with-executables-from ast-grep-cli` also puts `ast-grep` on
`PATH` for `ast-grep scan` and
[`ast-grep lsp`](https://ast-grep.github.io/guide/tools/editors.html).

## 1. Install on your machine

```sh
uv tool install byor --with-executables-from ast-grep-cli
byor install
```

`byor install` writes `~/sgconfig.yml` (so global rules work in uninitialized
repos), installs hooks or plugins for your chosen agents plus the `byor` skill,
and records selections in `~/.local/state/byor/state.yml`.

Select agents non-interactively or change them later:

```sh
byor install --agents claude-code,codex
byor hook install --agent copilot
byor hook uninstall --agent copilot
```

Supported `--agent` names: `claude-code`, `codex`, `copilot`, `antigravity`,
`opencode`, `pi`, and `skill`. In Codex, run `/hooks` once after installing or
updating hooks to trust them. See [Harnesses](../reference/harnesses.md).

## 2. Onboard through your agent (optional)

Open your coding agent and say "set up byor". The bundled skill checks health
with `byor doctor`, offers to run `byor init`, and can import mechanically
checkable preferences from `CLAUDE.md`, `AGENTS.md`, or
`copilot-instructions.md` without editing those files unless asked.

## 3. Initialize a repository

Global rules work in any repository immediately after `byor install`. Run
`byor init` inside a repo (never in `$HOME`) when you want project rules, local
exclusions, opt-in packages, or a blocking CI gate:

```sh
cd /path/to/repo
byor init
```

Non-interactive variants:

```sh
byor init --non-interactive --private               # hide all byor files in .git/info/exclude
byor init --non-interactive --gate                  # promote rules/checks and generate pre-commit + CI gates
byor init --non-interactive --git-hooks             # install post-merge / post-checkout sync shims
byor init --non-interactive --packages craft python # opt into global packages on init
```

## 4. Verify

```sh
byor doctor   # read-only check of machine and repository health
byor list     # active rules and checks with scope origins
```

For a complete personal configuration to browse or adapt, see
[RyanSaxe/dotfiles `config/byor`](https://github.com/RyanSaxe/dotfiles/tree/main/config/byor).

Next: [Architecture](../concepts/architecture.md) or
[Authoring rules](../guides/authoring-rules.md).
