---
name: byor
description: Capture durable, mechanically checkable code feedback as ast-grep rules. Trigger when the user states a lasting preference about code syntax or structure — "never use X", "always do Y", "stop doing Z" — that a syntax pattern can check. Do not trigger on one-off requests about the current change or vague philosophy. If the preference cannot be expressed as a syntax pattern, follow the skill's decline guidance.
---
# BYOR Rule Capture

This repository uses BYOR to enforce custom ast-grep diagnostics. When the
user gives feedback that a syntax pattern can check, capture it as a rule so
every future session enforces it automatically — do not just remember it.

## When to Act

Act when the user expresses a durable, mechanically checkable preference about
code syntax or structure:

- "never use X" / "always do Y" / "stop doing Z"
- Policy phrasing about lasting behavior ("in this repo we don't ...")

Do not act on:

- One-off requests about the current change ("remove this print")
- Vague philosophy ("keep it simple", "make this more readable")
- Preferences no syntax pattern can detect (naming taste, architecture) —
  decline these as described in "When to Decline" below

## Workflow

### 1. Draft the rule

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
when the rule fires. Write both for an AI reader. For ast-grep pattern syntax
and a fully worked example, see **references/patterns.md**.

### 2. Propose a scope

- `project` — team policy voiced about this codebase (committed, shared)
- `global` — a personal preference that transcends this repository
- `local` — an experiment, private to this checkout

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

## When ast-grep is the wrong tool

ast-grep matches **syntax**. When the user's feedback is really about
formatting, types, or a known lint class — or needs real logic over the file —
ast-grep is not the right home for it. See **references/checks.md** to pick the
formatter, linter, or type checker that owns it, or to author a byor check
script for bespoke logic.

## When to Decline

If the preference is not expressible as an ast-grep pattern and no linter,
type checker, or formatter fits either — naming philosophy, architectural
taste — say so and suggest recording it in the harness's instruction file
instead (CLAUDE.md, AGENTS.md, or .github/copilot-instructions.md). Do not
force a bad pattern.

## References

- **references/patterns.md** — ast-grep pattern primer and a worked example.
- **references/checks.md** — pick the right tool, or author a check script.
