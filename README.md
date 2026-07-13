<p align="center">
  <img src="https://raw.githubusercontent.com/RyanSaxe/byor/main/assets/byor_banner.png" alt="byor: Build Your Own Rules" width="100%">
</p>

<p align="center">
  <a href="https://github.com/RyanSaxe/byor/actions/workflows/ci.yml"><img src="https://github.com/RyanSaxe/byor/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/byor/"><img src="https://img.shields.io/pypi/v/byor" alt="PyPI"></a>
  <a href="https://pypi.org/project/byor/"><img src="https://img.shields.io/pypi/pyversions/byor" alt="Python versions"></a>
  <a href="LICENSE"><img src="https://img.shields.io/pypi/l/byor" alt="License"></a>
</p>

Linters catch the mistakes everyone agrees on. `byor` catches the rules that are
*yours* — your conventions, your taste, the library you standardized on — and
enforces them in your editor, your terminal, CI, and your AI agent's loop.

This function does nothing but forward a call:

```python
def get_user(user_id: int) -> User:
    return fetch_user(user_id)
```

No linter flags it: whether pass-through wrappers are worth banning is a matter
of taste. `byor` flags it, because you decided your codebase doesn't do this. A
rule is an [ast-grep](https://ast-grep.github.io) pattern with an instruction for
your agent:

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

You rarely write these by hand. Tell your agent the rule in plain language and it
writes the check.

## Install

```bash
uv tool install byor && byor install   # CLI + skill + agent hooks (once)
byor init                              # optional: repo-scoped or team rules
```

Bundles ast-grep; needs Python 3.11+. Rules work in any language ast-grep
supports. Then open your agent in the repo and say **"set up byor"**.

## Where rules run

One rule, enforced everywhere you touch code:

- **Editor** — diagnostics as you type ([`ast-grep lsp`](https://ast-grep.github.io/guide/tools/editors.html)).
- **Terminal** — `ast-grep scan`.
- **CI** — committed rules run with plain `ast-grep`; `byor init --gate` writes the workflow.
- **AI agents** — a post-edit hook corrects each edit as it lands; a pre-command gate corrects shell commands before they run.

> `byor` is the sheepdog for your flock of coding agents: you set the rules, and
> it reins in any that stray, while the work is still happening.

The agent loop is the point. A style guide in `AGENTS.md` is a suggestion; a
`byor` rule is enforced, one edit at a time, in context — so it gets applied
instead of triaged at review.

## What a rule can be

| Kind | Catches | Example |
| --- | --- | --- |
| **Pattern** | a banned call, import, or construct | [no-requests](examples/rules/no-requests.yml) |
| **Shape** | structure with no string to grep (the wrapper above) | [no-routing-functions](examples/rules/no-routing-functions.yml) |
| **Check** | anything a linter, type checker, or script decides | [dependency-gate](examples/config/scripts/dependency-gate.sh) |
| **Command** | a shell command, before it runs | [no-pip-install](examples/command-rules/no-pip-install.yml) |

Every example is exercised in CI. More in [examples/](examples/) and [docs/rules.md](docs/rules.md).

## Scopes

A rule can be yours alone or committed for the whole team. That is what makes
`byor` a style guide and not a personal habit: shared rules are version-controlled,
reviewed, and enforced identically for everyone, human or agent.

| Scope | Lives in | Shared with |
| --- | --- | --- |
| `project` | `.byor/rules/project/` | Your team (committed) |
| `local` | `.byor/rules/personal/local/` | You, this repo |
| `global` | `~/.config/byor/rules/` | You, every repo |

Project and local rules override a global rule with the same ID. Command rules
follow the same scopes under `.byor/commands/`. Packages and profiles tune which
rules apply where — see [docs/rules.md](docs/rules.md).

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

Cursor and Antigravity expose no reliable post-edit hook, so `byor` omits them.
Details in [docs/ai-agents.md](docs/ai-agents.md).

## Commands

```text
Setup      install · init · hook · doctor · profile · package
Rules      add · list · edit · remove · promote · exclude · include
Automatic  agent-check · command-check · sync
```

`--help` on any command; `--repo PATH` on repo commands.

## Documentation

- [docs/rules.md](docs/rules.md) — rule format, scopes, packages, profiles
- [docs/ai-agents.md](docs/ai-agents.md) — agent integration, hooks, the gate
- [docs/sync-model.md](docs/sync-model.md) — copies, self-healing, git hooks
- [examples/](examples/) — reference rules and configs
