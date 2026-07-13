<p align="center">
  <img src="https://raw.githubusercontent.com/RyanSaxe/byor/main/assets/byor_banner.png" alt="byor: Build Your Own Rules" width="100%">
</p>

<p align="center">
  <a href="https://github.com/RyanSaxe/byor/actions/workflows/ci.yml"><img src="https://github.com/RyanSaxe/byor/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/byor/"><img src="https://img.shields.io/pypi/v/byor" alt="PyPI"></a>
  <a href="https://pypi.org/project/byor/"><img src="https://img.shields.io/pypi/pyversions/byor" alt="Python versions"></a>
  <a href="LICENSE"><img src="https://img.shields.io/pypi/l/byor" alt="License"></a>
</p>

Custom code rules for the conventions your linter won't enforce.

byor lets you write the checks a general linter leaves out: rules specific
to your team, your codebase, and your taste. It runs them in your editor, in CI,
and inside your AI agent's loop as it writes.

The recommended practice for getting AI Agents to follow a styleguide is putting
your standards in a markdown file (e.g. AGENTS.md or a skill). But, in practice,
this is unreliable. byor exists because agents don't follow prose. If you set up
byor as an agentic hook, it will send diagnostic messages whenever your agent
breaks a rule, so the required correction is always in context.

> byor is the sheepdog for your flock of coding agents: you set the rules, and
> it reins in any that stray, while the work is still happening.

## A Simple Example

When I was recently reviewing code from `codex`, it was completely littered with
pass-through wrappers: functions that existed to route arguments to other functions.

```python
def get_user(user_id: int) -> User:
    return fetch_user(user_id)
```

Whether a pass-through wrapper is worth banning is a matter of taste, and personally
I think they just make code harder to read. So I built a rule that bans writing code
like this, and now my agents never commit the mistake again.

```yaml
# .byor/rules/project/no-routing-functions.yml
# NOTE: this is a simplification of the full rule for the README
#       you can view the full rule here: examples/rules/no-routing-functions.yml
id: no-routing-functions
language: Python
severity: warning
message: A function whose only job is forwarding a call.
rule:
  kind: function_definition
  has:
    field: body
    has:
      kind: return_statement
      pattern: return $CALLEE($$$ARGS)
      all:
        - nthChild: 1
        - nthChild: { position: 1, reverse: true }
metadata:
  byor:
    agent_prompt: >
      Call the implementation directly, or give the function real behavior.
      Don't keep a pure pass-through.
```

You rarely write these by hand. Tell your agent the rule in plain language and
it writes the check and will stress test and iterate with you to refine it.

## Install

```bash
uv tool install byor    # the CLI (bundles ast-grep)
byor install            # editor + agent integrations, once per machine
```

The above commands will set up byor globally. After doing so, open up your coding agent
and ask it to help set you up. It will likely read your markdown files for rules it can
codify, as well as offer to run `byor init` if you would like to separate the rules of
your repository from your global config.

## Where rules run

A rule you write once runs in four places:

- **Editor:** a diagnostic while you type ([`ast-grep lsp`](https://ast-grep.github.io/guide/tools/editors.html)).
- **Terminal:** `ast-grep scan`.
- **CI:** committed rules run with plain `ast-grep`. `byor init --gate` creates the `.github` workflow.
- **AI agents:** a post-edit hook corrects each edit as it lands, and a pre-command gate corrects shell commands before they run, scoped to what changed.

## What a rule can be


| Kind | Catches | Example |
| --- | --- | --- |
| **ast-grep rule** | a call, import, or code structure like the wrapper above | [no-requests](examples/rules/no-requests.yml) · [no-routing-functions](examples/rules/no-routing-functions.yml) |
| **Check** | whatever a linter, type checker, or script decides | [dependency-gate](examples/config/scripts/dependency-gate.sh) |
| **Command** | a shell command, before it runs | [no-pip-install](examples/command-rules/no-pip-install.yml) |


Every example runs in CI. More in [examples/](examples/) and [docs/rules.md](docs/rules.md).

## Scopes

A rule can be yours alone or committed for the whole team. Committing it turns a
preference into a standard: the rule is version-controlled, reviewed like any
other change, and applied the same way for everyone, human or agent.


| Scope | Lives in | Shared with |
| --- | --- | --- |
| `project` | `.byor/rules/project/` | Your team (committed) |
| `local` | `.byor/rules/personal/local/` | You, this repo |
| `global` | `~/.config/byor/rules/` | You, every repo |


A project or local rule overrides a global rule with the same ID. Command rules
follow the same scopes under `.byor/commands/`. Packages and profiles tune which
rules apply where; see [docs/rules.md](docs/rules.md).

## AI agents

### Another Quick Example

Agents are good at making an error disappear instead of fixing them. I ran into
this a lot with pre-commit hooks. There would be a large number of errors it would
have to resolve, and it would resolve many, but often would choose to silence some.

Moving from `pre-commit` to `byor` hooks improved this because the agent wouldn't
get lazy as a function of having so many diagnostics at once. However, it didn't
solve the problem. Luckily, we can create rules that tell the agent not to silence!

```yaml
# .byor/rules/project/no-type-suppression.yml
# NOTE: you may need to adapt the comment regex depending on the type checker
id: no-type-suppression
language: Python
severity: error
message: Don't silence the type checker. Fix the type.
rule:
  any:
    - pattern: cast($TYPE, $VALUE)
    - kind: comment
      regex: '#\s*type:\s*ignore'
metadata:
  byor:
    agent_prompt: >
      Silencing a type checker is a last resort, and only acceptable when 
      the type system genuinely cannot express a shape. Prioritize fixing 
      the issue at the signature if you can do so without `Any` or `object`.
      Be open to resolving the issue by modifying other methods or redesigning
      the interface in a way that is still consistent with the implementation.
      If this is a rare case where you need a cast, ask for approval to do so.
      Upon approval, silence both the type checker and ast-grep, leaving a comment
      to explain why this particular case requires the exception.
```

The post-edit hook feeds that back the moment the agent writes the suppression,
so it fixes the type instead of hiding it.

### Install Agentic Hooks

```bash
byor install --agents claude-code,codex
byor hook install --agent copilot     # add one later
```


| Harness | Post-edit hook | Pre-command gate |
| --- | --- | --- |
| Claude Code | `PostToolUse` | `PreToolUse` |
| Codex | `PostToolUse` | `PreToolUse` |
| Copilot CLI | `postToolUse` | `preToolUse` |
| OpenCode | plugin | not yet |
| Pi | extension | not yet |


Cursor and Antigravity expose no reliable post-edit hook, so byor omits them.
Details in [docs/ai-agents.md](docs/ai-agents.md).

## Commands

### Setup

```text
byor install   register byor's editor + agent integrations (machine-wide)
byor init      set up byor in a repository (docs/sync-model.md)
byor hook      add or remove one agent integration
byor doctor    check that everything is wired up
byor profile   list or apply exclusion profiles
byor package   list or install opt-in rule bundles
```

### Rule Management

These are mostly run by your agent as it captures feedback ([docs/rules.md](docs/rules.md)):

```text
byor add       create a rule (--command for a command rule)
byor list      show rules and where they resolve from
byor edit      open a rule in $EDITOR
byor remove    delete a rule
byor promote   move a personal or package rule into shared config
byor exclude   turn off a global rule in this repo
byor include   turn a previously excluded rule back on
```

byor runs the rest itself: `byor agent-check` (the post-edit hook), `byor
command-check` (the pre-command gate), and `byor sync` (mirror global rules into
a repo). Every command takes `--help`; repo commands take `--repo PATH`.

## Documentation

- [docs/rules.md](docs/rules.md) — rule format, scopes, packages, profiles
- [docs/ai-agents.md](docs/ai-agents.md) — agent integration, hooks, the gate
- [docs/sync-model.md](docs/sync-model.md) — copies, self-healing, git hooks
- [examples/](examples/) — reference rules and configs
