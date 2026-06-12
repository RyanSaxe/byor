# Rules

BYOLSP rules are real ast-grep YAML files — no second rule language, no
compiled format. Anything ast-grep accepts works; BYOLSP adds optional
metadata that AI hooks use.

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
  byolsp:
    rationale: >
      casting hides type model problems and can make invalid assumptions
      invisible to both reviewers and type checkers.
    agent_prompt: >
      Do not use typing.cast here. Fix the type by narrowing, changing the
      signature, introducing a protocol, or restructuring the value flow. If
      the cast is genuinely necessary, leave a concise comment explaining the
      invariant that the type checker cannot see.
    allow_with_comment: true
    tags:
      - python
      - typing
```

Required ast-grep fields: `id`, `language`, `rule`, `message`. Recommended:
`severity`.

Optional `metadata.byolsp` fields, all ignored by ast-grep itself:

| Field | Used for |
| --- | --- |
| `rationale` | Why the rule exists, for humans reading the file |
| `agent_prompt` | What `byolsp agent-check` tells an AI agent to do; falls back to `message` when absent |
| `allow_with_comment` | Signals that a justified exception with a comment is acceptable (default `false`) |
| `docs_url` | Link to fuller documentation |
| `tags` | Free-form labels (default `[]`) |

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
| `project` | `.byolsp/rules/project/` | Shared team policy, committed |
| `local` | `.byolsp/rules/personal/local/` | Private to you and this repo: experiments, preferences, temporary diagnostics |
| `global` | `~/.config/byolsp/rules/` (canonical) | Your personal rules across every repo |

Global rules are copied into `.byolsp/rules/personal/global/` by sync so
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
byolsp add --scope project|local|global [--id RULE_ID] [--language LANGUAGE]
           [--from FILE | --edit]
```

- `--from FILE` copies an existing ast-grep YAML rule file into the scope.
- `--edit` opens a generated template in `$EDITOR` (split with `shlex`,
  default `vi`).
- With neither, `add` prints the template (with `--id`/`--language`
  substituted) and asks you to rerun with one of them.

The new rule is written as `<rule-id>.yml` at the scope's rule root, then
validated: YAML parses, required fields present, no illegal ID conflict. With
`--edit`, validation runs on a draft before anything is written to the scope —
if it fails, the error ends with `Your draft is saved at <path>.` and you can
rerun with `add --from <draft>`.

After writing, `add` syncs the current repo (global scope also syncs every
registered repo) and prints any failing `doctor --quick` checks.

## Editing rules

```bash
byolsp edit RULE_ID [--scope project|local|global|auto]
```

Opens the rule in `$EDITOR`. `auto` (the default) resolves project, then
local, then canonical global. The global scope always opens the canonical file
under `~/.config/byolsp/rules/` — never a generated copy under
`.byolsp/rules/personal/global/`. Leaving the file unchanged prints
`No changes to 'RULE_ID'` and exits 0. The post-action is the same as `add`:
validate, sync (fan out for global scope), report doctor problems.

## Promotion

```bash
byolsp promote RULE_ID --from local|global [--to project] [--replace]
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
byolsp exclude RULE_ID
byolsp include RULE_ID
```

`exclude` adds the ID to `global.excluded_rule_ids` in `.byolsp/local.yml`
(private, gitignored) and syncs, removing the generated copy. `include`
removes the ID and syncs; if a project or local rule still owns the ID, the
global rule stays skipped and `include` says so. These commands only affect
global rules. See what is excluded with:

```bash
byolsp list --scope all
```

```text
project  no-python-cast       .byolsp/rules/project/python/no-python-cast.yml
skipped  no-one-line-wrapper  excluded in .byolsp/local.yml
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
  byolsp:
    rationale: >
      A function that only routes to another function often adds call-stack
      noise without creating a useful abstraction.
    agent_prompt: >
      Do not add a one-line delegating function unless it creates a meaningful
      boundary, stable public API, semantic name, or compatibility layer. Inline
      the call or use the real function directly. If this wrapper is genuinely
      necessary, leave a concise comment explaining the boundary it protects.
    allow_with_comment: true
    tags:
      - python
      - abstraction
```
