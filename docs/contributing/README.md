# Contributing

## Setup

```sh
uv sync --locked
npm ci
prek install
```

Run Python only through `uv run` (`uv add`, `uv sync`), never bare `python` or
`pip`.

## Checks

```sh
prek run --all-files
uv run --locked scripts/sync_version.py --check
uv run --locked ast-grep scan --error
uv run --locked ast-grep test -c sgconfig.yml --skip-snapshot-tests
uv run --locked ast-grep test -c examples/sgconfig.yml --skip-snapshot-tests
uv run --locked pytest --cov=byor --cov-report=term-missing --cov-fail-under=90
```

## Package layout

| Path | Responsibility |
| --- | --- |
| `src/byor/cli.py` | CLI parser, pre-command self-healing, disabled-repo guard, dispatch |
| `src/byor/config.py`, `src/byor/config_schema.py` | Config, registry, and machine-state loading, validation, locking |
| `src/byor/commands/` | Subcommands (`init`, `install`, `sync`, `doctor`, `gate`, `rules`, `package`, `profile`, `disable`, `heal`, `listing`) |
| `src/byor/agents/` | Hook installers, payload parsers, plugin installers (`harness`, `hookconfig`, `opencode`, `pi`, `skill`) |
| `src/byor/data/agents/` | Packaged TypeScript sources (`opencode-plugin.ts`, `pi-extension.ts`, `harness-types.d.ts`) |
| `src/byor/data/skill/` | Bundled `byor` skill (`SKILL.md` + `references/`) |
| `src/byor/rules/` | Rule discovery, metadata validation, ID conflict checks, mirror sync |
| `src/byor/scaffold/` | Ignore blocks, `sgconfig.yml`, git shims, worktree copy, pre-commit, CI |
| `src/byor/scan/` | `ast-grep` runner, extra checks, `agent-check`, `command-check`, line scoping |
| `src/byor/io/` | Atomic YAML/text I/O, locking, git helpers, path utilities |
| `examples/` | CI-tested starter rules, command rules, and check scripts |
| `tests/` | Pytest suite mirroring `src/byor/`, plus `test_check_scripts.py` |

## Conventions

- Conventional Commits (for example,
  `docs: replace mermaid diagrams with svgs`).
- After changing `[project].version` in `pyproject.toml`, run
  `uv run --locked scripts/sync_version.py`.
- Add `valid:` and `invalid:` cases in `.byor/rule-tests/` or
  `examples/rule-tests/` for every rule change.
- Keep Ubuntu coverage >= 90%, preferring real filesystem/CLI tests in
  `tmp_path` sandboxes via `tests/support.py`.
- See [AGENTS.md](../../AGENTS.md) for contributor and release workflow details.
