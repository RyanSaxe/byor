<p align="center">
  <img src="https://raw.githubusercontent.com/RyanSaxe/byor/main/assets/byor_banner.png" alt="byor: Build Your Own Rules" width="100%">
</p>

<p align="center">
  <a href="https://github.com/RyanSaxe/byor/actions/workflows/ci.yml"><img src="https://github.com/RyanSaxe/byor/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/byor/"><img src="https://img.shields.io/pypi/v/byor" alt="PyPI"></a>
  <a href="https://pypi.org/project/byor/"><img src="https://img.shields.io/pypi/pyversions/byor" alt="Python versions"></a>
  <a href="LICENSE"><img src="https://img.shields.io/pypi/l/byor" alt="License"></a>
</p>

Every codebase has rules that no linter will enforce for you. Here is one. This
function does nothing:

```python
def get_user(user_id: int) -> User:
    return fetch_user(user_id)
```

It takes an argument, hands it to another function, and returns the result. You
might think a wrapper like this is noise that hides a rename behind a layer of
indirection. You might think it is a reasonable seam to hang future logic on.
Both positions are defensible, and that is exactly the problem: your linter will
not take a side. Ruff, ESLint, clippy, and every linter like them ship the rules
a broad community has already settled. An unused import is dead. A bare `except`
swallows bugs. Nobody argues about those. The rules that are actually worth
arguing about, the ones that encode how *you* think code should read, are the
ones a linter deliberately leaves alone.

A linter *could* enforce a rule against forwarding wrappers. Nothing stops it
technically. But a linter is a shared default, and a shared default can only
contain what a large group agrees to live with. Taste does not survive that
averaging. Your team's conventions, your own preferences, the one HTTP library
you standardized on last quarter: these are real rules, and today you enforce
them with code review, tribal memory, and hope.

`byor` is for those rules.

## What it is

`byor` lets you write the rules a linter won't, and then enforces them
mechanically everywhere you work with code. The same rule you write once shows
up as a diagnostic in your editor, a finding in your terminal, a failing check
in CI, and, the part that matters most now, a correction inside your AI agent's
loop while it is still writing.

A rule is an [ast-grep](https://ast-grep.github.io) pattern, a linter or type
checker you already run, or a small script. You rarely write one by hand. You
tell your agent the rule in plain language and it uses `byor`'s skill to turn
your sentence into a real, executable check that every future session enforces.
The wrapper above is a five-line rule; the next section shows it.

Because these rules are a style guide and not a personal habit, `byor` treats
sharing as a first-class concern. A rule can be yours alone, or committed to a
repository so the whole team inherits it, and the git integration is what keeps
a team honest to the same conventions instead of relitigating them in review.

## Why now

You could always have written custom checks. Almost nobody did, because the
enforcement was never worth the effort: a diagnostic you have to remember to
look at is not much better than a convention you have to remember to follow.

AI agents change that calculation. An agent writes code faster than you can
review it, and it does not share your taste. You tell it to stop wrapping calls
in pass-through functions; it agrees, and twenty minutes later it writes
`get_user` again. A style guide in `AGENTS.md` does not hold, because prose is a
suggestion and the agent is optimizing for a working diff, not your preferences.

> `byor` is the sheepdog for your flock of coding agents. You set the rules; it
> reins in any one that strays, while the work is still happening.

It herds in both directions:

- A **post-edit hook** checks each file the moment the agent writes it, and
  hands back the specific instruction for any rule the edit broke, scoped to the
  lines that changed. The agent fixes the violation before it moves on.
- A **pre-command gate** checks each shell command *before* it runs. Where a
  permission prompt can only say no, `byor` says no and tells the agent what to
  run instead.

Where the feedback lands changes what happens to it. The same rule delivered as
a report at the end of a large change invites the agent to fix the easy half and
declare victory. Delivered one edit at a time, in context, it just gets applied.
A banned library caught at the first `import` is a one-line correction; caught
at review, with a day of work built on top of it, it is a rewrite.

## Four rules

Linters keep absorbing the checks that everyone comes to agree on. The rules
worth writing yourself are the ones that name your choices, and `byor` gives
them the same enforcement a linter has. They come in four sizes, from a
one-line pattern to a script. Every one below is real and exercised in CI; the
annotated versions live in [examples/](examples/).

**A pattern.** Say this codebase uses httpx. No general linter can know that.
A short ast-grep rule enforces it with no holes, because imports are a choke
point: every use of a library begins with one. The `agent_prompt` tells the
agent what to do instead of leaving it to guess.

```yaml
# .byor/rules/project/no-requests.yml
id: no-requests
language: Python
severity: error
message: This codebase uses httpx, not requests.
rule:
  any:
    - kind: dotted_name
      regex: ^requests(\.|$)
      inside: { stopBy: end, kind: import_statement }
    - pattern: from requests import $$$NAMES
    - pattern: from requests.$SUB import $$$NAMES
metadata:
  byor:
    agent_prompt: >
      Use httpx instead. For simple calls the API is the same
      (httpx.get, httpx.post); for anything repeated, use an httpx.Client
      or httpx.AsyncClient with an explicit timeout. Do not add requests
      to the dependencies.
```

The first clause matches the module name inside any plain `import`, so the
aliased, submodule, and comma-combined (`import os, requests`) forms are all
covered; the two patterns handle from-imports.

**A shape.** There is no string to grep for in the wrapper at the top of this
page. What makes it a violation is structure: a function whose entire body is a
single call to something else, in any form (`return`, `await`, a bare call,
`yield from`, with or without a docstring). ast-grep matches structure, so a
rule can say exactly that:

```yaml
# .byor/rules/project/no-routing-functions.yml
id: no-routing-functions
language: Python
severity: warning
message: Do not create functions whose only behavior is routing to another call.
rule:
  all:
    - any:
        - pattern: return $CALLEE($$$ARGS)
        - pattern: return await $CALLEE($$$ARGS)
        - pattern:
            context: $CALLEE($$$ARGS)
            selector: expression_statement
        - pattern:
            context: await $CALLEE($$$ARGS)
            selector: expression_statement
        - pattern:
            context: yield from $CALLEE($$$ARGS)
            selector: expression_statement
    - any:
        - all:
            - nthChild: 1
            - nthChild:
                position: 1
                reverse: true
        - all:
            - nthChild: 2
            - nthChild:
                position: 1
                reverse: true
            - follows:
                kind: expression_statement
                has:
                  kind: string
    - inside:
        kind: block
        inside:
          kind: function_definition
          field: body
metadata:
  byor:
    agent_prompt: >
      Remove this routing function and call the underlying implementation
      directly. If the function must exist as a public API or integration
      boundary, add real boundary behavior such as validation, translation,
      authorization, retry policy, error handling, or instrumentation. Do not
      preserve a wrapper whose only effect is changing argument order, defaults,
      or names.
```

This is the kind of rule the argument at the top was about. Reasonable people
disagree about pass-through wrappers, so no linter ships this. If *you* have
decided your codebase does not have them, `byor` holds that line.

**A script.** Some rules are not about the text of the code at all. This check
fails whenever the dependency list differs from the last commit, so an agent has
to stop and ask before adding a package:

```yaml
# .byor/config.yml
checks:
  - name: dependency-gate
    extensions: [toml]
    run: .byor/scripts/dependency-gate.sh
    gate: false
```

```sh
#!/bin/sh
# The `dependencies = [...]` block, from its opening line to the first `]`.
deps() { awk '/^dependencies = \[/ { open = 1 } open { print } open && /\]/ { exit }'; }

[ -f pyproject.toml ] || exit 0
git rev-parse --verify --quiet HEAD >/dev/null 2>&1 || exit 0 # no commits yet: nothing to compare

committed=$(git show HEAD:pyproject.toml 2>/dev/null | deps)
current=$(deps <pyproject.toml)
[ "$committed" = "$current" ] && exit 0

echo "The dependency list in pyproject.toml differs from the last commit."
echo "If you added or removed a package without being asked to, revert it and ask the user first."
exit 1
```

`gate: false` marks a check that polices the agent rather than the code. The
post-edit hook runs it, but the pre-commit and CI gates `byor` can generate
leave it out, where it would only get in the way of a person adding a dependency
on purpose. A rule like this only makes sense inside the loop.

**A command.** The last kind of rule is about what the agent *runs*, not what it
writes, and it can only be enforced before the command executes. Your harness
can deny a command, but a denial teaches nothing. A command rule denies it with
the correction attached:

```yaml
# .byor/commands/project/no-pip-install.yml
id: no-pip-install
language: Bash
severity: error
message: This machine manages Python dependencies with uv, not pip.
rule:
  any:
    - pattern: pip install $$$ARGS
    - pattern: pip3 install $$$ARGS
    - pattern: python -m pip install $$$ARGS
metadata:
  byor:
    agent_prompt: >
      Use uv instead: `uv add <package>` to add a dependency, `uv sync`
      to install what the lockfile already says. Never invoke pip directly.
```

The command line is parsed as Bash, so the pattern matches `pip install` buried
inside `cd docs && pip install x | tee log` but not quoted prose like
`echo "pip install x"`. The agent reads the `agent_prompt`, runs `uv add`, and
moves on. This is steering, not a sandbox: it corrects an agent typing a command
plainly and makes no claim to stop a determined evasion.

The first two rules are ordinary ast-grep, and they follow you everywhere you
read code:

- **Editor** — point your editor's ast-grep integration at `ast-grep lsp` and
  the `message` shows up as a diagnostic while you type.
- **AI agent** — the post-edit hook hands over the `agent_prompt`, scoped to the
  lines the agent changed.
- **Terminal** — `ast-grep scan` prints the `message`.

ast-grep rules are `byor`'s built-in kind. It also runs any linter, type
checker, or script you already use and folds their output into the same feedback
the rules produce.

## Install

```bash
uv tool install byor && byor install   # install the CLI, then set up the skill + agent hooks (once)
byor init                              # optional — only for repo-scoped or shared rules (see below)
```

`byor` bundles ast-grep, so Python 3.11+ is all you need to *run* it. The rules
themselves work in any language ast-grep supports (TypeScript, Go, Rust, and
more), not just Python. `byor install` registers your editor and agent
integrations machine-wide.

`byor init` is optional. Run it only when you want rules scoped to a repository
or shared with a team; your personal global rules already work in every repo
without it. On a repo the team has not adopted `byor` for, `byor init --private`
keeps the whole footprint out of git.

After that one-time setup, let your agent do the rest. Open it in the repo and
say **"set up byor"**. The skill verifies the install, runs `byor init` if you
want repo or team rules, and offers to import the mechanically checkable
preferences you already wrote in your `CLAUDE.md` / `AGENTS.md` as enforced
rules. [docs/ai-agents.md](docs/ai-agents.md) covers what each step writes.

## A style guide is a team artifact

A rule that only you can see is a preference. A rule the whole team inherits is a
style guide. `byor` uses the same file format for both and lets you choose the
audience per rule with a scope:

| Scope | Lives in | Shared with |
| --- | --- | --- |
| `project` | `.byor/rules/project/` | Your team (committed) |
| `local` | `.byor/rules/personal/local/` | You, this repo only |
| `global` | `~/.config/byor/rules/` | You, in every repo |

Global rules are your personal standards; `byor` makes them apply in every repo
you work in. Project rules are committed, so a fresh clone already enforces them
and every teammate's agent is held to the same conventions. Project and local
rules override a global rule with the same ID, so a team policy or a local
experiment takes precedence over your personal default.

This is why the git integration matters. A style guide that lives in one
person's head drifts the moment a second person joins. A style guide that lives
in committed, executable rules does not: it is version-controlled, it is
reviewed like any other change, and it is enforced identically for everyone,
human or agent. Command rules follow the same three scopes under
`.byor/commands/`.

Two mechanisms tune which rules apply where. **Packages** are named bundles of
rules you opt a repo into one at a time, for conventions that are reusable but
too situational to force on every repo. **Profiles** apply repo-local
exclusions from your global config, for when a repo should opt out of a broad
group of rules without deleting them everywhere. Both are covered in
[docs/rules.md](docs/rules.md).

## Terminal, editor, and CI

A rule under `.byor/rules/` is an ordinary ast-grep rule, so the ordinary tools
read it with no `byor` in the loop:

```bash
ast-grep scan            # lint the repo
ast-grep scan src/       # ...or a path
```

For live in-editor diagnostics, point your editor's ast-grep integration at
`ast-grep lsp`; rules light up as you type and reload when you edit them
([editor setup](https://ast-grep.github.io/guide/tools/editors.html)).

CI needs `byor` even less. Project rules are committed files that work with
plain `ast-grep`, so a fresh clone already has everything `ast-grep scan` reads.
Scan with `--error` so warnings fail the build:

```yaml
- uses: astral-sh/setup-uv@v6
- run: uvx --from ast-grep-cli ast-grep scan --error
```

`byor init --gate` generates this workflow and a matching
`.pre-commit-config.yaml`, promoting your effective rules and checks into
committed config first so the gate stays `byor`-free but still covers your
checks. Checks marked `gate: false` stay out of both, where they belong to the
agent loop rather than the humans at the gate.

## With AI agents

`byor install` wires up the agents you pick, once and machine-wide; `byor hook`
adds or drops one later.

```bash
byor install --agents claude-code,codex
byor hook install --agent copilot       # add an agent later
byor hook uninstall --agent copilot     # or remove one (--agent skill removes the skill)
```

| Harness | Skill | Post-edit hook | Pre-command gate | Diagnostic precision |
| --- | --- | --- | --- | --- |
| Claude Code | yes | `PostToolUse` | `PreToolUse` | the edited lines |
| Codex | yes | `PostToolUse` | `PreToolUse` | the edited lines |
| Copilot CLI | yes | `postToolUse` | `preToolUse` | the edited lines |
| OpenCode | yes | `tool.execute.after` plugin | not yet | the changed file |
| Pi | yes | `tool_result` extension | not yet | the changed file |

Cursor and Antigravity are not supported: neither exposes a post-edit hook that
`byor` can reliably integrate with, so `byor` omits them until that changes.
See [docs/ai-agents.md](docs/ai-agents.md) for what each integration installs.

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
byor add            Create a rule in a scope (--command for a command rule)
byor list           Show rules and where they come from
byor edit           Open a rule in $EDITOR
byor remove         Delete a rule
byor promote        Move a personal/package rule or a check into shared config
byor exclude        Disable a global rule in this repository
byor include        Re-enable an excluded global rule
```

**Automatic.** `byor` runs these itself: the hooks and self-heal.

```text
byor agent-check    Render diagnostics for your agent (post-edit hook)
byor command-check  Gate a shell command before it runs (pre-command hook)
byor sync           Mirror global rules into the repo
```

Every command takes `--help`, and repo-operating commands take `--repo PATH`
(default: search upward from the current directory).

## What's next

`byor`'s inner loop has two deterministic mechanisms today: the post-edit hook
for what agents write, and the pre-command gate for what they run. Together they
cover anything a rule, a linter, a type checker, a script, or a command pattern
can express.

The strays that remain are behavioral, not textual: an agent drifting off the
plan you agreed on, stopping a loop early, editing files outside the scope you
set. Teaching `byor` to catch those too is where it is headed. If there is a
rule you wish it could enforce,
[open an issue](https://github.com/RyanSaxe/byor/issues).

## Documentation

- [docs/rules.md](docs/rules.md) — rule format, scopes, packages, and profiles
- [docs/ai-agents.md](docs/ai-agents.md) — AI agent integration, hooks, and the gate
- [docs/sync-model.md](docs/sync-model.md) — copies, self-healing, and git hooks
- [examples/](examples/) — reference rules (simple to advanced) and config setups
