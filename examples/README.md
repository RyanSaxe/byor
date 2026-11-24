# Examples

Starter rules, command rules, and check scripts demonstrating byor mechanics.
Copy what fits; nothing here is enabled by default.

Every rule in this directory is tested in CI against its `valid:` and `invalid:`
fixtures:

```sh
ast-grep test -c examples/sgconfig.yml --skip-snapshot-tests
```

## Real-world personal setup

The files in this folder are intentionally small, self-contained teaching
examples. For a full production setup across many repositories, see
[RyanSaxe/dotfiles `config/byor`](https://github.com/RyanSaxe/dotfiles/tree/main/config/byor):

- **Global layer (`config.yml`, `commands/`, `scripts/`):** concise agent output
  (`output.concise: true`), private init defaults, seven Bash command rules plus
  `guard-commands.py` for high-blast-radius safety checks, and
  `run-repo-linters.py`, a read-only multi-language post-edit linter runner
  (Python, JS/TS, Markdown, Go, Lua, Shell, Nix) with a 5-second per-tool
  timeout.
- **Committed repository layer:** project-specific linters and `ast-grep` rules
  run in `prek` and CI.
- **Opt-in packages (`packages/`):** `craft` (seven cross-language design-craft
  checks for module headers, thin docstrings, routing wrappers, file length,
  directory sprawl, suppression reasons, and manifest dependencies), `python`
  (PEP 723 script check, `sys.path` mutation ban, `monkeypatch` prompt), and
  `web` (JS/TS/TSX mock prompts and React Compiler `try ... finally` rules).

## Rules library

Each file is an ordinary `ast-grep` YAML rule with `metadata.byor.agent_prompt`
(see [Authoring rules](../docs/guides/authoring-rules.md)). Ordered from
simplest pattern to relational rules:

| Rule | Demonstrates |
| --- | --- |
| [`no-print`](rules/no-print.yml) | Bare `pattern` with a variadic metavariable (`$$$ARGS`) |
| [`no-requests`](rules/no-requests.yml) | Banning a library at import choke points (`dotted_name` + from-import patterns) |
| [`python.no-typing-cast`](rules/python.no-typing-cast.yml) | `any` combining import and call patterns with named metavariables (`$TYPE`, `$VALUE`) |
| [`no-routing-functions`](rules/no-routing-functions.yml) | Relational rules (`any`/`all`, `inside`, `follows`, `nthChild` with `reverse`) matching pass-through wrappers |
| [`keyword-only-args`](rules/keyword-only-args.yml) | Composed `utils` rules counting positional parameters while exempting `self`/`cls` and `*`/`*args` |
| [`no-console-log`](rules/no-console-log.yml) | Bare `pattern` in TypeScript (`language: typescript`) |
| [`no-type-suppression`](rules/no-type-suppression.yml) | Combining two node kinds (`cast(...)` calls and `# type: ignore` comments) in one rule |

Matching fixtures live in [`rule-tests/`](rule-tests/).

## Command rules library

Command rules use `language: Bash` to steer shell commands in the pre-command
gate. In a repository they live under `.byor/commands/` (never in `sgconfig.yml`
`ruleDirs`). Fixtures live in [`command-rule-tests/`](command-rule-tests/) and
run with the same `ast-grep test` command above.

| Rule | Demonstrates |
| --- | --- |
| [`prefer-rg`](command-rules/prefer-rg.yml) | Bare pattern matching `grep` inside pipelines or compound commands without matching quoted strings |
| [`no-pip-install`](command-rules/no-pip-install.yml) | `any` covering `pip`, `pip3`, and `python -m pip` while allowing `uv pip install` |
| [`no-force-push`](command-rules/no-force-push.yml) | Relational `has` with anchored regex matching `--force`/`-f` at any position while keeping `--force-with-lease` legal |

## Global config and check scripts

Starter global configuration and check scripts under [`config/`](config/):

| File | Purpose |
| --- | --- |
| [`config/config.yml`](config/config.yml) | Annotated `~/.config/byor/config.yml` with `checks`, `command_checks`, `output`, `worktrees`, and a sample profile |
| [`config/scripts/ruff.sh`](config/scripts/ruff.sh) | Autofix-then-report check script that tells the agent what changed and scans the whole repo when given no args |
| [`config/scripts/dependency-gate.sh`](config/scripts/dependency-gate.sh) | Agent-only check (`gate: false`) that fails when `pyproject.toml` dependencies change from `HEAD` |
| [`config/scripts/uv-lock-guard.sh`](config/scripts/uv-lock-guard.sh) | Agent-only check (`gate: false`) rejecting hand edits to `uv.lock` |
| [`config/scripts/protect-ssh.sh`](config/scripts/protect-ssh.sh) | `command_check` script using `$HOME` expansion to block commands targeting `~/.ssh` |
