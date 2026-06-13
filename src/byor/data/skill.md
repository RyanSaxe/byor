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
when the rule fires. Write both for an AI reader.

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

## Pick the right tool

ast-grep matches **syntax**. It is the right tool when the policy is about the
shape of the code: a forbidden call, a banned import, a required construct. For
feedback that is really about something else, prefer the tool built for it:

- **Formatting** (line length, quote style, import order, trailing commas) — a
  formatter or its linter (`ruff`, `prettier`, `gofmt`). Do not encode these as
  ast-grep rules; they are noisy and the formatter already owns them.
- **Types** (a value used as the wrong type, a missing return type) — a type
  checker (`ty`, `mypy`, `tsc`).
- **Known lint classes** (unused variables, shadowing, mutable defaults) — the
  language's linter, which already ships a tested rule for them.

When the user's feedback fits one of these better than ast-grep, say so, and
offer to set it up: configure the tool, then wire it in so it runs everywhere
the user wants — as a byor `check` (so it rides the same agent feedback
channel), in CI, and/or as a pre-commit hook. That setup is a normal
engineering task you perform directly; it does not go through the byor rule
commands. Keep ast-grep for the syntax patterns it is uniquely good at.

## When to Decline

If the preference is not expressible as an ast-grep pattern and no linter,
type checker, or formatter fits either — naming philosophy, architectural
taste — say so and suggest recording it in the harness's instruction file
instead (CLAUDE.md, AGENTS.md, or .github/copilot-instructions.md). Do not
force a bad pattern.

## Worked Example

User says: "never use print for logging in this repo".

Draft:

```yaml
id: no-print-logging
language: Python
severity: warning
message: Use the logging module instead of print for logging.
rule:
  pattern: print($$$ARGS)
metadata:
  byor:
    rationale: >
      print bypasses log levels, handlers, and structured output; this repo
      logs through the logging module.
    agent_prompt: >
      Replace this print with a logger call at the appropriate level, using
      logging.getLogger(__name__). Keep print only for CLI user-facing
      output, with a brief comment saying so.
    tags:
      - python
      - logging
```

Confirm: "This sounds like team policy for this repo, so I drafted the rule
above at project scope, with no exceptions allowed — create it?" On yes:

```bash
byor add --scope project --from /tmp/no-print-logging.yml
```

Verify: write `print("debug")` to `/tmp/byor-rule-check.py`, run
`ast-grep scan /tmp/byor-rule-check.py` from the repo root, confirm the
`no-print-logging` diagnostic appears, then delete the file.

## ast-grep Pattern Primer

- A pattern is real code with holes. `$X` matches exactly one node:
  `cast($TYPE, $VALUE)` matches any two-argument call to `cast`.
- `$$$ARGS` matches zero or more nodes: `print($$$ARGS)` matches every call
  to `print`, whatever its arguments.
- Metavariables are `$UPPERCASE`; repeating a name requires the occurrences
  to match identical code.
- Patterns match syntax, not semantics: `print($$$ARGS)` cannot tell logging
  from CLI output. Prefer a slightly broad pattern with a precise
  `agent_prompt` over a clever rule that misses cases.
- Test a pattern before committing to the full rule:

```bash
ast-grep run -p 'print($$$ARGS)' --lang python .
```
