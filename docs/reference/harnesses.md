# Harnesses

`byor install` registers your selected AI coding harnesses and the shared `byor`
skill once per machine; `byor hook install --agent AGENT` and
`byor hook uninstall --agent AGENT` add or remove individual integrations
afterward. Integrations live under your home directory and apply in every
repository.

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

Cursor exposes no reliable post-edit hook, so byor omits it.

## Ownership and self-healing

byor records installed agents in `~/.local/state/byor/state.yml`. Self-heal
keeps disk files and `state.yml` aligned: it refreshes missing or drifted
integrations for recorded agents and records agents whose unmodified byor hooks
or plugins it finds on disk. Use `byor hook uninstall --agent AGENT` to remove
both sides cleanly.

User edits are preserved:

- **JSON hook entries (`claude-code`, `codex`, `antigravity`):** Any hook entry
  you modify (chained command, added `timeout`, changed matcher) becomes
  user-owned. byor never overwrites or removes it, and `byor doctor` treats it
  as healthy.
- **Copilot (`~/.copilot/hooks/byor.json`):** Owned wholesale by byor; keep
  custom Copilot hooks in a separate file under `~/.copilot/hooks/`.
- **Managed files (`opencode`, `pi`, `skill`):** Carry the marker
  `Managed by BYOR. Manual edits may be overwritten.` (`<!-- ... -->` in
  Markdown, `// ...` in TypeScript). Removing the marker hands ownership to you;
  `uninstall` and self-heal leave unmarked files alone.

## `claude-code`

Merges `PostToolUse` (matcher `Write|Edit|MultiEdit`) and `PreToolUse` (matcher
`Bash`) groups into `~/.claude/settings.json`, preserving all existing keys and
hook groups:

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

- Post-edit feedback uses stderr plus exit code `2` (hence `>&2`), so Claude
  Code injects diagnostics into the model's context only when issues exist.
- Pre-command denials write `hookSpecificOutput.permissionDecision: "deny"` and
  `permissionDecisionReason` on stdout and exit `0`; approvals stay silent.

## `codex`

Installs `PostToolUse` (matcher `apply_patch|Edit|Write`) and `PreToolUse`
(matcher `Bash`) hooks into `~/.codex/hooks.json`:

- Parses `apply_patch` envelopes (`*** Add File:`, `*** Update File:`,
  `*** Move to:`) from `tool_input.command` to scope diagnostics to added lines.
- Emits `hookSpecificOutput` JSON on stdout (exit `0`) for both post-edit
  `additionalContext` and pre-command `permissionDecision: "deny"`.
- **Manual trust step:** Codex does not execute new or updated hooks until you
  approve them. Run `/hooks` in a Codex session after installing or upgrading
  byor hooks. Codex must be recent enough to fire `PostToolUse` for
  `apply_patch` (older builds through ~0.118 fired only for Bash).

## `copilot`

Writes `~/.copilot/hooks/byor.json` with `postToolUse` and `preToolUse` entries:

- Ignores read-only Copilot tools (`view`, `glob`, `grep`, `rg`, and
  `str_replace_editor` with `command: "view"`).
- Emits `{"additionalContext": "..."}` for post-edit findings and
  `{"permissionDecision": "deny", "permissionDecisionReason": "..."}` for
  pre-command denials, truncating cleanly to fit Copilot's 10 KB context cap.

## `antigravity`

Writes a named `"byor"` entry in `~/.gemini/config/hooks.json`, preserving
sibling named hook groups:

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

- Hooks `PostInvocation` (because Antigravity's `PostToolUse` discards feedback
  output), reading completed edit tool calls (`write_file`, `edit_file`,
  `write_to_file`, `replace_file_content`, `multi_replace_file_content`) since
  `initialNumSteps` from `transcriptPath`, skipping internal artifact files, and
  returning diagnostics via `injectSteps`.
- Gates `run_command` under `PreToolUse`, emitting
  `{"decision": "deny", "reason": "..."}` on match and printing nothing on allow
  (returning `{}` denies the call, while `{"decision": "allow"}` would bypass
  Antigravity's own permission prompts).

## `opencode`

Writes `~/.config/opencode/plugin/byor.ts`. After a file-editing tool finishes,
runs `byor agent-check --scope diff --files <file>` and appends diagnostics to
the tool result on exit `2` (other exit codes fail open).

The default export supports both OpenCode major versions:

| Mechanism | OpenCode 2 | OpenCode 1 (1.3.4+) |
| --- | --- | --- |
| Plugin entry | `setup` | `server` |
| Hook | `ctx.tool.hook("execute.after")` | `tool.execute.after` |
| Edit tools | `edit`, `write` (`path`) | `edit`, `write` (`filePath`) |
| Patch tool | `patch` (`patchText`) | `apply_patch` (`patchText`) |

## `pi`

Writes `~/.pi/agent/extensions/byor.ts`. Hooks the `tool_result` event for
`edit` and `write`, runs `byor agent-check --scope diff --files <file>`, and
appends diagnostics to the tool result on exit `2`.

## `skill`

`byor install` always installs the bundled `byor` skill (`SKILL.md` plus six
reference files under `references/`) into three locations so every supported
harness discovers it:

| Harness | Skill directory |
| --- | --- |
| Claude Code | `~/.claude/skills/byor/` |
| Antigravity | `~/.gemini/config/skills/byor/` |
| Codex, Pi | `~/.agents/skills/byor/` |
| Copilot CLI, OpenCode | `~/.agents/skills/byor/` (also reads `~/.claude/skills/`) |

Frontmatter contains only `name` and `description` for cross-harness
compatibility. Self-heal keeps all three renders current and prunes stale
marker-bearing files after byor upgrades; `byor doctor` reports drifted renders
under `agent_files`. Remove the marker from a file to maintain it manually, or
run `byor hook uninstall --agent skill` to remove managed renders.
