<p align="center">
  <img src="https://raw.githubusercontent.com/RyanSaxe/byor/main/assets/byor_banner.png" alt="byor: Build Your Own Rules" width="100%">
</p>

<p align="center">
  <a href="https://github.com/RyanSaxe/byor/actions/workflows/ci.yml"><img src="https://github.com/RyanSaxe/byor/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/byor/"><img src="https://img.shields.io/pypi/v/byor" alt="PyPI"></a>
  <a href="https://pypi.org/project/byor/"><img src="https://img.shields.io/pypi/pyversions/byor" alt="Python versions"></a>
  <a href="LICENSE"><img src="https://img.shields.io/pypi/l/byor" alt="License"></a>
</p>

Your AI agent keeps breaking rules you have already given it. You say arguments
past the first couple should be keyword-only; it agrees and listens, but then
later in the session it writes `create_user(name, email, True, False, None)`. So
you add a line to your ever-growing `AGENTS.md` in the hope it fixes it. **It doesn't.**

> `byor` is the sheepdog for your flock of coding agents: you set the rules and it
> reins in any agent that attempts to stray in real time. 

`byor` can do this reliably because the rules it creates are real executable checks, 
not markdown prompts. You rarely write one of these rules by hand. If you tell your 
agent to create a rule, or even give it critical feedback about code it has written,
it will use `byor`'s skill to create the best automated system to keep your agent in
check. Here is an example:

```yaml
# .byor/rules/project/keyword-only-args.yml
id: keyword-only-args
language: python
severity: warning
message: Arguments after the first two must be keyword-only. Add `*` so callers pass them by name.
rule:
  kind: parameters
  any:
    - all:  # a function whose third argument is still positional
        - has: { nthChild: 3, any: [{kind: identifier}, {kind: typed_parameter}, {kind: default_parameter}, {kind: typed_default_parameter}] }
        - not: { has: { nthChild: 1, regex: "^(self|cls)$" } }
    - all:  # a method, where self/cls shifts the limit to the fourth slot
        - has: { nthChild: 1, regex: "^(self|cls)$" }
        - has: { nthChild: 4, any: [{kind: identifier}, {kind: typed_parameter}, {kind: default_parameter}, {kind: typed_default_parameter}] }
metadata:
  byor:
    agent_prompt: >
      Put a bare `*` after the second parameter so the rest are keyword-only,
      e.g. def f(a, b, *, c, d), and pass them by name at the call sites.
```

A rule like this is a structural [ast-grep](https://ast-grep.github.io) check,
and `byor` is set up so this naturally just works wherever you do:

- **IDE** — set up your IDE with `ast-grep lsp` to see `message` as a diagnostic.
- **AI agent** — a post-edit hook hands over the `agent_prompt`, scoped to
  the lines it changed, so the agent fixes the violation before moving on.
- **Terminal** — `ast-grep scan` shows the `message`.

ast-grep rules are `byor`'s built-in kind; it also runs any linter, type checker, or
script you already use and folds their output into the same agent feedback. This
rule and others live in [examples/](examples/), exercised in CI.

## Install

```bash
uv tool install byor && byor install   # install the CLI, then set up the skill + agent hooks (once)
byor init                              # optional — only for repo-scoped or shared rules (see below)
```

`byor` bundles ast-grep, so Python 3.11+ is all you need to *run* it — the rules
themselves work in any language ast-grep supports (TypeScript, Go, Rust, and
more), not just Python. `byor install` registers your editor and agent
integrations machine-wide. `byor init` is **optional**: run it only when you want
rules or checks scoped to a repository, or shared with contributors — your
personal global rules and checks already work in every repo without it.
[docs/ai-agents.md](docs/ai-agents.md) covers what each step writes.

After that one-time bootstrap, let your AI coding agent handle the rest: open it
in the repo and say **"set up byor"**. The skill verifies the install, runs
`byor init` if you want repo or team rules, and offers to import the preferences
you already wrote in your CLAUDE.md / AGENTS.md as enforced rules.

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

Tags in `metadata.byor.tags` are arbitrary labels you own. byor uses them for
listing, profile setup, and repo-local exclusions; it does not reserve any tag
names. Use `byor list --tags` to see the vocabulary already present in a repo.

Profiles are named templates in your global config that apply private
repo-local exclusions at init time, or later with `byor profile add`. They are
useful when a repo should opt out of broad groups of global rules or checks
without deleting those personal standards everywhere:

```yaml
profiles:
  existing:
    description: Low-friction defaults for mature repositories.
    rules:
      excluded_tags:
        - legacy-risk
    checks:
      excluded_tags:
        - strict
```

## With AI coding agents

Agents can both obey your rules and write new ones:

- **Feedback.** A post-edit hook runs `byor agent-check` after the agent edits a
  file and feeds the diagnostics back into its context, scoped to the lines it
  changed, so it fixes violations before moving on.
- **Capture.** A bundled skill turns durable feedback ("never do this", "always
  do that") into an ast-grep rule: the agent drafts it, confirms once, and runs
  `byor add`. When a linter or type checker fits better, the skill offers that
  instead.
- **Setup.** The same skill onboards you: say "set up byor" and it checks the
  install, optionally inits the repo, and imports the mechanically checkable
  preferences from your existing CLAUDE.md / AGENTS.md as rules — and can clean up
  an existing repo on a throwaway branch so you start without a wall of warnings.

`byor install` wires up the agents you pick (once, machine-wide); `byor hook`
adds or drops one later.

```bash
byor install --agents claude-code,codex
byor hook install --agent copilot       # add an agent later
byor hook uninstall --agent copilot     # or remove one (--agent skill removes the skill)
```

byor supports five harnesses:

| Harness | Skill | Real hook | Diagnostic precision |
| --- | --- | --- | --- |
| Claude Code | yes | `PostToolUse` | the edited lines |
| Codex | yes | `PostToolUse` | the edited lines |
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
byor init --private Keep byor to yourself; commit nothing (git info/exclude)
byor init --gate    Distribute a byor-free pre-commit + CI gate to the team
byor hook           Add or remove an agent integration
byor doctor         Check that everything is wired up
byor profile        List or apply configured profiles
byor package        List or install opt-in rule/check packages
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

## What's next

Today byor catches strays with one mechanism: a deterministic post-edit hook.
That already covers anything a rule, a linter, or a type checker can express.

The harder strays are behavioral, not textual: an agent drifting off the plan you
agreed on, stopping a loop early, editing files outside the scope you set.
Teaching the sheepdog to herd those too is where byor is headed. If there is a
rule you wish it could enforce, [open an issue](https://github.com/RyanSaxe/byor/issues).

## Documentation

- [docs/rules.md](docs/rules.md) — rule format, scopes, and the rule workflow
- [docs/ai-agents.md](docs/ai-agents.md) — AI agent integration and `agent-check`
- [docs/sync-model.md](docs/sync-model.md) — copies, self-healing, and git hooks
- [examples/](examples/) — reference rules (simple → advanced) and config setups
