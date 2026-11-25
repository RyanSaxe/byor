# Examples

Starter rules, command rules, and check scripts showing what byor enforces
beyond standard linters: library choices, packaging boundaries, design-craft
nudges, architectural seams, framework compiler constraints, and shell safety
gates. Copy what fits; nothing here is enabled by default.

Every rule and command rule in this directory is tested in CI against its
`valid:` and `invalid:` fixtures:

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
(see [Authoring rules](../docs/guides/authoring-rules.md)) targeting conventions
standard linters like Ruff and oxlint do not enforce:

| Rule | Demonstrates |
| --- | --- |
| [`python.no-requests`](rules/python.no-requests.yml) | Banning a library at import choke points (`dotted_name` + from-import patterns) to standardize on `httpx` |
| [`python.no-sys-path-mutation`](rules/python.no-sys-path-mutation.yml) | Blocking `sys.path.append`/`insert`/`extend`/`+=` packaging hacks |
| [`python.question-monkeypatch`](rules/python.question-monkeypatch.yml) | Design-craft nudge (`severity: info`) asking agents to justify `monkeypatch.setattr`/`delattr` while allowing `setenv`/`delenv`/`chdir` |
| [`ts.no-direct-env-access`](rules/ts.no-direct-env-access.yml) | Architectural boundary routing `process.env` reads through a typed `env` config module (`language: TypeScript`) |
| [`react.no-try-finally-jsx`](rules/react.no-try-finally-jsx.yml) | Relational AST rule (`all`, `has`, `inside`) catching `try ... finally` inside TSX components for React Compiler (`language: Tsx`) |

Matching fixtures live in [`rule-tests/`](rule-tests/).

## Command rules library

Command rules use `language: Bash` to steer shell commands in the pre-command
gate. In a repository they live under `.byor/commands/` (never in `sgconfig.yml`
`ruleDirs`). Fixtures live in [`command-rule-tests/`](command-rule-tests/) and
run with the same `ast-grep test` command above.

| Rule | Demonstrates |
| --- | --- |
| [`no-force-push`](command-rules/no-force-push.yml) | Relational `has` with anchored regex matching `--force`/`-f` at any position while keeping `--force-with-lease` legal |
| [`no-pip-install`](command-rules/no-pip-install.yml) | `any` covering `pip`, `pip3`, and `python -m pip` while allowing `uv pip install` |
| [`no-commit-no-verify`](command-rules/no-commit-no-verify.yml) | Blocking `git commit --no-verify`, `-n`, and `SKIP=` / `PREK_SKIP=` hook bypasses without false-positives on `git log -n` or quoted strings |

## Global config and check scripts

Starter global configuration and check scripts under [`config/`](config/):

| File | Purpose |
| --- | --- |
| [`config/config.yml`](config/config.yml) | Annotated `~/.config/byor/config.yml` with read-only linter forwarding (`gate: false`), `command_checks`, `output.concise: true`, `paths.commands`, `init.packages`, and a sample profile |
| [`config/scripts/ruff-readonly.sh`](config/scripts/ruff-readonly.sh) | Read-only Ruff check + format check script (`gate: false`) that reports lint and formatting issues without mutating files underneath an active agent |
| [`config/scripts/dependency-gate.sh`](config/scripts/dependency-gate.sh) | Agent-only check (`gate: false`) that fails when `pyproject.toml` dependencies change from `HEAD` |
| [`config/scripts/check-suppression-reason.py`](config/scripts/check-suppression-reason.py) | Cross-language craft script (`gate: false`) requiring a short reason on newly added `# noqa`, `# type: ignore`, `// oxlint-disable`, `@ts-*`, or `ast-grep-ignore` suppressions |
| [`config/scripts/protect-ssh.sh`](config/scripts/protect-ssh.sh) | `command_check` script using `$HOME` expansion to block commands targeting `~/.ssh` |
