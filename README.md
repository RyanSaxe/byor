# byor

**Build Your Own Rules.** Write a custom lint rule once and it becomes a live
diagnostic in your terminal, your editor, and your AI agent's feedback loop —
no language server to build, no rules engine to run.

byor is a small CLI around [ast-grep](https://ast-grep.github.io), a fast
structural search-and-lint tool. ast-grep already does the hard parts:
`ast-grep scan` lints from the terminal, and `ast-grep lsp` is a real language
server that shows diagnostics inside your editor. byor doesn't wrap either of
them. It arranges plain rule files and plain configuration so those tools just
work — and then adds what ast-grep alone doesn't: sharing rules across all your
repositories, and feeding their diagnostics back into AI coding agents. No
daemon, no second rule language, no custom editor protocol.

```yaml
# .byor/rules/project/no-print.yml — one rule, enforced everywhere
id: no-print
language: python
severity: warning
message: Use the logging module, not print, in library code.
rule:
  pattern: print($$$ARGS)
```

## Install

```bash
uv tool install byor    # once per machine (or `uvx byor` to try without installing)
byor install            # once: register the skill + your AI agents' post-edit hooks
byor init               # in a repo: set up project rules
```

byor needs only Python 3.11+. Installed as a [uv](https://docs.astral.sh/uv/)
tool it lives in your user environment, not a project's dependencies, so it
works in any repo whatever the language. ast-grep ships with byor; to use a
different build, put `ast-grep` on `PATH` or point `$BYOR_AST_GREP` at it.

`byor install` is one-time setup: it registers the rule-capture skill and a
post-edit hook for the AI agents you use, and points ast-grep at your personal
rule library so those rules apply in every repo — even ones without byor. `byor
init` then adds a repository's own pieces — `sgconfig.yml`, the `.byor/` rule
directories, a gitignore entry — after which `ast-grep scan` and `ast-grep lsp`
work on their own.

## In your terminal and editor

A rule under `.byor/rules/` is a normal ast-grep rule, so the normal ast-grep
tools see it:

```bash
ast-grep scan            # lint the whole repo from the terminal
ast-grep scan src/       # …or just a path
```

For **live in-editor diagnostics**, run ast-grep's language server. Point your
editor's ast-grep / LSP integration at `ast-grep lsp` and your custom rules
light up as you type — squiggles, hovers, and quick navigation, exactly like a
built-in linter, for rules you wrote in a few lines of YAML. ast-grep reloads
rule files as you change them, so editing a rule updates the editor without a
restart. ([ast-grep editor setup](https://ast-grep.github.io/guide/tools/editors.html).)

This is the payoff of staying on plain ast-grep files: the terminal and the
editor are first-class for free, and byor never has to be running.

## Rule scopes

The same rule format lives at three scopes:

| Scope | Lives in | Shared with |
| --- | --- | --- |
| `project` | `.byor/rules/project/` | Your team (committed) |
| `local` | `.byor/rules/personal/local/` | You, this repo only |
| `global` | `~/.config/byor/rules/` | You, in every repo |

Global rules are your personal standards; byor makes them apply everywhere.
Project and local rules override a global rule with the same ID, so a team
policy or a local experiment takes precedence. See [docs/rules.md](docs/rules.md)
for the rule format and the `add` / `edit` / `promote` / `exclude` workflow.

## With AI coding agents

Agents don't just have to obey your rules — they can write them, and they get
told the moment they break one.

- **Feedback loop.** A post-edit hook runs `byor agent-check` after the agent
  edits a file and feeds any diagnostics straight back into its context, scoped
  to the lines it just touched, so it fixes violations before moving on.
- **Rule capture.** byor installs a skill that turns durable feedback —
  "never do this", "always do that" — into an ast-grep rule: the agent drafts
  the rule, confirms with a single question, and creates it with `byor add`.
  The skill also knows when a linter, type checker, or formatter is the better
  tool and offers to set that up instead.

`byor install` wires up the agents you pick; `byor hook` adds or drops one
later. Both write to each agent's own config, so the integration follows you
into every repo:

```bash
byor install --agents claude-code,codex
byor hook install --agent cursor          # add one more, anytime
```

byor supports six harnesses:

| Harness | Skill | Real hook | Diagnostic precision |
| --- | --- | --- | --- |
| Claude Code | yes | `PostToolUse` | the edited lines |
| Codex | yes | `PostToolUse` | the patched lines |
| Copilot CLI | yes | `postToolUse` | best-effort path |
| Cursor | yes | `postToolUse` | the edited lines |
| OpenCode | yes | `tool.execute.after` plugin | the changed file |
| Pi | yes | `tool_result` extension | the changed file |

Beyond ast-grep rules, a `checks:` section (in `.byor/config.yml` or your
global config) runs extra command-line tools — `ruff`, a type checker, anything
— on the changed files and folds their output into the same agent feedback. See
[docs/ai-agents.md](docs/ai-agents.md).

## Why byor copies rules instead of symlinking them

byor mirrors your global rules into each repo's
`.byor/rules/personal/global/` rather than symlinking a shared directory. The
reason is the override model above: because each repo holds its own copy, a
project or local rule can shadow a global one by ID, a rule can be promoted from
global into the project, and a teammate's committed rule can win cleanly — none
of which a single shared symlink target allows. Plain copies are also what let
a fresh clone lint with **zero byor installed**. (A symlinked *directory* would
load, but ast-grep does not follow symlinked rule *files*, so copies are also
the only thing that reliably works.)

The copies are a build artifact byor owns. They can go stale, but staleness is
self-healing rather than something you police: every byor command syncs the
current repo first, and `byor sync --all` heals every registered repo. See
[docs/sync-model.md](docs/sync-model.md).

## Continuous integration

Project rules are committed plain files, so CI gates on them with **no byor
installed** — a fresh clone already has everything `ast-grep scan` needs.
Install ast-grep and scan with `--error` so warnings fail the build (a plain
scan exits 0 on warnings):

```yaml
- run: npm install -g @ast-grep/cli
- run: ast-grep scan --error
```

See [docs/sync-model.md](docs/sync-model.md) for the full workflow and why a
fresh clone needs no byor.

## Commands

```text
byor install        Register byor's AI integrations globally (one-time)
byor init           Initialize byor in a repository
byor sync           Mirror enabled global rules into the repository
byor doctor         Validate installation health
byor add            Create a new rule in a scope
byor edit           Open an existing rule in $EDITOR
byor remove         Delete a rule from its scope
byor promote        Move a personal rule into shared project rules
byor exclude        Disable a global rule in this repository
byor include        Re-enable a previously excluded global rule
byor list           Show rules and where they come from
byor agent-check    Run ast-grep on changed files and render agent feedback
byor hook           Install or uninstall AI agent integrations
```

Every command takes `--help`, and repo-operating commands take `--repo PATH`
(default: search upward from the current directory).

## Documentation

- [docs/rules.md](docs/rules.md) — rule format, scopes, and the rule workflow
- [docs/ai-agents.md](docs/ai-agents.md) — AI agent integration and `agent-check`
- [docs/sync-model.md](docs/sync-model.md) — copies, self-healing, and git hooks
