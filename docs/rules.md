# Rules

byor rules are ordinary ast-grep YAML files. Anything ast-grep accepts works,
plus optional metadata that the AI hooks read. A rule turns a preference of
yours, the kind a general linter will not ship, into an executable check that
byor's post-edit hook enforces in real time.

## Rule file format

A worked example (a starting point, not an enabled default):

```yaml
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
      invisible to reviewers and type checkers. Most casts should be replaced
      with narrowing, clearer signatures, protocols, or better value flow.
    agent_prompt: >
      Do not use typing.cast here. Fix the type by narrowing, changing the
      signature, introducing a protocol, or restructuring the value flow. Keep a
      cast only when the needed invariant cannot be expressed by Python's type
      system. If this is genuinely necessary, add `# ast-grep-ignore:
      python.no-typing-cast` on its own line directly above the offending line,
      with a short comment above it explaining the type-system limitation.
    tags:
      - python
      - typing
      - style-guide
      - greenfield
```

Required ast-grep fields: `id`, `language`, `rule`, `message`. Recommended:
`severity`.

Optional `metadata.byor` fields, all ignored by ast-grep itself:

| Field | Used for |
| --- | --- |
| `rationale` | Why the rule exists, for humans reading the file |
| `agent_prompt` | What `byor agent-check` tells an AI agent to do; falls back to `message` when absent |
| `docs_url` | Link to fuller documentation |
| `tags` | Free-form labels for listing, profiles, and exclusions (default `[]`) |

## Exceptions

Exception policy lives in `agent_prompt` prose, not in schema fields. A rule
that tolerates exceptions says so in its `agent_prompt`, ending with the
suppression idiom native to `ast-grep scan` and `ast-grep lsp`:

```python
# <short reason>
# ast-grep-ignore: <rule-id>
some_offending_line()
```

The `ast-grep-ignore` directive goes on its own line directly above the
violation, naming the rule id (a bare `ast-grep-ignore` silences every rule on
the line below). Put the reason on the comment line above it. ast-grep also
honors the directive at the end of the offending line itself, but a formatter
that splits that line relocates the comment and silently invalidates it — the
line above is immune to reformatting.

`byor add --allow-exceptions` ends the new rule's `agent_prompt` with the
standard sentence:

> If this is genuinely necessary, add `# ast-grep-ignore: <rule-id>` on its own
> line directly above the offending line, with a short comment above it
> explaining why.

In a repo with a committed gate (see [sync-model.md](sync-model.md)), only
suppress rules the repo commits. The gate's runner knows nothing about your
personal global or package rules, and ast-grep treats a suppression naming an
unknown rule as an error (`unused-suppression`) — so committing that hatch
fails the build by design. Promote the rule to project scope first, then
suppress it.

## Rule IDs

Rule IDs must be unique across the three repo rule directories combined, and
unique within the canonical global rules directory. The recommended pattern is

```text
[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)*
```

`add` and `edit` warn on IDs outside this pattern but require a bare name: an
ID with a path separator, backslash, or `..` component is rejected, because
the rule is written to a `<rule-id>.yml` file. Duplicates are also rejected
before ast-grep ever sees them (ast-grep treats duplicate IDs as a hard
error).

## Scopes

| Scope | Directory | Meaning |
| --- | --- | --- |
| `project` | `.byor/rules/project/` | Shared team policy, committed |
| `local` | `.byor/rules/personal/local/` | Private to you and this repo: experiments, preferences, temporary diagnostics |
| `global` | `~/.config/byor/rules/` (canonical) | Your personal rules across every repo |

Global rules are copied into `.byor/rules/personal/global/` by sync so
ast-grep can read them. That directory is a generated build artifact — never
edit it by hand; sync mirrors it wholesale (see
[sync-model.md](sync-model.md)). Organize rules in subdirectories by language
or topic (e.g. `rules/python/no-typing-cast.yml`); sync preserves the relative
paths.

Same-ID rules resolve by scope precedence — project wins over local wins over
package wins over global, mirroring the check tiers — and sync skips the losing
copy, no error. Conflicts that ast-grep would see are errors:

| Where | Behavior |
| --- | --- |
| Duplicate ID within project rules | Error |
| Duplicate ID within local rules | Error |
| Duplicate ID within canonical global rules | Error |
| Project ID matches global ID | Project wins; sync skips the copy |
| Local ID matches global ID | Local wins; sync skips the copy |
| Package ID matches global ID | Package wins; sync skips the global copy |
| Project ID matches local ID | Error — a local variation of a project rule requires a different ID |
| Two installed packages share an ID | Error — exclude one with `byor exclude` |

## Adding rules

```bash
byor add --scope project|local|global [--id RULE_ID] [--language LANGUAGE]
           [--from FILE | --edit] [--allow-exceptions]
```

- `--from FILE` copies an existing ast-grep YAML rule file into the scope.
- `--edit` opens a generated template in `$EDITOR` (split with `shlex`,
  default `vi`).
- With neither, `add` prints the template (with `--id`/`--language`
  substituted) and asks you to rerun with one of them.
- `--allow-exceptions` ends the rule's `agent_prompt` with the standard
  suppression sentence (see [Exceptions](#exceptions)): pre-filled in the
  `--edit` template, appended to the copied rule with `--from`. When the rule
  has no `metadata.byor.agent_prompt`, it is created seeded from `message`
  so the prompt still carries the fix instruction.

The new rule is written as `<rule-id>.yml` at the scope's rule root, then
validated: YAML parses, required fields present, no illegal ID conflict. With
`--edit`, validation runs on a draft before anything is written to the scope —
if it fails, the error ends with `Your draft is saved at <path>.` and you can
rerun with `add --from <draft>`.

After writing, `add` syncs the current repo (global scope also syncs every
registered repo) and prints any failing `doctor --quick` checks.

## Editing rules

```bash
byor edit RULE_ID [--scope project|local|global|auto]
```

Opens the rule in `$EDITOR`. `auto` (the default) resolves project, then
local, then canonical global. The global scope always opens the canonical file
under `~/.config/byor/rules/` — never a generated copy under
`.byor/rules/personal/global/`. Leaving the file unchanged prints
`No changes to 'RULE_ID'` and exits 0. The post-action is the same as `add`:
validate, sync (fan out for global scope), report doctor problems.

## Removing rules

```bash
byor remove RULE_ID [--scope project|local|global|auto]
```

Deletes the rule file. Scope resolution is identical to `edit`: `auto` (the
default) resolves project, then local, then canonical global, and the global
scope always deletes the canonical file under `~/.config/byor/rules/` —
never just a generated copy. The post-action is the same as `add` and `edit`:
sync (global scope fans out to every registered repo, removing the generated
copies) and `doctor --quick`.

Removing a project or local rule that shadowed a global rule by ID lets the
global copy return on that same sync.

## Promotion

```bash
byor promote RULE_ID --from local|global|package [--to project] [--replace]
byor promote --check NAME
```

- `--from local` copies the local rule into project rules and removes the
  local original. (`--keep-local` exists but always fails: keeping the
  original would leave a project and a local rule sharing one ID, which
  ast-grep rejects — give the local variant a different ID instead.)
- `--from global` copies the canonical global rule into project rules and
  never removes the canonical original. Sync then skips the global copy
  because the project owns the ID; deleting the project rule later lets the
  global rule return naturally. `excluded_rule_ids` is not touched.
- `--from package` copies an installed package's rule into project rules and
  leaves the package source alone, exactly like `--from global`. Sync then
  skips the package copy because the project owns the ID.
- `--check NAME` copies a global or installed-package check into
  `.byor/config.yml` `checks:`, making it a tracked repo check. The check
  merge lets a repo check win by name, so the copied check runs exactly once
  in byor, in CI, and in a pre-commit hook. Promoting a name that is already
  a repo check fails.

The rule's relative path below its scope root is preserved
(`local/python/x.yml` → `project/python/x.yml`). If the destination file
exists, promote fails unless `--replace`.

## Exclusion

```bash
byor exclude RULE_ID
byor include RULE_ID
byor exclude --tag TAG
byor include --tag TAG
```

`exclude` adds the ID to `global.excluded_rule_ids` in `.byor/local.yml`
(private, gitignored) and syncs, removing the generated copy. `include`
removes the ID and syncs; if a project or local rule still owns the ID, the
global rule stays skipped and `include` says so. These commands affect global
and installed-package rules; project and local rules are repo files you edit
directly.

Tag exclusions add entries under `global.excluded_tags` and skip any personal
global rule with a matching `metadata.byor.tags` value. Tags are user-defined
labels; byor does not reserve names or enforce a taxonomy. To see the tags
already in use, run:

```bash
byor list --tags
```

See what is excluded with:

```bash
byor list --scope all
```

```text
project  python.no-typing-cast  .byor/rules/project/python.no-typing-cast.yml
skipped  no-one-line-wrapper  excluded in .byor/local.yml
```

## Profiles

Profiles are named templates in the global config. They apply repo-local
exclusions, but they are not runtime modes: after a profile is applied, sync
only reads `.byor/local.yml`.

```yaml
profiles:
  existing:
    description: Low-friction defaults for mature repositories.
    rules:
      excluded_tags:
        - legacy-risk
      excluded_rule_ids: []
    checks:
      excluded_tags:
        - strict
      excluded: []
```

Use a profile during init, set a default for non-interactive init, or apply one
later:

```bash
byor init --profile existing
byor init --profiles legacy prototyping
byor profile add existing
```

```yaml
init:
  profile: existing
  # or several; `profile` and `profiles` merge when both are set
  profiles:
    - legacy
    - prototyping
```

The profile `rules` section maps onto `.byor/local.yml`'s `global` section.
Those selectors affect personal global rules only; project and local rules stay
owned by the repo.

`byor profile add` merges the profile's selectors into any exclusions already in
`.byor/local.yml`; it never clears existing entries, and re-adding a profile is
a no-op. Remove individual selectors with `byor include`.

## Packages

Where a profile subtracts, a package adds. A package is a named bundle under the
global config's `packages/` directory that a repo opts into. Unlike the personal
global rules, a package is not in root ast-grep, so its rules apply only where
they are installed.

```text
~/.config/byor/packages/
  python-strict/
    no-cast.yml          # a rule, same format as any other
    checks.yml           # optional: checks this package contributes
```

A `checks.yml` is a `checks:` list in the same shape as the repo and global
configs:

```yaml
checks:
  - name: ruff-strict
    extensions: [py]
    run: uv run ruff check --select ALL
```

Install and list packages the way you apply profiles:

```bash
byor package list
byor package add python-strict
byor init --packages python-strict web-conventions   # or at init time
```

```yaml
init:
  # installed by every non-interactive init; `package` and `packages` merge
  packages:
    - python-strict
```

`byor package add` records the opt-in in `.byor/local.yml` (private, gitignored)
and syncs. The package's rules mirror into `.byor/rules/personal/packages/`,
git-ignored but visible to ast-grep. Rules and checks share one precedence:
repo wins over package wins over global (by ID for rules, by name for checks) —
opting into a package is an easy avenue to override your global setup.
Excluding a package rule or check works through the usual `byor exclude`.

To share a package's rules or checks with the team, promote them into tracked
config: `byor promote RULE_ID --from package` and `byor promote --check NAME`.
Editing the package under `~/.config/byor/packages/` and re-syncing upgrades an
installed package's rules, just as editing a global rule does.

## Another worked example

A starting point for taste-level rules — again, not an enabled default:

```yaml
id: no-trivial-delegating-function
language: Python
severity: warning
message: Avoid one-line functions that only delegate to another function.
rule:
  pattern: |
    def $NAME($$$ARGS):
        return $CALLEE($$$CALL_ARGS)
metadata:
  byor:
    rationale: >
      A function that only routes to another function often adds call-stack
      noise without creating a useful abstraction.
    agent_prompt: >
      Do not add a one-line delegating function unless it creates a meaningful
      boundary, stable public API, semantic name, or compatibility layer. Inline
      the call or use the real function directly. If this wrapper is genuinely
      necessary, leave a concise comment explaining the boundary it protects.
    tags:
      - python
      - abstraction
```

This `pattern` only catches a literal one-line `def`; the
[`examples/`](../examples/) directory has `no-routing-functions`, a relational
version that also catches multi-line bodies, awaited calls, and docstringed
one-liners. The examples there run from a bare pattern up to that rule, each with
the ast-grep technique it demonstrates.
