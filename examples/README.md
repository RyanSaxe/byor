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
| [`no-python-cast`](rules/no-python-cast.yml) | A `pattern` with named metavariables (`$TYPE`, `$VALUE`). |
| [`no-routing-functions`](rules/no-routing-functions.yml) | Relational rules — `any`/`all`, `inside`, `follows`, and `nthChild` with `reverse`, to match "a call that is a function's only statement." |
| [`keyword-only-args`](rules/keyword-only-args.yml) | `nthChild` positional counting with a `self`/`cls` exemption, to require that arguments after the first two are keyword-only. |
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
  only the issues it could not fix.
