# Authoring rules

byor enforces three kinds of checks: `ast-grep` YAML rules for code shape,
`language: Bash` command rules for shell commands, and external `checks` /
`command_checks` for linters, type checkers, or custom scripts.

## `ast-grep` rules

A rule file is standard `ast-grep` YAML plus an optional `metadata.byor` block
that AI hooks read:

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
      Casting hides type-model problems and can make invalid assumptions
      invisible to reviewers and type checkers.
    agent_prompt: >
      Do not use typing.cast here. Fix the type by narrowing, changing the
      signature, introducing a protocol, or restructuring the value flow.
    tags:
      - python
      - typing
```

Required `ast-grep` fields are `id`, `language`, `rule`, and `message`
(`severity` is recommended). See
[Configuration reference](../reference/configuration.md#rule-metadata) for all
`metadata.byor` fields.

### Rule IDs

Rule IDs must be unique within each scope (`project`, `local`, and canonical
`global`). Recommended format:

```text
[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)*
```

`byor add` and `byor edit` warn on IDs outside that pattern and reject path
separators, backslashes, or `..` components because rules are stored as
`<rule-id>.yml`. Duplicate IDs are rejected before `ast-grep` sees them.

### Exceptions

Exception policy belongs in `agent_prompt` prose using `ast-grep`'s native
suppression comment placed on the line directly above the violation:

```python
# External library returns untyped dict; validated at boundary.
# ast-grep-ignore: python.no-typing-cast
value = cast(dict[str, str], raw)
```

Putting `# ast-grep-ignore: <rule-id>` on its own line above the violation keeps
formatters from relocating it if they wrap the code line. Always name the rule
ID; a bare `ast-grep-ignore` silences every rule on that line.

Pass `--allow-exceptions` to `byor add` to append the standard suppression
sentence to `agent_prompt` (seeding `agent_prompt` from `message` if absent).

In a repository with a committed gate, only suppress rules the repository
commits. `ast-grep` treats a suppression naming an unknown rule as an
`unused-suppression` error, so suppressing a personal global or package rule
fails CI by design. Promote the rule to `project` scope first.

## Managing rules with the CLI

```sh
byor add --scope project --from draft.yml            # copy, validate, sync, and run quick doctor
byor add --scope global --id no-print --edit         # open template in $EDITOR (saves draft on failure)
byor add --scope project --from draft.yml --allow-exceptions
byor edit python.no-typing-cast                      # resolve project -> local -> canonical global in $EDITOR
byor remove python.no-typing-cast                    # delete from resolved scope and re-sync
byor list --scope all                                # show active, skipped, and excluded rules + checks
```

Global-scope `add`, `edit`, and `remove` always operate on canonical files under
`~/.config/byor/rules/` (never generated mirrors) and fan out to every
registered repository. Leaving `$EDITOR` unchanged exits `0` with
`No changes to '<rule-id>'`.

## Command rules

Command rules use the same YAML shape with `language: Bash` and live under
`.byor/commands/` (or `~/.config/byor/commands/` for global scope). They never
appear in `sgconfig.yml` `ruleDirs`, so file scans never pick them up and
`.sh`-file rules never block interactive commands. Command rules have their own
ID namespace, though `byor exclude RULE_ID` and tag exclusions apply to both
trees.

Create and verify a command rule in both directions:

```sh
byor add --scope project --command --from no-pip-install.yml
byor command-check --command 'pip install requests'   # denied, exit 2
byor command-check --command 'uv add requests'        # clean, exit 0
```

`byor list`, `byor edit`, `byor remove`, and `byor promote` do not manage
command rules yet; edit or move the YAML files directly and run `byor sync`. See
[examples/command-rules/](../../examples/command-rules/) for CI-tested patterns.

## Extra checks

Declare external linters, type checkers, or scripts under `checks:` in
`.byor/config.yml` (shared) or `~/.config/byor/config.yml` (personal):

```yaml
checks:
  - name: ruff
    extensions: [py]
    run: uv run ruff check --output-format concise
    tags: [format]
```

Contract for `checks`:

- **Shell-free execution.** `run` is split with `shlex` and executed directly
  without a shell, preventing shell injection from committed configs (no `&&`,
  pipes, redirects, or shell aliases). Leading `~` / `~/` expands to your home
  directory.
- **File arguments and whole-repo fallback.** `extensions` (without dots)
  filters in-scope files; empty `extensions` matches every in-scope file. byor
  appends matching paths as trailing arguments. When invoked with no paths (such
  as in generated CI workflows), the command must scan the whole repository
  while respecting `.gitignore`.
- **Precedence and output.** Checks merge by `name` (`repo` > `package` >
  `global`). Any nonzero exit appends stdout and stderr under `### <name>` and
  causes `agent-check` to exit `2`. A missing executable prints one stderr
  warning and is skipped without crashing the hook.
- **Agent-only checks (`gate: false`).** Set `gate: false` for checks that
  police agents rather than humans (for example, failing when `pyproject.toml`
  dependencies change so the agent asks first). `byor init --gate` still
  promotes `gate: false` checks into `.byor/config.yml` so every contributor's
  agent sees them, but omits them from `.pre-commit-config.yaml` and CI.

## Writing check scripts

Multi-step logic (such as autofixing before reporting) belongs in a script. An
autofixing script must tell the agent what it changed and exit nonzero whenever
it modified a file, even if no issues remain, so the agent knows why the file on
disk changed:

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

# Keep F401 unfixable so autofix does not delete an import before the next edit uses it.
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

Place personal scripts under `~/.config/byor/scripts/` (referenced with `~/`)
and repository scripts under `.byor/scripts/` (referenced by repo-relative
path). Either make the script executable or invoke its interpreter in `run` (for
example, `sh ~/.config/byor/scripts/ruff.sh`).

### Sharing helper scripts

When multiple check scripts share logic, invoke the helper as a subprocess by
path rather than mutating `sys.path`:

- Inside a repository script, resolve the helper relative to `__file__` (for
  example, `Path(__file__).parent / "lib" / "helper.py"`). byor's own Python
  checks share file discovery via `.byor/scripts/lib/pyfiles.py`.
- Inside a `~/` script that may be vendored by `byor init --gate`, spell out the
  literal `~/.config/byor/scripts/lib/helper.py` path. Gate vendoring scans for
  literal home-script paths, copies transitive helpers into `.byor/scripts/`,
  and rewrites those strings in place.

## Testing rules

Write `valid:` and `invalid:` cases alongside your rules and run `ast-grep test`
(see [examples/](../../examples/README.md) for the full fixture layout):

```sh
ast-grep test -c examples/sgconfig.yml --skip-snapshot-tests
```
