# Authoring rules

byor runs three kinds of checks: `ast-grep` YAML rules for code shape,
`language: Bash` command rules for shell commands, and external `checks` /
`command_checks` for linters, type checkers, or scripts.

## `ast-grep` rules

Use standard `ast-grep` YAML with an optional `metadata.byor` block for AI
hooks:

```yaml
# .byor/rules/project/python.no-typing-cast.yml
id: python.no-typing-cast
language: Python
severity: warning
message: Avoid typing.cast in Python code.
rule:
  any:
    - pattern: from typing import cast
    - pattern: from typing import cast as $ALIAS
    - pattern: from typing_extensions import cast
    - pattern: from typing_extensions import cast as $ALIAS
    - pattern: typing.cast($TYPE, $VALUE)
    - pattern: typing_extensions.cast($TYPE, $VALUE)
    - pattern: cast($TYPE, $VALUE)
metadata:
  byor:
    rationale: >
      Casting hides type-model problems and hides invalid assumptions from
      reviewers and type checkers.
    agent_prompt: >
      Do not use typing.cast here. Fix the type by narrowing, changing the
      signature, introducing a protocol, or restructuring the value flow.
    tags: [python, typing]
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
# External library returns untyped dict; validated at boundary.
# ast-grep-ignore: python.no-typing-cast
value = cast(dict[str, str], raw)
```

Pass `--allow-exceptions` to `byor add` to append the standard suppression
instruction to `agent_prompt` (seeding from `message` if absent). In gated
repos, only suppress committed `project` rules because `ast-grep` flags
suppressions for unknown rules as `unused-suppression` in CI.

## Managing rules with the CLI

```sh
byor add --scope project --from draft.yml            # validate, copy, sync, quick doctor
byor add --scope global --id no-print --edit         # open template in $EDITOR (saves draft on error)
byor add --scope project --from draft.yml --allow-exceptions
byor edit python.no-typing-cast                      # resolve project -> local -> canonical global
byor remove python.no-typing-cast                    # delete from resolved scope and re-sync
byor list --scope all                                # active, skipped, and excluded rules + checks
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

Use a script for multi-step checks (such as autofix-then-report). An autofixing
script must report what it changed and exit nonzero whenever it edits a file so
the agent sees why disk changed:

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

# Keep F401 unfixable so autofix does not drop an import before the next edit uses it.
fixed=$(uvx ruff check --fix-only --show-fixes --unfixable F401 "$@" 2>/dev/null)
reformatted=""
case "$(uvx ruff format "$@" 2>&1)" in
*reformatted*) reformatted="ruff format reformatted the file(s)." ;;
esac
remaining=$(uvx ruff check --quiet --output-format concise "$@" 2>/dev/null)

[ -z "$fixed$reformatted$remaining" ] && exit 0
[ -n "$fixed" ] && printf 'Autofixed by ruff (no action needed):\n%s\n' "$fixed"
[ -n "$reformatted" ] && printf '%s\n' "$reformatted"
[ -n "$remaining" ] && printf 'Remaining ruff issues to fix:\n%s\n' "$remaining"
exit 1
```

Put personal scripts in `~/.config/byor/scripts/` (`~/`) and repo scripts in
`.byor/scripts/`. Make scripts executable or prefix an interpreter in `run`
(`sh ~/.config/byor/scripts/ruff.sh`).

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
