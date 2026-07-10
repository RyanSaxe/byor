# Examples

Reference rules and configuration for byor, written to teach the mechanics.
Copy what fits; nothing here is an enabled default.

Everything in this directory is exercised in CI: the rules are validated with
`ast-grep test` against the samples in [`rule-tests/`](rule-tests), so the
examples cannot silently drift from the tool.

## Rules library

A rule is an ordinary ast-grep YAML file (see [docs/rules.md](../docs/rules.md)).
These are ordered from the simplest mechanism to the most expressive:

| Rule | Demonstrates |
| --- | --- |
| [`no-print`](rules/no-print.yml) | A bare `pattern` with a variadic metavariable (`$$$ARGS`). |
| [`no-requests`](rules/no-requests.yml) | Banning a library at its import choke point — a `dotted_name` clause covers every plain-import form (aliased, submodule, comma-combined) and two patterns cover from-imports. A rule that names your choice (httpx over requests), which no general linter ships. |
| [`python.no-typing-cast`](rules/python.no-typing-cast.yml) | A small `any` rule combining import patterns and call patterns with named metavariables (`$TYPE`, `$VALUE`). |
| [`no-routing-functions`](rules/no-routing-functions.yml) | Relational rules — `any`/`all`, `inside`, `follows`, and `nthChild` with `reverse`, to match pass-through calls or `yield from` as a function's only statement. |
| [`keyword-only-args`](rules/keyword-only-args.yml) | `utils` rules composed with nested `follows` to count sibling nodes — flagging a third positional parameter while exempting `self`/`cls` and anything after a bare `*` or `*args`. |
| [`no-console-log`](rules/no-console-log.yml) | The same bare `pattern` mechanism in a non-Python language (`language: typescript`). |

Each file's `metadata.byor.agent_prompt` is the directive byor hands an AI agent
when the rule trips; the leading comment explains the ast-grep technique.

The matching files in [`rule-tests/`](rule-tests) are also documentation: each
lists `valid:` code that must not match and `invalid:` code that must. To run
them:

```bash
ast-grep test -c examples/sgconfig.yml --skip-snapshot-tests
```

## Config setup

byor goes beyond plain ast-grep by wiring external linters and type checkers
into the same loop and feeding their output to AI agents.

- [`config/config.yml`](config/config.yml) — an annotated global config
  (`~/.config/byor/config.yml`): the `checks` block (ruff + ty) and the `ai`
  agent list.
- [`config/scripts/ruff.sh`](config/scripts/ruff.sh) — a `check` script that
  autofixes what is safe, tells the agent exactly what changed, then reports
  only the issues it could not fix. It accepts explicit file arguments for hook
  mode, and scans non-ignored repo Python files when no files are passed.
- [`config/scripts/dependency-gate.sh`](config/scripts/dependency-gate.sh) — an
  agent-only check (`gate: false`): it fails whenever the dependency list in
  `pyproject.toml` differs from the last commit, so an agent must ask before
  adding or removing a package. Version bumps and other pyproject edits pass.
- [`config/scripts/uv-lock-guard.sh`](config/scripts/uv-lock-guard.sh) — an
  agent-only check that rejects hand-edits to `uv.lock`. The post-edit hook
  fires only on the agent's own file edits, so `uv add` run in a terminal
  never trips it.

The scripts are exercised by the test suite (`tests/test_check_scripts.py`) in
both directions: the failure they exist to catch, and the ordinary changes
they must let through.
