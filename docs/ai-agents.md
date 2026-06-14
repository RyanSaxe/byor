# AI Agents

byor turns your ast-grep rules into directive feedback for AI coding
agents. Every agent integration wraps the same command:

```bash
byor agent-check --scope diff --files <changed files>
```

Humans keep using `ast-grep scan` directly; `agent-check` exists to render
rule-specific instructions back into an agent's context. Real post-edit hooks
use `--stdin-hook HARNESS` instead, which scopes to the exact edited lines.

byor writes no instruction files: a harness discovers the rule-capture skill on
its own (the cross-agent `SKILL.md` standard) and the installed post-edit hook
runs `agent-check` automatically, so the loop needs no prose telling the agent
to run byor.

## agent-check

```bash
byor agent-check [--repo PATH] [--files FILE ...] [--scope edit|diff|file]
                   [--format text|json] [--max-results N]
```

Runs `ast-grep scan --json=compact --include-metadata --color never` on the
given files (the whole repository when `--files` is omitted) and renders each
match with the rule's `metadata.byor.agent_prompt`, falling back to
`message` when the rule has none. It then runs any configured extra checks (see
[Extra checks](#extra-checks)) on the in-scope files.

Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | No diagnostics (text mode prints nothing) |
| 2 | Diagnostics found |
| 1 | Tool or configuration error |

Text output groups by file and sorts by line, then rule ID:

```text
BYOR found 1 issue in AI-written code.

src/example.py:3:9
Rule: no-python-cast
Severity: warning
Message: Avoid typing.cast in Python code.
Code: value = cast(int, "3")

Instruction:
Do not use typing.cast here. Fix the type by narrowing, changing the signature, introducing a protocol, or restructuring the value flow. If the cast is genuinely necessary, leave a concise comment explaining the invariant that the type checker cannot see.
```

At most 20 diagnostics render by default; when more exist the output ends
with:

```text
...and N more diagnostics. Run ast-grep scan for the full list.
```

`--max-results N` is forwarded to ast-grep and also replaces the 20-diagnostic
render cap. `--format json` prints all diagnostics as
`{"issues": [{"file", "line", "column", "rule_id", "severity", "message",
"code", "instruction"}, ...]}` with 1-based positions and repo-relative paths.

`--scope` keeps only diagnostics whose lines overlap the chosen ranges
(default: `file` with `--files`, `edit` in hook mode). `diff` scopes to
uncommitted `git diff HEAD` lines — an untracked file is all new lines, and
without usable git history the whole file stays in scope. `edit` scopes to the
lines a hook payload's edit touched, so it requires `--stdin-hook`, and falls
back to `diff` then `file` when the edit contents cannot be located. Under
`edit`/`diff` scope, files missing on disk are skipped silently.

`--stdin-hook HARNESS` (claude-code|codex|copilot|cursor, instead of `--files`)
reads that harness's post-edit JSON payload on stdin, normalizes it to the
edited file(s) and edit text, and replies in the harness's own feedback format
(claude-code via stderr + exit 2; codex/copilot/cursor via a JSON envelope on
stdout). Codex payloads carry an `apply_patch` envelope, which byor parses
for the added lines. Payloads without a recognizable file — including malformed
ones — exit 0 without scanning, and hook mode is silent in a repo with no
`.byor/config.yml`, so a hook can never block the agent loop.

## Extra checks

`agent-check` can run extra command-line checks after ast-grep. Declare them
under `checks:` in `.byor/config.yml` (committed, shared with the team) or in
the global config (personal, every repo):

```yaml
checks:
  - name: ruff
    extensions: [py]
    run: uv run ruff check --output-format concise
```

`run` is shlex-split into argv and invoked directly (never through a shell);
the in-scope files whose extension is listed in `extensions` are appended as
trailing arguments (an empty `extensions` matches every in-scope file). Checks
merge by `name` with the repo config winning over the global one, and
`.byor/local.yml` disables them per repo:

```yaml
checks:
  excluded:
    - ruff
```

A check that exits nonzero has its raw stdout and stderr appended under a
`### <name>` header on the same channel as the diagnostics, and makes
`agent-check` exit `2`. A check whose command cannot be found prints one
warning line to stderr and is skipped — it never crashes the hook. `byor
list` and `byor doctor` show the effective checks with their origin and any
exclusions.

Trust model: committed checks run on every contributor's machine, the same
model as pre-commit hooks. Only add checks whose commands you trust.

## Installing and removing integrations

`byor install` sets up the agents you choose (plus the harness-neutral `skill`)
in one step; `byor hook` adds or removes a single one afterward. There is no
per-repo step — each integration writes to its agent's own config under your
home directory, so it applies in every repo you work in.

```bash
byor install --agents claude-code,codex
byor hook install --agent AGENT
byor hook uninstall --agent AGENT
```

`AGENT` is one of `claude-code`, `codex`, `copilot`, `cursor`, `opencode`,
`pi`, or `skill`. byor records the agents you install under `ai.agents` in
`~/.config/byor/config.yml`, which `doctor` and `hook uninstall` read.

Generated files carry the marker
`<!-- Managed by BYOR. Manual edits may be overwritten. -->` (a `//` comment
equivalent in TypeScript). `uninstall` removes only marker-bearing files;
anything you edited (the marker removed) is preserved with a message.

The global config can carry an `init:` section whose values become `byor
init`'s defaults — the pre-selected answer for each interactive prompt and the
answer used under `--non-interactive`. An explicit init flag always overrides
the global default.

```yaml
init:
  ignore_mode: local
  git_hooks: true
```

## Per-agent status

Each integration lands in its agent's own configuration under your home directory.

| Agent | Integration |
| --- | --- |
| `claude-code` | Real `PostToolUse` hook (`~/.claude/settings.json`) |
| `codex` | Real `PostToolUse` hook (`~/.codex/hooks.json`, matcher `Edit\|Write`); trust the hook via `/hooks` |
| `copilot` | Real `postToolUse` hook (`~/.copilot/hooks/byor.json`) |
| `cursor` | Real `postToolUse` hook (`~/.cursor/hooks.json`) |
| `opencode` | Real `tool.execute.after` plugin (`~/.config/opencode/plugin/byor.ts`) |
| `pi` | Real `tool_result` extension (`~/.pi/agent/extensions/byor.ts`) |
| `skill` | Rule-capture skill rendered into `~/.agents/skills/byor/SKILL.md` and `~/.claude/skills/byor/SKILL.md`; installed by `byor install` by default |

Codex, Copilot, Cursor, OpenCode, and Pi auto-discover the `byor` rule-capture
skill from `~/.agents/skills/byor/SKILL.md`, so they get the capture loop natively.

### skill

The rule-capture skill teaches agents to *create* rules from your feedback,
not just obey them. When you voice a durable, mechanically checkable
preference about code syntax or structure — "never use X", "always do Y" —
the agent:

1. **Drafts** a complete ast-grep rule: id, language, severity, message,
   `rule.pattern`, and `metadata.byor` with a rationale, an imperative
   `agent_prompt` for future AI readers, and tags.
2. **Proposes a scope**: `project` for team policy voiced about this
   codebase, `global` for personal preferences that transcend the repo,
   `local` for experiments — and shows you the drafted rule.
3. **Confirms** with exactly one question — covering the rule, the scope, and
   whether exceptions are acceptable — before writing anything. No rule is
   ever created from an offhand remark. When you allow exceptions, the drafted
   `agent_prompt` ends with the standard suppression sentence.
4. **Creates** the rule via `byor add --scope SCOPE --from FILE` (which
   validates, syncs, and runs doctor), then **verifies** it by running
   `ast-grep scan` against an in-repo example of the violation.

The skill body that drives this ships with byor as Markdown (`byor/data/skill.md`);
when a feedback policy is really better solved by a linter, type checker, or
formatter (line length, import order, a wrong type), the skill has the agent
say so and offer to configure that tool and wire it in — as a byor `check`, in
CI, or as a pre-commit hook. A preference no tool can express (naming
philosophy, architectural taste) is declined with a pointer to the harness's
own instruction file (CLAUDE.md, AGENTS.md, …).

### The skill is byor-owned

The skill is global — one render per machine — and lives at two paths because
no single one is read by every harness:

| Harness | Reads the skill from |
| --- | --- |
| Claude Code | `~/.claude/skills/byor/SKILL.md` |
| Codex, Cursor, Pi | `~/.agents/skills/byor/SKILL.md` |
| Copilot, OpenCode | `~/.agents/skills/byor/SKILL.md`; also read `~/.claude/skills/` |

byor owns both renders, like the OpenCode plugin or the generated rule copies.
It writes them from the packaged skill (`byor/data/skill.md`) and keeps them
current with the **same self-heal that runs on every byor command**: a render
that drifts from the installed byor's skill is silently rewritten, so the skill
can never go stale against a changed CLI, and there is no refresh command to
remember.

To take a render over, **remove its byor marker**: byor then leaves that file
alone (the standard ownership escape hatch), and you maintain it. The frontmatter
is intentionally just `name` + `description`, the only fields every harness reads,
so the one file works everywhere. `hook uninstall --agent skill` removes the
marker-bearing renders.

### opencode

OpenCode supports real post-edit hooks through TypeScript plugins. Install
writes `~/.config/opencode/plugin/byor.ts`, which hooks `tool.execute.after`:
when the tool is `edit`, `write`, or `apply_patch`, it runs
`byor agent-check --scope diff --files <file>` on the touched file and, on
exit 2, appends the diagnostics to the tool output the model sees. Any other
exit code appends nothing, so a byor configuration error never breaks the
agent loop.

The plugin covers `edit`, `write`, and `apply_patch` calls that name a single
`filePath`; a multi-file `apply_patch` or a file changed another way (for
example via a shell command) is not auto-checked.

### pi

Pi supports real post-edit hooks through TypeScript extensions. Install writes
`~/.pi/agent/extensions/byor.ts`. The extension hooks the `tool_result` event
for the `edit` and `write` tools, runs
`byor agent-check --scope diff --files <file>` on the touched file, and appends
any diagnostics to the tool result the model sees. Pi already reads skills from
`~/.agents/skills/`, so it discovers the rule-capture skill with no Pi-specific
work.

### codex

Install writes a `PostToolUse` hook (matcher `Edit|Write`) into
`~/.codex/hooks.json`. Codex does not run a new hook until you trust it: run
`/hooks` in the Codex session and approve the byor entry once —
`byor hook install --agent codex` prints this reminder.

### claude-code

Install merges a `PostToolUse` hook into `~/.claude/settings.json`, creating it
if absent and preserving existing keys and hook groups:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit|NotebookEdit",
        "hooks": [
          {
            "type": "command",
            "command": "byor agent-check --stdin-hook claude-code >&2"
          }
        ]
      }
    ]
  }
}
```

Claude Code pipes the tool-call JSON to the hook on stdin (which
`--stdin-hook claude-code` parses) and, on exit 2, feeds the hook's stderr back
to the model — hence `>&2`. `agent-check` exits 2 exactly when there are
diagnostics, so the instructions reach the model only when something needs
fixing.

Install is idempotent. `uninstall` removes only hook groups whose every
command is byor's; a group you mixed your own hooks into counts as
user-edited and stays.
