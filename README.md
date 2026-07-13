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

byor lets you write the checks a general linter leaves out: the rules specific
to your team, your codebase, and your taste. It runs them in your editor, in CI,
and inside your AI agent's loop as it writes.

This function does nothing but forward a call:

```python
def get_user(user_id: int) -> User:
    return fetch_user(user_id)
```

No linter flags it. Whether a pass-through wrapper is worth banning is a matter
of taste, and a linter ships only the rules everyone already agrees on. byor
flags it, because you decided your codebase shouldn't have them. A rule is an
ast-grep pattern plus an instruction for your agent:

```yaml
# .byor/rules/project/no-requests.yml
id: no-requests
language: Python
severity: error
message: This codebase uses httpx, not requests.
rule:
  any:
    - pattern: import requests
    - pattern: from requests import $$$NAMES
metadata:
  byor:
    agent_prompt: Use httpx instead. Do not add requests to the dependencies.
```

byor exists because agents don't follow prose. Every harness asks you to put
your standards in an `AGENTS.md`, a skill, a Markdown style guide. The agent
reads it, agrees, and drifts anyway. A byor rule is not a suggestion: the agent
has to satisfy it before it moves on, in the loop where it wrote the code.

> byor is the sheepdog for your flock of coding agents: you set the rules, and
> it reins in any that stray, while the work is still happening.

You rarely write these by hand. Tell your agent the rule in plain language and
it writes the check.

## Install

```bash
uv tool install byor && byor install   # CLI + skill + agent hooks (once)
byor init                              # optional: repo-scoped or team rules
```

byor bundles ast-grep and needs Python 3.11+. Rules work in any language
ast-grep supports. Then open your agent in the repo and say **"set up byor"**.

## Where rules run

A rule you write once runs in four places:

- **Editor:** a diagnostic while you type ([`ast-grep lsp`](https://ast-grep.github.io/guide/tools/editors.html)).
- **Terminal:** `ast-grep scan`.
- **CI:** committed rules run with plain `ast-grep`. `byor init --gate` writes the workflow.
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

```text
Setup      install · init · hook · doctor · profile · package
Rules      add · list · edit · remove · promote · exclude · include
Automatic  agent-check · command-check · sync
```

Every command takes `--help`; repo commands take `--repo PATH`.

## Documentation

- [docs/rules.md](docs/rules.md) — rule format, scopes, packages, profiles
- [docs/ai-agents.md](docs/ai-agents.md) — agent integration, hooks, the gate
- [docs/sync-model.md](docs/sync-model.md) — copies, self-healing, git hooks
- [examples/](examples/) — reference rules and configs
