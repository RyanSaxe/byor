# AI Agents

BYOLSP turns your ast-grep rules into directive feedback for AI coding
agents. Every agent integration wraps the same command:

```bash
byolsp agent-check --scope diff --files <changed files>
```

Humans keep using `ast-grep scan` directly; `agent-check` exists to render
rule-specific instructions back into an agent's context. Real post-edit hooks
use `--stdin-hook HARNESS` instead, which scopes to the exact edited lines.

## Generic integration

`byolsp init` writes `.byolsp/agents/README.md` with the core instruction:

> After writing or editing code, run `byolsp agent-check --scope diff --files
> <changed files>`. If BYOLSP reports a diagnostic, fix it before continuing.
> If a
> rule's instruction permits exceptions, only keep the violating code when
> genuinely necessary, and suppress it with
> `# ast-grep-ignore: <rule-id> -- <short reason>` on its own line above the
> violation.

Point any agent harness at that file (or copy the instruction into the
harness's own instruction location) and the loop works.

## agent-check

```bash
byolsp agent-check [--repo PATH] [--files FILE ...] [--scope edit|diff|file]
                   [--format text|json] [--max-results N]
```

Runs `ast-grep scan --json=compact --include-metadata --color never` on the
given files (the whole repository when `--files` is omitted) and renders each
match with the rule's `metadata.byolsp.agent_prompt`, falling back to
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
BYOLSP found 1 issue in AI-written code.

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
stdout). Codex payloads carry an `apply_patch` envelope, which byolsp parses
for the added lines. Payloads without a recognizable file — including malformed
ones — exit 0 without scanning, and hook mode is silent in a repo with no
`.byolsp/config.yml`, so a hook can never block the agent loop.

## Extra checks

`agent-check` can run extra command-line checks after ast-grep. Declare them
under `checks:` in `.byolsp/config.yml` (committed, shared with the team) or in
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
`.byolsp/local.yml` disables them per repo:

```yaml
checks:
  excluded:
    - ruff
```

A check that exits nonzero has its raw stdout and stderr appended under a
`### <name>` header on the same channel as the diagnostics, and makes
`agent-check` exit `2`. A check whose command cannot be found prints one
warning line to stderr and is skipped — it never crashes the hook. `byolsp
list` and `byolsp doctor` show the effective checks with their origin and any
exclusions.

Trust model: committed checks run on every contributor's machine, the same
model as pre-commit hooks. Only add checks whose commands you trust.

## Installing and removing integrations

```bash
byolsp hook install --agent AGENT [--hook-scope project|global|local]
byolsp hook uninstall --agent AGENT
```

`AGENT` is one of `generic`, `claude-code`, `codex`, `copilot`, `cursor`,
`opencode`, or `skill`. `--hook-scope` chooses where a real hook registers:
`project` (committed config, with a `command -v byolsp` guard so teammates
without byolsp are unaffected), `global` (under `~/`, personal), or `local`
(claude-code's `.claude/settings.local.json` only). `byolsp init` installs the
agents you select (interactively or via `--agents`), plus the harness-neutral
`skill` by default, asking project vs global once for all hook-capable agents.
Installed agents are recorded under `ai.agents` in `.byolsp/config.yml`, which
`doctor` and `hook uninstall` use.

Generated files carry the marker
`<!-- Managed by BYOLSP. Manual edits may be overwritten. -->` (a `//` comment
equivalent in TypeScript). `uninstall` removes only marker-bearing files;
anything you edited (the marker removed) is preserved with a message.

The global config can carry an `init:` section whose values become `byolsp
init`'s defaults — the pre-selected answer for each interactive prompt and the
answer used under `--non-interactive`. An explicit init flag always overrides
the global default.

```yaml
init:
  agents: [claude-code, codex]
  ignore_mode: local
  git_hooks: true
  hook_scope: global
```

## Per-agent status

| Agent | Integration |
| --- | --- |
| `generic` | Instruction file: `.byolsp/agents/README.md` |
| `claude-code` | Real `PostToolUse` hook (`.claude/settings.json`, `settings.local.json`, or `~/.claude/settings.json`) plus instruction file `.byolsp/agents/claude-code.md` |
| `codex` | Real `PostToolUse` hook (`.codex/hooks.json` or `~/.codex/hooks.json`, matcher `Edit\|Write`) plus instruction file; trust the hook via `/hooks`, and copy the instruction into `AGENTS.md` |
| `copilot` | Real `postToolUse` hook (`.github/hooks/byolsp.json` or `~/.copilot/hooks/byolsp.json`) plus instruction file; copy the instruction into `.github/copilot-instructions.md` |
| `cursor` | Real `postToolUse` hook (`.cursor/hooks.json` or `~/.cursor/hooks.json`) plus instruction file `.byolsp/agents/cursor.md` |
| `opencode` | Real `tool.execute.after` plugin `.opencode/plugin/byolsp.ts` plus instruction file `.byolsp/agents/opencode.md` |
| `skill` | Rule-capture skill rendered identically into `.agents/skills/byolsp/SKILL.md` and `.claude/skills/byolsp/SKILL.md`; installed by `init` by default |

Codex, Copilot, Cursor, and OpenCode auto-discover the `byolsp` rule-capture
skill from `.agents/skills/byolsp/SKILL.md`, so they get the capture loop
natively; their instruction files say so.

### skill

The rule-capture skill teaches agents to *create* rules from your feedback,
not just obey them. When you voice a durable, mechanically checkable
preference about code syntax or structure — "never use X", "always do Y" —
the agent:

1. **Drafts** a complete ast-grep rule: id, language, severity, message,
   `rule.pattern`, and `metadata.byolsp` with a rationale, an imperative
   `agent_prompt` for future AI readers, and tags.
2. **Proposes a scope**: `project` for team policy voiced about this
   codebase, `global` for personal preferences that transcend the repo,
   `local` for experiments — and shows you the drafted rule.
3. **Confirms** with exactly one question — covering the rule, the scope, and
   whether exceptions are acceptable — before writing anything. No rule is
   ever created from an offhand remark. When you allow exceptions, the drafted
   `agent_prompt` ends with the standard suppression sentence.
4. **Creates** the rule via `byolsp add --scope SCOPE --from FILE` (which
   validates, syncs, and runs doctor), then **verifies** it by running
   `ast-grep scan` against an in-repo example of the violation.

Preferences no syntax pattern can express (naming philosophy, architectural
taste) are declined with a pointer to the harness's instruction file instead.

The skill is one canonical document rendered identically into two locations,
which together cover every major harness natively:

| Harness | Reads the skill from |
| --- | --- |
| Claude Code | `.claude/skills/byolsp/SKILL.md` |
| Codex | `.agents/skills/byolsp/SKILL.md` |
| Copilot | `.agents/skills/byolsp/SKILL.md`; also reads `.claude/skills/` |
| OpenCode | `.agents/skills/byolsp/SKILL.md`; also reads `.claude/skills/` |

`byolsp init` installs both renders by default;
`byolsp hook install --agent skill` and `hook uninstall --agent skill` manage
them explicitly. Both renders are marker-managed: an unmarked file you placed
at either path is never overwritten by init or hook install. `doctor` checks
both renders exist and match the packaged content when `skill` is in
`ai.agents`; a render without the BYOLSP marker is treated as user-owned and
accepted as is.

### opencode

OpenCode supports real post-edit hooks through TypeScript plugins. Install
writes `.opencode/plugin/byolsp.ts`, which hooks `tool.execute.after`: when
the tool is `edit`, `write`, or `apply_patch`, it runs
`byolsp agent-check --scope diff --files <file>` on the touched file and, on
exit 2, appends the diagnostics to the tool output the model sees. Any other
exit code appends nothing, so a byolsp configuration error never breaks the
agent loop.

Install also writes the standard instruction file `.byolsp/agents/opencode.md`,
which tells the model the plugin covers `edit`, `write`, and `apply_patch`
calls that name a single `filePath` (a multi-file `apply_patch` is skipped)
and to run `agent-check` manually for files changed another way (for example
via shell commands).

### codex

Install writes a `PostToolUse` hook (matcher `Edit|Write`) into
`.codex/hooks.json` (project) or `~/.codex/hooks.json` (global). Codex does not
run a new hook until you trust it: run `/hooks` in the Codex session and approve
the byolsp entry once. Install also writes the standard instruction file; copy
its contents into `AGENTS.md`, which Codex reads as repository guidance.

### claude-code

Install merges a `PostToolUse` hook into the settings file for the chosen
scope — `.claude/settings.json` (project), `.claude/settings.local.json`
(local), or `~/.claude/settings.json` (global) — creating it if absent and
preserving existing keys and hook groups:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit|NotebookEdit",
        "hooks": [
          {
            "type": "command",
            "command": "byolsp agent-check --stdin-hook claude-code >&2"
          }
        ]
      }
    ]
  }
}
```

A project-scope command is wrapped in a `command -v byolsp` guard so a
teammate without byolsp is unaffected. Claude Code pipes the tool-call JSON to
the hook on stdin (which `--stdin-hook claude-code` parses) and, on exit 2,
feeds the hook's stderr back to the model — hence `>&2`: `agent-check` exits 2
exactly when there are diagnostics, so the instructions reach the model only
when something needs fixing.

Install is idempotent. `uninstall` removes only hook groups whose every
command is byolsp's; a group you mixed your own hooks into counts as
user-edited and stays.
