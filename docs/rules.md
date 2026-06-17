# Rules

byor rules are ordinary ast-grep YAML files. Anything ast-grep accepts works,
plus optional metadata that the AI hooks read.

## Rule file format

A worked example (a starting point, not an enabled default):

```yaml
id: no-python-cast
language: Python
severity: warning
message: Avoid typing.cast in Python code.
rule:
  pattern: cast($TYPE, $VALUE)
metadata:
  byor:
    rationale: >
      casting hides type model problems and can make invalid assumptions
      invisible to both reviewers and type checkers.
    agent_prompt: >
      Do not use typing.cast here. Fix the type by narrowing, changing the
      signature, introducing a protocol, or restructuring the value flow. If
      the cast is genuinely necessary, leave a concise comment explaining the
      invariant that the type checker cannot see.
    tags:
      - python
      - typing
```

Required ast-grep fields: `id`, `language`, `rule`, `message`. Recommended:
`severity`.

Optional `metadata.byor` fields, all ignored by ast-grep itself:

| Field | Used for |
| --- | --- |
| `rationale` | Why the rule exists, for humans reading the file |
| `agent_prompt` | What `byor agent-check` tells an AI agent to do; falls back to `message` when absent |
| `docs_url` | Link to fuller documentation |
| `tags` | Free-form labels (default `[]`) |

## Exceptions

Exception policy lives in `agent_prompt` prose, not in schema fields. A rule
that tolerates exceptions says so in its `agent_prompt`, ending with the
suppression idiom native to `ast-grep scan` and `ast-grep lsp`:

```python
# <short reason>
some_offending_line()  # ast-grep-ignore: <rule-id>
```

The `ast-grep-ignore` directive goes at the end of the offending line, naming
the rule id (a bare `ast-grep-ignore` silences every rule on that line). Put the
reason on the comment line above — the same idiom as `# noqa` or `# type: ignore`.

`byor add --allow-exceptions` ends the new rule's `agent_prompt` with the
standard sentence:

> If this is genuinely necessary, add `# ast-grep-ignore: <rule-id>` at the end
> of the offending line, with a short comment on the line above explaining why.

## Rule IDs

Rule IDs must be unique across the three repo rule directories combined, and
unique within the canonical global rules directory. The recommended pattern is

```text
[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)*
```

`add` and `edit` warn on IDs outside this pattern but accept whatever ast-grep
accepts. Duplicates are rejected before ast-grep ever sees them (ast-grep
treats duplicate IDs as a hard error).

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
or topic (e.g. `rules/python/no-python-cast.yml`); sync preserves the relative
paths.

Project and local rules override global rules by the same ID: sync skips the
global copy, no error. Conflicts that ast-grep would see are errors:

| Where | Behavior |
| --- | --- |
| Duplicate ID within project rules | Error |
| Duplicate ID within local rules | Error |
| Duplicate ID within canonical global rules | Error |
| Project ID matches global ID | Project wins; sync skips the copy |
| Local ID matches global ID | Local wins; sync skips the copy |
| Project ID matches local ID | Error — a local variation of a project rule requires a different ID |

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
byor promote RULE_ID --from local|global [--to project] [--replace]
```

- `--from local` copies the local rule into project rules and removes the
  local original. (`--keep-local` exists but always fails: keeping the
  original would leave a project and a local rule sharing one ID, which
  ast-grep rejects — give the local variant a different ID instead.)
- `--from global` copies the canonical global rule into project rules and
  never removes the canonical original. Sync then skips the global copy
  because the project owns the ID; deleting the project rule later lets the
  global rule return naturally. `excluded_rule_ids` is not touched.

The rule's relative path below its scope root is preserved
(`local/python/x.yml` → `project/python/x.yml`). If the destination file
exists, promote fails unless `--replace`.

## Exclusion

```bash
byor exclude RULE_ID
byor include RULE_ID
```

`exclude` adds the ID to `global.excluded_rule_ids` in `.byor/local.yml`
(private, gitignored) and syncs, removing the generated copy. `include`
removes the ID and syncs; if a project or local rule still owns the ID, the
global rule stays skipped and `include` says so. These commands only affect
global rules. See what is excluded with:

```bash
byor list --scope all
```

```text
project  no-python-cast       .byor/rules/project/python/no-python-cast.yml
skipped  no-one-line-wrapper  excluded in .byor/local.yml
```

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
