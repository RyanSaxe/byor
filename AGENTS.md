# Developing `byor`

This file is for changing `byor`. For usage and installation, see
[README.md](README.md) and [docs/](docs/).

## Setup

```sh
uv sync --locked
npm ci
prek install
```

Run Python exclusively through `uv run` (`uv add`, `uv sync`), never bare
`python` or `pip`.

## Repository layout

- `src/byor/cli.py` — CLI entry point and argument dispatch
- `src/byor/config.py` and `src/byor/config_schema.py` — repo, local, global,
  registry, and machine-state config loading, validation, and locking
- `src/byor/commands/` — subcommand implementations (`init`, `install`, `sync`,
  `doctor`, `gate`, `rules`, `package`, `profile`, `disable`, `heal`, `listing`)
- `src/byor/agents/` — agent hook installation, payload parsing, and packaged
  plugin installers (`harness.py`, `hookconfig.py`, `opencode.py`, `pi.py`,
  `skill.py`)
- `src/byor/data/agents/` — packaged TypeScript sources (`opencode-plugin.ts`,
  `pi-extension.ts`) typed against ambient stubs in `harness-types.d.ts`
- `src/byor/data/skill/` — bundled agent skill and reference docs
- `src/byor/rules/` — rule discovery, metadata parsing, and sync
- `src/byor/scaffold/` — `.gitignore`, `sgconfig.yml`, git hooks, worktree copy,
  and optional gate generation
- `src/byor/scan/` — `ast-grep` runner, extra checks, `agent-check`,
  `command-check`, and diff/edit line scoping
- `src/byor/io/` — atomic YAML/text I/O, locking, git helpers, path utilities
- `scripts/sync_version.py` — mirrors `[project].version` from `pyproject.toml`
  into `src/byor/__init__.py`
- `.byor/rules/project/` and `.byor/rule-tests/` — committed project `ast-grep`
  rules and their valid/invalid sample suites
- `examples/` — example rules and configs verified in CI
- `tests/` — pytest suite mirroring `src/byor/`, plus `test_check_scripts.py`
  for `.byor/scripts/`

## Project conventions

- Keep `src/byor/__init__.py` in sync with `pyproject.toml` via
  `uv run --locked scripts/sync_version.py` whenever the package version
  changes.
- When adding or editing a rule under `.byor/rules/project/` or `examples/`, add
  or update matching valid/invalid cases in `.byor/rule-tests/` or
  `examples/rule-tests/`.
- Keep Ubuntu test coverage at or above 90% (`pytest --cov=byor`).
- Prefer testing real filesystem and CLI behavior in `tmp_path` sandboxes using
  helpers in `tests/support.py`.
- Ask before adding a new runtime or development dependency.

## Checks

Run these before pushing:

```sh
prek run --all-files
uv run --locked scripts/sync_version.py --check
uv run --locked ast-grep test -c sgconfig.yml --skip-snapshot-tests
uv run --locked ast-grep test -c examples/sgconfig.yml --skip-snapshot-tests
uv run --locked pytest --cov=byor --cov-report=term-missing --cov-fail-under=90
```

## Branches, commits, and releases

- Default branch is `main`; land changes through pull requests into `main`.
- Write commit messages following Conventional Commits (for example,
  `feat(scan): include rule tags in JSON output`).
- Releases are triggered manually via `workflow_dispatch` on
  `.github/workflows/release.yml`, which verifies tests, bumps the version, tags
  and creates a GitHub Release, and publishes to PyPI via Trusted Publishing.
- Agents may run local checks/tests and open or update pull requests without
  asking. Ask before merging pull requests, approving workflow runs, or
  triggering a release.
