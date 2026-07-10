---
name: byor
description: Capture durable, mechanically checkable feedback as enforced rules and checks — ast-grep patterns, linters, type checkers, custom scripts, and command rules that gate shell commands — and set up or onboard byor. Trigger to capture when the user states a lasting preference about code ("never use X", "always do Y", "stop doing Z") or about commands ("never run X", "use uv, not pip") that a mechanical check can verify. Trigger to set up when the user wants to get started with byor, install it, or import existing preferences from CLAUDE.md / AGENTS.md. Do not trigger on one-off requests about the current change or vague philosophy.
---
# BYOR Rule Capture

This repository uses BYOR to enforce custom diagnostics mechanically. When the
user gives feedback that *any* mechanical check can verify, capture it — as a
rule or a check — so every future session enforces it automatically; do not
just remember it.

For getting started, repairing the install, or importing the user's existing
preferences from their instruction files, follow **references/setup.md**.

## When to Act

Act when the user expresses a durable preference about code — or about the
commands you run — that some mechanical check can verify:

- "never use X" / "always do Y" / "stop doing Z"
- Policy phrasing about lasting behavior ("in this repo we don't ...")
- Command corrections ("never run pip install", "use rg, not grep",
  "stop force-pushing")

Do not act on:

- One-off requests about the current change ("remove this print")
- Vague philosophy ("keep it simple", "make this more readable")
- Preferences no mechanical check can detect (naming taste, architecture) —
  decline these as described in "When to Decline" below

## Choose the mechanism

byor enforces more than ast-grep. Match the preference to the check that fits:

- **Code shape** — a forbidden call, banned import, required construct: an
  ast-grep rule, drafted below.
- **A shell command** — a banned or redirected command ("use uv, not pip"):
  a command rule, the same YAML with `language: Bash`, denied *before* the
  command runs with your correction. See **references/commands.md**.
- **Formatting, types, or a known lint class** — a formatter, type checker, or
  linter that already owns it. See **references/checks.md**.
- **Bespoke logic over the file** — a small custom check script. See
  **references/checks.md**.

The last two wire in as byor `checks`, so they ride the same agent feedback
channel as rules. Only decline when nothing mechanical fits.

## Capture an ast-grep rule

### 1. Draft the rule

First inspect the existing tag vocabulary:

```bash
byor list --scope all --tags
```

Tags are user-defined labels for listing, profiles, and repo-local exclusions.
Reuse existing tags when they fit the new rule. Create a new tag only when it
expresses a genuinely new grouping the existing vocabulary does not cover. Tag
by the groupings a user would toggle together, so one selector can exclude the
whole group later — see **references/profiles.md**.

Write a complete ast-grep YAML rule with id, language, severity, message,
`rule.pattern`, and `metadata.byor`:

```yaml
id: kebab-case-id
language: Python
severity: warning
message: One-line statement of the policy.
rule:
  pattern: forbidden_call($$$ARGS)
metadata:
  byor:
    rationale: >
      Why this policy exists, in one or two sentences.
    agent_prompt: >
      Imperative instruction for the future AI that hits this diagnostic:
      what to write instead, and when an exception is acceptable.
    tags:
      - python
```

`rationale` records why; `agent_prompt` tells the next AI exactly what to do
when the rule fires. Write both for an AI reader. Tags help future agents and
users apply profiles or repo-local exclusions without naming every rule one by
one. For ast-grep pattern syntax and a fully worked example, see
**references/patterns.md**.

### 2. Propose a scope

- `project` — team policy voiced about this codebase (committed, shared)
- `global` — a personal preference that transcends this repository
- `local` — an experiment, private to this checkout

If the preference is really a *reusable bundle* other repos should opt into one
at a time — not a single rule for this repo, and not something to force on every
repo — that is a **package**, authored differently. See
**references/packages.md**.

Show the user the drafted rule and your recommended scope.

### 3. Confirm with exactly one question

Ask one question covering the rule, the scope, and whether exceptions are
acceptable, then stop and wait. Never create a rule from an offhand remark
without confirmation. When the user allows exceptions, end the drafted
agent_prompt with the standard sentence:

> {{ALLOW_EXCEPTIONS_SENTENCE}}

### 4. Create, then verify

Write the drafted YAML to a temp file and run:

```bash
byor add --scope SCOPE --from FILE
```

This validates the rule, syncs it into place, and runs doctor. Then prove the
rule catches the violation: write a minimal offending snippet to a clearly
named scratch file in /tmp, scan it **from the repo root** so ast-grep applies
this project's config, confirm the rule id appears, and delete it:

```bash
ast-grep scan /tmp/byor-rule-check.py
```

## Capture a command rule

A command rule uses the same YAML shape with `language: Bash` and a pattern
over the command line; the pre-command gate denies matching commands and hands
back the `agent_prompt`, so it must always name the replacement command.
Create it with the `--command` flag, then prove it fires and that innocent
commands pass:

```bash
byor add --scope SCOPE --command --from FILE
byor command-check --command 'pip install requests'   # expect the rule id, exit 2
byor command-check --command 'uv add requests'        # expect silence, exit 0
```

Pattern guidance, the command-check script escape hatch, and what command
rules cannot do are in **references/commands.md**.

## When to Decline

Decline only when no mechanical check of any kind fits — not an ast-grep
pattern, not a linter, type checker, or formatter, and not a custom check
script. For genuine taste (naming, architecture), say so and suggest recording
it in the harness's instruction file instead (CLAUDE.md, AGENTS.md, or
.github/copilot-instructions.md). Do not force a bad rule.

## References

- **references/patterns.md** — ast-grep pattern primer and a worked example.
- **references/commands.md** — command rules and command checks: gate shell commands.
- **references/checks.md** — pick the right tool, or author a check script.
- **references/packages.md** — author a reusable, opt-in bundle of rules/checks.
- **references/profiles.md** — tune which rules apply: exclusions and profiles.
- **references/setup.md** — set up byor and import existing preferences.
