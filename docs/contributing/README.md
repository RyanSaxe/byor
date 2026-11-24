# Contributing

## Setup

```sh
uv sync --locked
npm ci
prek install
```

Run Python exclusively through `uv run` (`uv add`, `uv sync`), never bare
`python` or `pip`.

## Checks

Run the full local suite before pushing:

```sh
prek run --all-files
uv run --locked scripts/sync_version.py --check
uv run --locked ast-grep scan --error
uv run --locked ast-grep test -c sgconfig.yml --skip-snapshot-tests
uv run --locked ast-grep test -c examples/sgconfig.yml --skip-snapshot-tests
uv run --locked pytest --cov=byor --cov-report=term-missing --cov-fail-under=90
```

| Check | Enforces |
| --- | --- |
| `rumdl` | Markdown linting, 80-character paragraph wrapping, and relative link checks (`.rumdl.toml`) |
| `ast-grep` | Committed project rules under `.byor/rules/project/` |
| `ruff` | Full lint rule set (`select = ["ALL"]` minus explicit ignores in `pyproject.toml`), line length 120, `max-complexity = 8`, `max-positional-args = 2`, formatting |
| `ty` | Static type checking across the project |
| `oxfmt` / `oxlint` | Formatting for JS/TS/JSON/YAML/TOML and type-aware linting of packaged TypeScript plugins |
| `max-file-lines` | Hard 1,000-line cap on source files |
| `uv-lock-check` | Keeps `uv.lock` synchronized with `pyproject.toml` |

## Package layout

| Path | Responsibility |
| --- | --- |
| `src/byor/cli.py` | CLI parser, pre-command self-healing, disabled-repo guard, and dispatch |
| `src/byor/config.py`, `src/byor/config_schema.py` | Repo, local, global, registry, and machine-state loading, validation, and locking |
| `src/byor/commands/` | Subcommand implementations (`init`, `install`, `sync`, `doctor`, `gate`, `rules`, `package`, `profile`, `disable`, `heal`, `listing`) |
| `src/byor/agents/` | Hook installers, payload parsers, and packaged plugin installers (`harness`, `hookconfig`, `opencode`, `pi`, `skill`) |
| `src/byor/data/agents/` | Packaged TypeScript sources (`opencode-plugin.ts`, `pi-extension.ts`, `harness-types.d.ts`) |
| `src/byor/data/skill/` | Bundled cross-agent `byor` skill (`SKILL.md` + `references/`) |
| `src/byor/rules/` | Rule discovery, metadata validation, ID conflict checks, and mirror sync |
| `src/byor/scaffold/` | Ignore blocks, `sgconfig.yml`, git hook shims, worktree copy, pre-commit, and CI generation |
| `src/byor/scan/` | `ast-grep` runner, extra checks, `agent-check`, `command-check`, and line scoping |
| `src/byor/io/` | Atomic YAML/text I/O, locking, git helpers, and path utilities |
| `examples/` | CI-tested starter rules, command rules, and check scripts |
| `tests/` | Pytest suite mirroring `src/byor/`, plus `test_check_scripts.py` for `.byor/scripts/` |

## Conventions

- Follow Conventional Commits (for example, `docs: restructure user guide`).
- Whenever `[project].version` changes in `pyproject.toml`, run
  `uv run --locked scripts/sync_version.py` so `src/byor/__init__.py` matches.
- When adding or editing a rule under `.byor/rules/project/` or `examples/`, add
  matching `valid:` and `invalid:` cases in `.byor/rule-tests/` or
  `examples/rule-tests/`.
- Keep Ubuntu test coverage at or above 90%, preferring real filesystem and CLI
  tests in `tmp_path` sandboxes via `tests/support.py`.
- See [AGENTS.md](../../AGENTS.md) for contributor and release workflow details.
