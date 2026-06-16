<p align="center">
  <img src="https://raw.githubusercontent.com/RyanSaxe/byor/main/assets/banner.png" alt="byor: Build Your Own Rules" width="600">
</p>

Write a custom lint rule once and it becomes a live diagnostic in your terminal,
your editor, and your AI agent's feedback loop.

byor is a small CLI around [ast-grep](https://ast-grep.github.io), a fast
structural search-and-lint tool. `ast-grep scan` lints from the terminal and
`ast-grep lsp` shows diagnostics in your editor; byor arranges plain rule files
and configuration so both work without setup, then adds the two things ast-grep
leaves out: sharing rules across your repositories, and feeding their
diagnostics back into AI coding agents.

```yaml
# .byor/rules/project/no-print.yml
id: no-print
language: python
severity: warning
message: Use the logging module, not print, in library code.
rule:
  pattern: print($$$ARGS)
metadata:
  byor:
    agent_prompt: >
      Replace this print with a module-level logger,
      logging.getLogger(__name__), at the appropriate level.
```

`message` is what you read in the terminal and editor; `agent_prompt` is the
directive byor hands your AI agent when it trips the rule.

## Install

```bash
uv tool install byor    # or `uvx byor` to try without installing
byor install            # set up the skill + your agents' hooks (once)
byor init               # add byor to a repo
```

byor bundles ast-grep, so Python 3.11+ is the only requirement. `byor install`
registers your editor and agent integrations machine-wide; `byor init` adds a
repository's rule directories and `sgconfig.yml`.
[docs/ai-agents.md](docs/ai-agents.md) covers what each step writes.

You can also let your AI coding agent handle it: open it in the repo and ask it
to set up byor.

## Terminal and editor

A rule under `.byor/rules/` is an ordinary ast-grep rule, so the ordinary tools
read it:

```bash
ast-grep scan            # lint the repo
ast-grep scan src/       # ...or a path
```

For live in-editor diagnostics, point your editor's ast-grep integration at
`ast-grep lsp`: rules light up as you type and reload when you edit them.
([editor setup](https://ast-grep.github.io/guide/tools/editors.html).)

## Rule scopes

The same rule format lives at three scopes:

| Scope | Lives in | Shared with |
| --- | --- | --- |
| `project` | `.byor/rules/project/` | Your team (committed) |
| `local` | `.byor/rules/personal/local/` | You, this repo only |
| `global` | `~/.config/byor/rules/` | You, in every repo |

Global rules are your personal standards; byor makes them apply in every repo.
Project and local rules override a global rule with the same ID, so a team
policy or a local experiment takes precedence. See [docs/rules.md](docs/rules.md)
for the rule workflow and [docs/sync-model.md](docs/sync-model.md) for how byor
copies global rules into each repo.

## With AI coding agents

Agents can both obey your rules and write new ones:

- **Feedback.** A post-edit hook runs `byor agent-check` after the agent edits a
  file and feeds the diagnostics back into its context, scoped to the lines it
  changed, so it fixes violations before moving on.
- **Capture.** A bundled skill turns durable feedback ("never do this", "always
  do that") into an ast-grep rule: the agent drafts it, confirms once, and runs
  `byor add`. When a linter or type checker fits better, the skill offers that
  instead.

`byor install` wires up the agents you pick (once, machine-wide); `byor hook`
adds or drops one later.

```bash
byor install --agents claude-code,codex
byor hook install --agent copilot
```

byor supports five harnesses:

| Harness | Skill | Real hook | Diagnostic precision |
| --- | --- | --- | --- |
| Claude Code | yes | `PostToolUse` | the edited lines |
| Codex | yes | `PostToolUse` | the patched lines |
| Copilot CLI | yes | `postToolUse` | the edited lines |
| OpenCode | yes | `tool.execute.after` plugin | the changed file |
| Pi | yes | `tool_result` extension | the changed file |

Cursor and Antigravity are not supported: neither exposes a post-edit hook that
byor can reliably integrate with, so byor omits them until that changes.

A `checks:` section in `.byor/config.yml` (or your global config) runs extra
command-line tools (a linter, a type checker, anything) on the changed files and
folds their output into the same feedback. See
[docs/ai-agents.md](docs/ai-agents.md).

## Continuous integration

Project rules are committed files that work with `ast-grep`, so CI doesn't need
`byor`: a fresh clone already has everything `ast-grep scan` reads. Scan with
`--error` so warnings fail the build (a plain scan exits 0 on warnings):

```yaml
- run: npm install -g @ast-grep/cli
- run: ast-grep scan --error
```

## Commands

**Setup.** You run these once to get going.

```text
byor install        Register byor's AI integrations (machine-wide)
byor init           Initialize byor in a repository
byor hook           Add or remove an agent integration
byor doctor         Check that everything is wired up
```

**Rules.** Your agent runs these as it captures and manages rules for you.

```text
byor add            Create a rule in a scope
byor list           Show rules and where they come from
byor edit           Open a rule in $EDITOR
byor remove         Delete a rule
byor promote        Move a personal rule into shared project rules
byor exclude        Disable a global rule in this repository
byor include        Re-enable an excluded global rule
```

**Automatic.** byor runs these itself: the post-edit hook and self-heal.

```text
byor agent-check    Render diagnostics for your agent
byor sync           Mirror global rules into the repo
```

Every command takes `--help`, and repo-operating commands take `--repo PATH`
(default: search upward from the current directory).

## Documentation

- [docs/rules.md](docs/rules.md) — rule format, scopes, and the rule workflow
- [docs/ai-agents.md](docs/ai-agents.md) — AI agent integration and `agent-check`
- [docs/sync-model.md](docs/sync-model.md) — copies, self-healing, and git hooks
