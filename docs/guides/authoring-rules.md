# Authoring rules

byor runs three kinds of checks: `ast-grep` YAML rules for code shape,
`language: Bash` command rules for shell commands, and external `checks` /
`command_checks` for linters, type checkers, or scripts.

## `ast-grep` rules

Use standard `ast-grep` YAML with an optional `metadata.byor` block for AI
hooks:

```yaml
# .byor/rules/project/python.no-requests.yml
id: python.no-requests
language: Python
severity: error
message: This codebase uses httpx, not requests.
rule:
  any:
    - kind: dotted_name
      regex: ^requests(\.|$)
      inside: { stopBy: end, kind: import_statement }
    - pattern: from requests import $$$NAMES
    - pattern: from requests.$SUB import $$$NAMES
metadata:
  byor:
    rationale: >
      We standardized on httpx for its async support and strict timeouts.
    agent_prompt: >
      Use httpx instead. For simple calls the API is the same (httpx.get,
      httpx.post); for repeated calls, use an httpx.Client or httpx.AsyncClient
      with an explicit timeout. Do not add requests to dependencies.
    tags: [python, dependencies]
```

Required fields: `id`, `language`, `rule`, `message` (`severity` recommended).
See [Configuration reference](../reference/configuration.md#rule-metadata) for
`metadata.byor` fields.

### Rule IDs

IDs must be unique within each scope (`project`, `local`, canonical `global`).
Recommended pattern: `[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)*`. `byor add` and
`byor edit` warn outside that pattern and reject `/`, `\`, or `..` because files
are named `<rule-id>.yml`.

### Exceptions

Put `# ast-grep-ignore: <rule-id>` on the line directly above the violation so
formatters cannot relocate it; always name the rule ID so other rules stay
active:

```python
# Legacy third-party SDK requires a requests.Session until v2 migration.
# ast-grep-ignore: python.no-requests
import requests
```

Pass `--allow-exceptions` to `byor add` to append the standard suppression
instruction to `agent_prompt` (seeding from `message` if absent). In gated
repos, only suppress committed `project` rules because `ast-grep` flags
suppressions for unknown rules as `unused-suppression` in CI.

## Managing rules with the CLI

```sh
byor add --scope project --from draft.yml                        # validate, copy, sync, quick doctor
byor add --scope global --id python.no-sys-path-mutation --edit  # open template in $EDITOR (saves draft on error)
byor add --scope project --from draft.yml --allow-exceptions
byor edit python.no-requests                                     # resolve project -> local -> canonical global
byor remove python.no-requests                                   # delete from resolved scope and re-sync
byor list --scope all                                            # active, skipped, and excluded rules + checks
```

Global `add`, `edit`, and `remove` operate on `~/.config/byor/rules/` and fan
out to registered repositories. Closing `$EDITOR` unchanged exits `0`.

## Command rules

Command rules use `language: Bash` under `.byor/commands/` or
`~/.config/byor/commands/`. They never enter `sgconfig.yml` `ruleDirs`, so file
scans ignore them and `.sh` rules never block interactive commands. They share
`byor exclude` / tag exclusions but keep a separate ID namespace.

```sh
byor add --scope project --command --from no-pip-install.yml
byor command-check --command 'pip install requests'   # denied, exit 2
byor command-check --command 'uv add requests'        # clean, exit 0
```

`list`, `edit`, `remove`, and `promote` do not manage command rules yet; edit
the YAML files directly and run `byor sync`. See
[examples/command-rules/](../../examples/command-rules/).

## Extra checks

Declare linters, type checkers, or scripts under `checks:` in `.byor/config.yml`
or `~/.config/byor/config.yml`:

```yaml
checks:
  - name: ruff
    extensions: [py]
    run: uv run ruff check --output-format concise
    tags: [format]
```

- **Shell-free.** `run` splits via `shlex` without a shell (no `&&`, pipes,
  redirects, or aliases). Leading `~` expands to `$HOME`.
- **File args and whole-repo fallback.** `extensions` (no dots; empty matches
  all) filters in-scope paths appended as trailing args. With no args (such as
  CI), the command must scan the whole repo respecting `.gitignore`.
- **Precedence and output.** Merges by `name` (`repo > package > global`).
  Nonzero exit appends stdout/stderr under `### <name>` and causes `agent-check`
  to exit `2`. Missing executables warn once on stderr and skip.
- **Agent-only (`gate: false`).** Promoted into `.byor/config.yml` for every
  contributor's agent, but omitted from `.pre-commit-config.yaml` and CI.

## Check scripts

Use a script for multi-step checks (such as lint + format verification). Prefer
read-only checks in agent hooks so files never mutate underneath an active edit
session; if a custom script does autofix, report what changed and exit nonzero:

```sh
#!/bin/sh
export NO_COLOR=1
unset FORCE_COLOR CLICOLOR_FORCE

if [ "$#" -eq 0 ]; then
  while IFS= read -r file; do
    [ -n "$file" ] && set -- "$@" "$file"
  done <<EOF
$(git ls-files -co --exclude-standard -- '*.py' '*.pyi')
EOF
  [ "$#" -eq 0 ] && exit 0
fi

lint=$(uvx ruff check --quiet --output-format concise "$@" 2>/dev/null)
lint_status=$?
format_needed=""
if ! uvx ruff format --check --quiet "$@" >/dev/null 2>&1; then
  format_needed="Files need formatting (run \`uvx ruff format\`)."
fi

if [ "$lint_status" -eq 0 ] && [ -z "$format_needed" ]; then
  exit 0
fi
[ -n "$lint" ] && printf '%s\n' "$lint"
[ -n "$format_needed" ] && printf '%s\n' "$format_needed"
exit 1
```

Put personal scripts in `~/.config/byor/scripts/` (`~/`) and repo scripts in
`.byor/scripts/`. Make scripts executable or prefix an interpreter in `run`
(`sh ~/.config/byor/scripts/ruff-readonly.sh`).

### Shared helpers

Invoke helper scripts as subprocesses by path rather than mutating `sys.path`:

- **Repo scripts:** resolve relative to `__file__` (for example,
  `Path(__file__).parent / "lib" / "helper.py"`).
- **Personal scripts (`~/`):** write the literal
  `~/.config/byor/scripts/lib/helper.py` path so `byor init --gate` detects,
  vendors, and rewrites transitive helpers into `.byor/scripts/`.

## Testing rules

Pair rules with `valid:` / `invalid:` fixtures (see
[examples/](../../examples/README.md)) and run:

```sh
ast-grep test -c examples/sgconfig.yml --skip-snapshot-tests
```
