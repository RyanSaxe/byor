# AI Agents

byor turns your ast-grep rules into directive feedback for AI coding agents,
delivered inside the agent's work loop: a post-edit hook checks each edit as
it lands, and a pre-command gate checks each shell command before it runs,
not a cleanup pass after the work is done. Every post-edit
integration wraps the same command:

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
                   [--format text|json] [--concise]
```

Runs `ast-grep scan --json=compact --include-metadata --color never` on the
given files (the whole repository when `--files` is omitted) and renders each
match with the rule's `metadata.byor.agent_prompt`, falling back to
`message` when the rule has none. It then runs any configured extra checks (see
[Extra checks](#extra-checks)) on the in-scope files, or across the whole
repository when `--files` is omitted.

Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | No diagnostics (text mode prints nothing) |
| 2 | Diagnostics found |
| 1 | Tool or configuration error |

Text output groups by file and sorts by line, then rule ID:

```text
BYOR found 1 issue.

src/example.py:3:9
Rule: python.no-typing-cast
Severity: warning
Message: Avoid typing.cast in Python code.
Code:
  3 | value = cast(int, "3")

Instruction:
Do not use typing.cast here. Fix the type by narrowing, changing the signature, introducing a protocol, or restructuring the value flow. Keep a cast only when the needed invariant cannot be expressed by Python's type system. If this is genuinely necessary, add `# ast-grep-ignore: python.no-typing-cast` on its own line directly above the offending line, with a short comment above it explaining the type-system limitation.
```

Every in-scope diagnostic is rendered: the agent sees the full set, never a
truncated sample it could mistake for the whole job. `--format json` prints all
diagnostics as `{"issues": [{"file", "line", "column", "rule_id", "severity",
"message", "code", "instruction"}, ...]}` with 1-based positions and
repo-relative paths.

`--concise` trims each diagnostic to its location and fix instruction, dropping
the code block and the redundant `Message` line. That injects fewer tokens back into
the agent on every matching edit, while keeping the guidance it needs to
self-correct:

```text
BYOR found 1 issue.

src/example.py:3:9  [warning] python.no-typing-cast
Do not use typing.cast here. Fix the type by narrowing, changing the signature, introducing a protocol, or restructuring the value flow. Keep a cast only when the needed invariant cannot be expressed by Python's type system. If this is genuinely necessary, add `# ast-grep-ignore: python.no-typing-cast` on its own line directly above the offending line, with a short comment above it explaining the type-system limitation.
```

To make it the default in every repo, including hook runs, opt in globally in
`~/.config/byor/config.yml`:

```yaml
output:
  concise: true
```

`output.max_diagnostics` caps how many diagnostics the text and hook feedback
render, appending a `... and N more` note so the agent fixes a batch at a time
instead of drowning in a long list. It is unlimited by default:

```yaml
output:
  max_diagnostics: 10
```

`--format json` is unaffected: JSON always carries every field.

`--scope` keeps only diagnostics whose lines overlap the chosen ranges
(default: `file` with `--files`, `edit` in hook mode). `diff` scopes to
uncommitted `git diff HEAD` lines: an untracked file is all new lines, and
without usable git history the whole file stays in scope. `edit` scopes to the
lines a hook payload's edit touched, so it requires `--stdin-hook`, and falls
back to `diff` then `file` when the edit contents cannot be located. Under
`edit`/`diff` scope, files missing on disk are skipped silently.

`--stdin-hook HARNESS` (claude-code|codex|copilot, instead of `--files`)
reads that harness's post-edit JSON payload on stdin, normalizes it to the
edited file(s) and edit text, and replies in the harness's own feedback format
(claude-code via stderr + exit 2; codex/copilot via a JSON envelope on
stdout). A codex payload carries the patch text in `tool_input.command` as an
`apply_patch` envelope; byor reads the changed files and their added lines
from its `*** Add File:` / `*** Update File:` sections, following `*** Move
to:` renames to the destination path. Payloads without a recognizable file, including malformed
ones, exit 0 without scanning. Hook mode resolves the repository from the
edited file, not the session's working directory: an agent editing a file in
another repo gets that repo's rules (`--repo` still overrides). In a repo with no `.byor/config.yml`, hook mode
scans the edit against your synced global rules and global checks instead;
it stays silent only when you have neither (no `~/sgconfig.yml` from
`byor install` and no global `checks:`). Hook feedback uses the same
rendering, but its summary line reads `BYOR found N issues in AI-written
code.` There the diagnostics describe the agent's own edit.

## Extra checks

`agent-check` can run extra command-line checks after ast-grep. Declare them
under `checks:` in `.byor/config.yml` (committed, shared with the team) or in
the global config (personal, every repo):

```yaml
checks:
  - name: ruff
    extensions: [py]
    run: uv run ruff check --output-format concise
    tags: [format]
```

`run` is shlex-split into argv and invoked directly (never through a shell);
the in-scope files whose extension is listed in `extensions` are appended as
trailing arguments (an empty `extensions` matches every in-scope file), so **a
check command must accept a list of file paths**. When no file paths are
provided, the command must treat that as a whole-repo scan while respecting
normal ignored-file rules; generated CI gates intentionally run checks that way,
so a check that quietly no-ops without arguments passes every CI run while
enforcing nothing.
The command runs without a shell, which keeps a committed check string from being a
shell-injection vector, so there is no `&&`, pipe, redirection, or alias.
Anything multi-step (autofix, then format, then report the rest) belongs in a
script the check points at; byor expands a leading `~`/`~/` in the command so
that script can live under `~/.config/byor` and resolve in every repo (see
[Check scripts](#check-scripts)). Checks merge by `name`: a repo check wins
over a same-named package check, which wins over a global one.
`.byor/local.yml` disables them per repo by name or by tag:

```yaml
checks:
  excluded:
    - ruff
  excluded_tags:
    - strict
```

Check tags are arbitrary user-defined labels, just like rule tags. They are
useful for profile templates and for disabling a group of checks in one repo:

```bash
byor exclude --check-tag strict
byor include --check-tag strict
byor exclude --check ruff
```

A check that exits nonzero has its raw stdout and stderr appended under a
`### <name>` header on the same channel as the diagnostics, and makes
`agent-check` exit `2`. A check whose command cannot be found prints one
warning line to stderr and is skipped. It never crashes the hook. `byor
list` and `byor doctor` show the effective checks with their origin and any
exclusions.

Scope: project checks live in committed `.byor/config.yml`, so they are shared
with anyone who works in the repo, like a committed pre-commit config, and
apply only to that repo. Global checks are your own and run in every repo you
work in. Only commit (or add) checks whose commands you trust.

### Agent-only checks

Some checks police agents, not code. A check that fails when the dependency
list changed exists so an agent asks before adding a package; a human adding
one on purpose should not be blocked by it in pre-commit or CI. Mark such a
check `gate: false`:

```yaml
checks:
  - name: dependency-gate
    extensions: [toml]
    run: ~/.config/byor/scripts/dependency-gate.sh
    gate: false
```

The post-edit hook runs it like any other check, and `byor init --gate` still
promotes it into tracked config so every contributor's agent is held to it,
but the generated `.pre-commit-config.yaml` and CI workflow leave it out. The
default is `gate: true`; existing configs are unaffected.

### Check scripts

Because `run` is a single shell-free command, anything with more than one step
goes in a script the check points at. A script also lets a check *autofix*
before it reports. The agent then spends tokens only on what it could not fix.
But an autofixing check must also **tell the agent what it changed**: the
harness already notifies the agent that "a hook modified the file," and without
a reason the agent re-reads and is surprised its code changed. So report the
fixes and exit nonzero whenever the file was touched, even when nothing is left
to fix, so byor still surfaces the note:

```sh
#!/bin/sh
# A byor `check` script: autofix what is safe, tell the agent exactly what
# changed, then report only the irreducible remainder. byor appends in-scope
# files as arguments and runs from the repo root; no arguments means scan the
# whole repo, respecting ignored files.
export NO_COLOR=1 # the agent reads this output; keep it plain text
unset FORCE_COLOR CLICOLOR_FORCE

if [ "$#" -eq 0 ]; then
  # Rebuild "$@" one line at a time so spaces and glob characters survive;
  # newlines in filenames are out of scope for this teaching example.
  while IFS= read -r file; do
    [ -n "$file" ] && set -- "$@" "$file"
  done <<EOF
$(git ls-files -co --exclude-standard -- '*.py' '*.pyi')
EOF
  [ "$#" -eq 0 ] && exit 0
fi

# F401 stays unfixable: autofixing it deletes a just-added import before the agent's next edit adds its usage.
fixed=$(uvx ruff check --fix-only --show-fixes --unfixable F401 "$@" 2>/dev/null) # apply + summarize
reformatted=""
case "$(uvx ruff format "$@" 2>&1)" in
*reformatted*) reformatted="ruff format reformatted the file(s)." ;;
esac
remaining=$(uvx ruff check --quiet --output-format concise "$@" 2>/dev/null)

[ -z "$fixed$reformatted$remaining" ] && exit 0 # already clean: stay silent
[ -n "$fixed" ] && printf 'Autofixed by ruff (no action needed):\n%s\n' "$fixed"
[ -n "$reformatted" ] && printf '%s\n' "$reformatted"
[ -n "$remaining" ] && printf 'Remaining ruff issues to fix:\n%s\n' "$remaining"
exit 1
```

A check script must accept the trailing file-path arguments, scan the whole repo
when no paths are supplied, exit nonzero when it changed a file or one still
violates, and keep its output concise and plain (it lands in the agent's context
verbatim). Put it where it is callable and matches the policy's scope:
a personal standard near the global config (`~/.config/byor/scripts/`,
referenced with `~/`); a repo policy committed in the repo
(`.byor/scripts/`, referenced by its repo-relative path, which already
resolves against the repo root). Make it executable, or name the interpreter in
`run` (`run: sh ~/.config/byor/scripts/ruff.sh`). The bundled rule-capture
skill walks an agent through authoring one when a policy fits a script better
than an ast-grep rule or an off-the-shelf tool.

Checks that outgrow one file share code as a path-referenced subprocess, never
a Python import: there is no package to import from, and `sys.path` tricks
break when scripts move between the two homes. A repo script resolves its
helper relative to itself (`Path(__file__).parent / "lib" / "helper.py"`). A
`~/` script must instead spell out the literal
`~/.config/byor/scripts/lib/helper.py` string, because gate generation vendors
scripts by following exactly those literal references, copying the helper to
`.byor/scripts/lib/helper.py` and rewriting the string in place. A
`__file__`-relative reference is invisible to that scan. byor's own Python
checks share their file discovery this way (`.byor/scripts/lib/pyfiles.py`).

## The pre-command gate

The post-edit hook covers what agents write; the pre-command gate covers what
they run. Claude Code, Codex, and Copilot expose a pre-execution hook
(`PreToolUse` / `preToolUse`) that byor installs alongside the post-edit hook:

```bash
byor command-check --stdin-hook HARNESS     # what the hook runs
byor command-check --command 'pip install x' [--repo PATH]   # test a rule by hand
```

Hook mode reads the harness's JSON payload on stdin, matches the pending
command against your `language: Bash` command rules (see
[docs/rules.md](rules.md)) and `command_checks`, and replies with the
harness's permission decision. On a match the decision is `deny` and the
reason is the rule's `agent_prompt`. The correction lands in the agent's
context, it rewrites the command, and reruns. This is what a permission
system cannot do: an allowlist says "no"; byor says "no, run this instead".

Contract details:

- **Always exit 0.** A deny is a deliberate JSON decision on stdout, never an
  exit code, so a crashed hook fails open to *allow*: a byor bug can never
  block your agent. The flip side: a broken command rule on disk silently
  disables the whole gate; `byor doctor` reports exactly that.
- **The fast path costs nothing extra.** With no command rules and no command
  checks in scope, the hook exits before spawning any subprocess. With rules,
  one `ast-grep --stdin` scan (~milliseconds) decides; total latency is
  dominated by byor's own startup, the same cost profile as the post-edit
  hook.
- **Any match denies.** There is no severity threshold: command rules are
  opt-in and exist to steer, so author them only for commands you always want
  rewritten.
- **`command_checks` are the script escape hatch.** byor pipes the pending
  command to the script's stdin; nonzero exit denies with the script's output
  as the correction. They run on every command, so keep them fast; a hanging
  check is cut off and skipped.
- **Steering, not a sandbox.** The gate corrects an agent typing a command
  plainly. `sh -c "pip install x"` embeds the command in a string the Bash
  parser correctly reads as a string, so it passes, by design. Do not present
  command rules as a security boundary; that is the harness permission
  system's job.

OpenCode and Pi do not get the gate yet: their byor integrations are post-edit
plugins, and pre-execution support there is planned separately.

## Installing and removing integrations

`byor install` sets up the agents you choose (plus the harness-neutral `skill`)
in one step; `byor hook` adds or removes a single one afterward. There is no
per-repo step: each integration writes to its agent's own config under your
home directory, so it applies in every repo you work in.

```bash
byor install --agents claude-code,codex
byor hook install --agent AGENT
byor hook uninstall --agent AGENT
```

`AGENT` is one of `claude-code`, `codex`, `copilot`, `opencode`,
`pi`, or `skill`. byor records the agents you install under `ai.agents` in
`~/.config/byor/config.yml`, which `doctor` and `hook uninstall` read.

Generated files carry the marker
`<!-- Managed by BYOR. Manual edits may be overwritten. -->` (a `//` comment
equivalent in TypeScript). `uninstall` removes only marker-bearing files;
anything you edited (the marker removed) is preserved with a message.

The global config can carry an `init:` section whose values become `byor
init`'s defaults: the pre-selected answer for each interactive prompt and the
answer used under `--non-interactive`. An explicit init flag always overrides
the global default.

```yaml
init:
  private: true
  git_hooks: true
  gate: true
```

## Per-agent status

Each integration lands in its agent's own configuration under your home directory.

| Agent | Integration |
| --- | --- |
| `claude-code` | Real `PostToolUse` + `PreToolUse` hooks (`~/.claude/settings.json`) |
| `codex` | Real `PostToolUse` + `PreToolUse` hooks (`~/.codex/hooks.json`); trust them via `/hooks` |
| `copilot` | Real `postToolUse` + `preToolUse` hooks (`~/.copilot/hooks/byor.json`) |
| `opencode` | Real `tool.execute.after` plugin (`~/.config/opencode/plugin/byor.ts`) |
| `pi` | Real `tool_result` extension (`~/.pi/agent/extensions/byor.ts`) |
| `skill` | The `byor` skill (hub `SKILL.md` + `references/`) rendered into `~/.agents/skills/byor/` and `~/.claude/skills/byor/`; installed by `byor install` by default |

Codex, Copilot, OpenCode, and Pi auto-discover the `byor` skill from
`~/.agents/skills/byor/SKILL.md`, so they get the capture loop natively.

Cursor and Antigravity are not supported: neither exposes a post-edit hook that
byor can reliably integrate with, so byor omits them until that changes.

### skill

The `byor` skill is a hub `SKILL.md` plus reference files
(`references/patterns.md`, `references/checks.md`, `references/setup.md`,
`references/packages.md`, `references/profiles.md`),
following the Agent Skills progressive-disclosure pattern so the shared
rule-authoring guidance lives in one place. It does two things: **capture** (the
default) and **setup**.

#### Capture

The hub teaches agents to *create* rules from your feedback, not just obey them.
When you voice a durable, mechanically checkable preference about code syntax or
structure ("never use X", "always do Y"), the agent:

1. **Drafts** a complete ast-grep rule: id, language, severity, message,
   `rule.pattern`, and `metadata.byor` with a rationale, an imperative
   `agent_prompt` for future AI readers, and tags.
2. **Proposes a scope** (`project` for team policy voiced about this
   codebase, `global` for personal preferences that transcend the repo,
   `local` for experiments), then shows you the drafted rule.
3. **Confirms** with exactly one question (covering the rule, the scope, and
   whether exceptions are acceptable) before writing anything. No rule is
   ever created from an offhand remark. When you allow exceptions, the drafted
   `agent_prompt` ends with the standard suppression sentence.
4. **Creates** the rule via `byor add --scope SCOPE --from FILE` (which
   validates, syncs, and runs doctor), then **verifies** it by running
   `ast-grep scan` against an in-repo example of the violation.

The skill tree that drives this ships with byor as Markdown (`byor/data/skill/`);
when a feedback policy is really better solved by a linter, type checker, or
formatter (line length, import order, a wrong type), `references/checks.md` has
the agent say so and offer to configure that tool and wire it in: as a byor
`check`, in CI, or as a pre-commit hook. A preference no tool can express (naming
philosophy, architectural taste) is declined with a pointer to the harness's
own instruction file (CLAUDE.md, AGENTS.md, …).

#### Setup

`references/setup.md` drives onboarding. After the one-time bootstrap
(`uv tool install byor && byor install`), say "set up byor" in your agent and it
verifies the install with `byor doctor` (repairing or adding harnesses by
re-running `byor install`), optionally runs `byor init` if you want repo-scoped
or team-shared rules, and, with your say-so, scans your existing instruction
files (CLAUDE.md, AGENTS.md, copilot-instructions.md), proposes rules for the
mechanically checkable preferences, and creates the ones you approve. For an
existing repo it can also run a one-time cleanup pass on an isolated branch so
you adopt byor on a clean tree instead of a wall of diagnostics. It never edits
your instruction files unless you ask.

### The skill is byor-owned

The skill is global, one render per machine, and lives at two paths because
no single one is read by every harness:

| Harness | Reads the skill from |
| --- | --- |
| Claude Code | `~/.claude/skills/byor/SKILL.md` |
| Codex, Pi | `~/.agents/skills/byor/SKILL.md` |
| Copilot, OpenCode | `~/.agents/skills/byor/SKILL.md`; also read `~/.claude/skills/` |

byor owns both renders, like the OpenCode plugin or the generated rule copies.
It writes them from the packaged skill tree (`byor/data/skill/`) and keeps them
current with the **same self-heal that runs on most byor commands**: any file
(hub or reference) that drifts from the installed byor's skill is silently
rewritten, so there is no refresh command to remember. The exception is
`byor doctor`, which is read-only: it reports a drifted render as an
`agent_files` failure instead of rewriting it.

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

`edit` and `write` name the touched file in `filePath`; `apply_patch`, the
only edit tool some models (e.g. GPT-5) use, instead carries a `patchText`,
so the plugin reads the changed paths from its `*** Add File:` / `*** Update
File:` markers and checks each. A file changed another way (for example via a
shell command) is not auto-checked.

### pi

Pi supports real post-edit hooks through TypeScript extensions. Install writes
`~/.pi/agent/extensions/byor.ts`. The extension hooks the `tool_result` event
for the `edit` and `write` tools, runs
`byor agent-check --scope diff --files <file>` on the touched file, and appends
any diagnostics to the tool result the model sees. Pi already reads skills from
`~/.agents/skills/`, so it discovers the rule-capture skill with no Pi-specific
work.

### codex

Install writes a `PostToolUse` hook (matcher `apply_patch|Edit|Write`) and a
`PreToolUse` gate (matcher `Bash`) into `~/.codex/hooks.json`. Codex edits
files through `apply_patch` (its real `tool_name`); `Edit`/`Write` remain in
the matcher as Codex's documented aliases. Codex reports shell commands under
`tool_name: "Bash"` in the `PreToolUse` payload, the same name Claude Code
uses, regardless of the underlying exec handler (`exec_command`,
`unified_exec`), so the gate matches on `Bash` (verified live against codex
0.144). Codex does not run new or changed hooks until you trust them: run
`/hooks` in the Codex session and approve the byor entries, again after an
upgrade adds a hook, which `byor hook install --agent codex` reminds you of.

Codex must be recent enough that `apply_patch` edits fire `PostToolUse` hooks:
older versions (through ~0.118) fired them only for the Bash tool, so byor saw
no edits there. A newly-installed gate is silent until trusted; a command byor
never sees (a shell handler that doesn't emit `PreToolUse`) is simply
allowed, consistent with the gate's fail-open design.

### claude-code

Install merges a `PostToolUse` hook into `~/.claude/settings.json`, creating it
if absent and preserving existing keys and hook groups:

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
    ]
  }
}
```

Claude Code pipes the tool-call JSON to the hook on stdin (which
`--stdin-hook claude-code` parses) and, on exit 2, feeds the hook's stderr back
to the model, hence `>&2`. `agent-check` exits 2 exactly when there are
diagnostics, so the instructions reach the model only when something needs
fixing.

The pre-command gate lands next to it as a `PreToolUse` group (matcher
`Bash`, command `byor command-check --stdin-hook claude-code`; no `>&2`
because the gate replies with a JSON permission decision on stdout):

```json
{
  "hooks": {
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

A deny replies with `hookSpecificOutput.permissionDecision: "deny"` and the
correction in `permissionDecisionReason`, which Claude Code feeds back to the
model; an allow prints nothing. Either way the hook exits 0.

Install is idempotent, and upgrading from a post-edit-only install adds the
`PreToolUse` group without touching the existing one. `uninstall` removes only
hook groups whose every command is byor's; a group you mixed your own hooks
into counts as user-edited and stays.
