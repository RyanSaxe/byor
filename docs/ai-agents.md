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
> files>`. If BYOLSP reports a diagnostic, fix it before continuing. If a rule
> says an exception is allowed with a comment, only keep the violating code
> when the code is genuinely necessary and add a concise comment explaining
> why.

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
byolsp hook install --agent generic|claude-code|codex|copilot
byolsp hook uninstall --agent generic|claude-code|codex|copilot
```

`byolsp init` also installs the agents you select (interactively or via
`--agents`). Installed agents are recorded under `ai.agents` in
`.byolsp/config.yml`, which `doctor` and `hook uninstall` use.

Generated files carry the marker
`<!-- Managed by BYOLSP. Manual edits may be overwritten. -->`. `uninstall`
removes only marker-bearing files; anything you edited (the marker removed) is
preserved with a message.

## Per-agent status

| Agent | v0.1 integration |
| --- | --- |
| `generic` | Instruction file: `.byolsp/agents/README.md` |
| `claude-code` | Real PostToolUse hook when `.claude/` exists; otherwise instruction file `.byolsp/agents/claude-code.md` with the exact wiring |
| `codex` | Instruction file `.byolsp/agents/codex.md`; copy the instruction into `AGENTS.md` |
| `copilot` | Instruction file `.byolsp/agents/copilot.md`; copy the instruction into `.github/copilot-instructions.md` |

### claude-code

When the repo has a `.claude/` directory, install merges this hook into
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
