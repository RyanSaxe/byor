# Harnesses

`byor install` registers selected harnesses and the `byor` skill globally under
`$HOME`. Add or remove one later with `byor hook install --agent AGENT` /
`byor hook uninstall --agent AGENT`.

## Support matrix

| Agent (`--agent`) | Post-edit hook | Pre-command gate | Config / plugin path |
| --- | --- | --- | --- |
| `claude-code` | `PostToolUse` | `PreToolUse` | `~/.claude/settings.json` |
| `codex` | `PostToolUse` | `PreToolUse` | `~/.codex/hooks.json` |
| `copilot` | `postToolUse` | `preToolUse` | `~/.copilot/hooks/byor.json` |
| `antigravity` | `PostInvocation` | `PreToolUse` | `~/.gemini/config/hooks.json` |
| `opencode` | Plugin (v1 & v2) | Not yet | `~/.config/opencode/plugin/byor.ts` |
| `pi` | Extension (`tool_result`) | Not yet | `~/.pi/agent/extensions/byor.ts` |
| `skill` | — | — | `~/.agents/skills/byor/`, `~/.claude/skills/byor/`, `~/.gemini/config/skills/byor/` |

Cursor exposes no reliable post-edit hook and is omitted.

## Ownership and self-healing

Self-heal keeps disk files and `~/.local/state/byor/state.yml` aligned. User
edits are preserved:

- **JSON hooks (`claude-code`, `codex`, `antigravity`):** modifying a hook entry
  (chained command, `timeout`, matcher) hands ownership to you; byor never
  overwrites or removes it, and `byor doctor` marks it healthy.
- **Copilot (`~/.copilot/hooks/byor.json`):** owned wholesale; put custom hooks
  in another file under `~/.copilot/hooks/`.
- **Managed files (`opencode`, `pi`, `skill`):** carry
  `Managed by BYOR. Manual edits may be overwritten.` (`<!-- ... -->` or
  `// ...`). Removing the marker hands ownership to you.

## `claude-code`

Merges `PostToolUse` (`Write|Edit|MultiEdit`) and `PreToolUse` (`Bash`) into
`~/.claude/settings.json`, preserving existing keys:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "byor agent-check --stdin-hook claude-code >&2"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "byor command-check --stdin-hook claude-code"
          }
        ]
      }
    ]
  }
}
```

- Post-edit writes diagnostics to stderr (`>&2`) with exit `2` on findings.
- Pre-command writes `hookSpecificOutput.permissionDecision: "deny"` +
  `permissionDecisionReason` on stdout (exit `0`); approvals are silent.

## `codex`

Installs `PostToolUse` (`apply_patch|Edit|Write`) and `PreToolUse` (`Bash`) in
`~/.codex/hooks.json`:

- Parses `apply_patch` envelopes (`*** Add File:`, `*** Update File:`,
  `*** Move to:`) from `tool_input.command` to scope to added lines.
- Emits `hookSpecificOutput` JSON on stdout (exit `0`) for both
  `additionalContext` and `permissionDecision: "deny"`.
- **Trust step:** run `/hooks` in Codex after installing or upgrading hooks.
  Requires a Codex build that fires `PostToolUse` for `apply_patch` (builds
  through ~0.118 fired only for Bash).

## `copilot`

Writes `~/.copilot/hooks/byor.json` (`postToolUse`, `preToolUse`):

- Ignores read-only tools (`view`, `glob`, `grep`, `rg`, `str_replace_editor`
  with `command: "view"`).
- Emits `{"additionalContext": "..."}` or
  `{"permissionDecision": "deny", "permissionDecisionReason": "..."}`,
  truncating to fit Copilot's 10 KB cap.

## `antigravity`

Writes a named `"byor"` group in `~/.gemini/config/hooks.json`, preserving
sibling groups:

```json
{
  "byor": {
    "PostInvocation": [
      {
        "type": "command",
        "command": "byor agent-check --stdin-hook antigravity"
      }
    ],
    "PreToolUse": [
      {
        "matcher": "run_command",
        "hooks": [
          {
            "type": "command",
            "command": "byor command-check --stdin-hook antigravity"
          }
        ]
      }
    ]
  }
}
```

- Hooks `PostInvocation` (Antigravity's `PostToolUse` discards feedback), reads
  edit calls (`write_file`, `edit_file`, `write_to_file`,
  `replace_file_content`, `multi_replace_file_content`) since `initialNumSteps`
  from `transcriptPath`, skips internal artifacts, and returns `injectSteps`.
- Gates `run_command` via `{"decision": "deny", "reason": "..."}` on match and
  prints nothing on allow (`{}` denies; `{"decision": "allow"}` bypasses
  Antigravity's permission prompts).

## `opencode`

Writes `~/.config/opencode/plugin/byor.ts`. Runs
`byor agent-check --scope diff --files <file>` after edit tools and appends
diagnostics on exit `2` (other codes fail open). Supports both major versions:

| Mechanism | OpenCode 2 | OpenCode 1 (1.3.4+) |
| --- | --- | --- |
| Plugin entry | `setup` | `server` |
| Hook | `ctx.tool.hook("execute.after")` | `tool.execute.after` |
| Edit tools | `edit`, `write` (`path`) | `edit`, `write` (`filePath`) |
| Patch tool | `patch` (`patchText`) | `apply_patch` (`patchText`) |

## `pi`

Writes `~/.pi/agent/extensions/byor.ts`. Hooks `tool_result` for `edit` and
`write`, runs `byor agent-check --scope diff --files <file>`, and appends
diagnostics on exit `2`.

## `skill`

`byor install` renders `SKILL.md` + six `references/` files into three paths
(frontmatter keeps only `name` and `description`):

| Harness | Skill directory |
| --- | --- |
| Claude Code | `~/.claude/skills/byor/` |
| Antigravity | `~/.gemini/config/skills/byor/` |
| Codex, Pi | `~/.agents/skills/byor/` |
| Copilot CLI, OpenCode | `~/.agents/skills/byor/` (also reads `~/.claude/skills/`) |

Self-heal updates all three renders and prunes stale managed files after
upgrades (`byor doctor` reports drift under `agent_files`). Remove the marker to
own a file manually, or run `byor hook uninstall --agent skill`.
