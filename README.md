<p align="center">
  <img src="https://raw.githubusercontent.com/RyanSaxe/byor/main/assets/byor_banner.png" alt="byor: Build Your Own Rules" width="100%">
</p>

<p align="center">
  <a href="https://github.com/RyanSaxe/byor/actions/workflows/ci.yml"><img src="https://github.com/RyanSaxe/byor/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/byor/"><img src="https://img.shields.io/pypi/v/byor" alt="PyPI"></a>
  <a href="https://pypi.org/project/byor/"><img src="https://img.shields.io/pypi/pyversions/byor" alt="Python versions"></a>
  <a href="LICENSE"><img src="https://img.shields.io/pypi/l/byor" alt="License"></a>
</p>

Your AI agent keeps breaking rules you have already given it. You tell it to
stop writing wrapper functions that do nothing but forward to another call; it
agrees, and twenty minutes later it writes:

```python
def get_user(user_id: int) -> User:
    return fetch_user(user_id)
```

So you add a line to your ever-growing `AGENTS.md` in the hope it fixes it. **It doesn't.**

> `byor` is the sheepdog for your flock of coding agents: you set the rules and it
> reins in any agent that attempts to stray in real time. 

`byor` can do this reliably because the rules it creates are real executable checks, 
not markdown prompts. You rarely write one of these rules by hand. If you tell your 
agent to create a rule, or even give it critical feedback about code it has written,
it will use `byor`'s skill to create the best automated system to keep your agent in
check.

## The inner loop

Working with a coding agent is a loop: you set a goal and the agent runs until
it gets there. Almost everything that keeps code quality honest sits outside
that loop. You review the diff at the end, CI complains after the push, or you
run a cleanup prompt once the feature works. `byor` moves enforcement inside
the loop: a post-edit hook checks each edit as the agent makes it, and the
agent fixes the violation while it still has the context.

Where the feedback lands changes what happens to it:

- **You review the change, not a cleanup.** The code already follows your
  rules when you first read it, so review time goes to what the change does.
- **One violation now gets fixed; a thousand at the end get triaged.** An
  outer pass that hands the agent a long report invites it to fix the easy
  half and stop. The same feedback delivered one edit at a time just gets
  applied.
- **Bad decisions get caught before they are load-bearing.** When the agent
  reaches for a library you banned, the correction at the first import is one
  line. The same correction at review time, with a day of work built on top
  of that library, is a rewrite.

## Three examples

Linters keep absorbing the rules general enough for everyone to agree on. The
rules worth writing yourself are the ones that name your choices, and `byor`
gives them the same enforcement a linter has. They come in three sizes.

**A pattern.** Suppose this codebase uses httpx. No general linter can know
that. A short [ast-grep](https://ast-grep.github.io) rule enforces it, and its
`agent_prompt` tells the agent what to do instead of leaving it to guess:

```yaml
# .byor/rules/project/no-requests.yml
id: no-requests
language: Python
severity: error
message: This codebase uses httpx, not requests.
rule:
  any:
    - pattern: import requests
    - pattern: import requests as $ALIAS
    - pattern: import requests.$SUB
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

**A shape.** There is no string to grep for in the wrapper at the top of this
page. What makes it a violation is structure: a function whose body is a single
call to something else, in any form (`return`, `await`, a bare call,
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

**A script.** Some rules are not about the text of the code at all. This check
fails whenever the dependency list differs from the last commit, so an agent
must stop and ask before adding a package:

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

`gate: false` marks a check that polices the agent rather than the code: the
post-edit hook runs it, but the pre-commit and CI gates `byor` generates leave
it out, where it would block a person adding a dependency on purpose. A rule
like this only makes sense inside the loop. [examples/](examples/) has a second
one, five lines of shell that reject hand-edits to `uv.lock`; it never fires on
`uv add` run in a terminal, because the hook only sees the agent's own file
edits.

Everything above is real and exercised in CI: the rules against valid and
invalid samples, the scripts in both directions. See [examples/](examples/) for
the annotated versions.

The first two are ordinary [ast-grep](https://ast-grep.github.io) rules, and
they follow you everywhere you read code:

- **IDE** — set up your IDE with `ast-grep lsp` to see `message` as a diagnostic.
- **AI agent** — a post-edit hook hands over the `agent_prompt`, scoped to
  the lines it changed, so the agent fixes the violation before moving on.
- **Terminal** — `ast-grep scan` shows the `message`.

ast-grep rules are `byor`'s built-in kind; it also runs any linter, type checker, or
script you already use and folds their output into the same agent feedback.

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
On a repo the team has not adopted byor for, `byor init --private` keeps the
whole footprint out of git (nothing tracked, ignored via `.git/info/exclude`);
see [docs/sync-model.md](docs/sync-model.md).
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

Packages are the opposite of a global rule: a named bundle of rules (and
optional checks) under `~/.config/byor/packages/` that a repo **opts into**
rather than getting everywhere automatically. `byor package add <name>` installs
one for you in a repo (personally, like a `local` rule — not committed); promote
its rules or checks with `byor promote` to share them with the team. Reach for a
package when a rule set is reusable but too situational to force on every repo.
See [docs/rules.md](docs/rules.md).

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
- uses: astral-sh/setup-uv@v6
- run: uvx --from ast-grep-cli ast-grep scan --error
```

`byor init --gate` generates this workflow and a matching `.pre-commit-config.yaml`
for you — promoting your effective rules and checks into committed config first,
so the gate stays byor-free but also covers your checks. Checks marked
`gate: false` stay out of both files: they police the agent inside the loop,
not the humans at the gate. See [docs/sync-model.md](docs/sync-model.md).

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
byor promote        Move a personal/package rule or a check into shared config
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

Today byor's inner loop has one mechanism: a deterministic post-edit hook.
That already covers anything a rule, a linter, a type checker, or a script can
express.

The harder strays are behavioral, not textual: an agent drifting off the plan you
agreed on, stopping a loop early, editing files outside the scope you set.
Teaching the sheepdog to herd those too is where byor is headed. If there is a
rule you wish it could enforce, [open an issue](https://github.com/RyanSaxe/byor/issues).

## Documentation

- [docs/rules.md](docs/rules.md) — rule format, scopes, and the rule workflow
- [docs/ai-agents.md](docs/ai-agents.md) — AI agent integration and `agent-check`
- [docs/sync-model.md](docs/sync-model.md) — copies, self-healing, and git hooks
- [examples/](examples/) — reference rules (simple → advanced) and config setups
