# AI Agents

BYOLSP turns your ast-grep rules into directive feedback for AI coding
agents. Every agent integration wraps the same command:

```bash
byolsp agent-check --files <changed files>
```

Humans keep using `ast-grep scan` directly; `agent-check` exists to render
rule-specific instructions back into an agent's context.

## Generic integration

`byolsp init` writes `.byolsp/agents/README.md` with the core instruction:

> After writing or editing code, run `byolsp agent-check --files <changed
> files>`. If BYOLSP reports a diagnostic, fix it before continuing. If a
> rule's instruction permits exceptions, only keep the violating code when
> genuinely necessary, and suppress it with
> `# ast-grep-ignore: <rule-id> -- <short reason>` on its own line above the
> violation.

Point any agent harness at that file (or copy the instruction into the
harness's own instruction location) and the loop works.

## agent-check

```bash
byolsp agent-check [--repo PATH] [--files FILE ...] [--format text|json]
                   [--max-results N]
```

Runs `ast-grep scan --json=compact --include-metadata --color never` on the
given files (the whole repository when `--files` is omitted) and renders each
match with the rule's `metadata.byolsp.agent_prompt`, falling back to
`message` when the rule has none.

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

`--stdin-hook` (instead of `--files`) reads a Claude Code PostToolUse JSON
payload on stdin and scans the `tool_input.file_path` it names. Payloads
without a file path — including malformed ones — exit 0 without scanning, so
the hook can never block the agent loop.

## Installing and removing integrations

```bash
byolsp hook install --agent generic|claude-code|codex|copilot|opencode|skill
byolsp hook uninstall --agent generic|claude-code|codex|copilot|opencode|skill
```

`byolsp init` also installs the agents you select (interactively or via
`--agents`), plus the harness-neutral `skill` by default. Installed agents are
recorded under `ai.agents` in `.byolsp/config.yml`, which `doctor` and
`hook uninstall` use.

Generated files carry the marker
`<!-- Managed by BYOLSP. Manual edits may be overwritten. -->` (a `//` comment
equivalent in TypeScript). `uninstall` removes only marker-bearing files;
anything you edited (the marker removed) is preserved with a message.

## Per-agent status

| Agent | v0.1 integration |
| --- | --- |
| `generic` | Instruction file: `.byolsp/agents/README.md` |
| `claude-code` | Real PostToolUse hook when `.claude/` exists; otherwise instruction file `.byolsp/agents/claude-code.md` with the exact wiring |
| `codex` | Instruction file `.byolsp/agents/codex.md`; copy the instruction into `AGENTS.md` |
| `copilot` | Instruction file `.byolsp/agents/copilot.md`; copy the instruction into `.github/copilot-instructions.md` |
| `opencode` | Real post-edit plugin `.opencode/plugin/byolsp.ts` plus instruction file `.byolsp/agents/opencode.md` |
| `skill` | Rule-capture skill rendered identically into `.agents/skills/byolsp/SKILL.md` and `.claude/skills/byolsp/SKILL.md`; installed by `init` by default |

Codex, Copilot, and OpenCode auto-discover the `byolsp` rule-capture skill
from `.agents/skills/byolsp/SKILL.md`, so all three get the capture loop
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
`byolsp agent-check --files <file>` on the touched file and, on exit 2,
appends the diagnostics to the tool output the model sees. Any other exit
code appends nothing, so a byolsp configuration error never breaks the agent
loop.

Install also writes the standard instruction file `.byolsp/agents/opencode.md`,
which tells the model the plugin covers `edit`, `write`, and `apply_patch`
calls that name a single `filePath` (a multi-file `apply_patch` is skipped)
and to run `agent-check` manually for files changed another way (for example
via shell commands).

### claude-code

When the repo has a `.claude/` directory holding more than the byolsp skill
render, install merges this hook into
`.claude/settings.json` (created if absent; existing keys and hook groups
preserved):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit|NotebookEdit",
        "hooks": [
          {
            "type": "command",
            "command": "byolsp agent-check --stdin-hook >&2"
          }
        ]
      }
    ]
  }
}
```

Claude Code pipes the tool-call JSON to the hook on stdin (which
`--stdin-hook` parses) and, on exit 2, feeds the hook's stderr back to the
model — hence `>&2`: `agent-check` exits 2 exactly when there are
diagnostics, so the instructions reach the model only when something needs
fixing.

Install is idempotent. `uninstall` removes only hook groups whose every
command is byolsp's; a group you mixed your own hooks into counts as
user-edited and stays.
