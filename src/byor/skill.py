"""The byor rule-capture skill: canonical SKILL.md content.

One skill, authored here, rendered identically into the two locations every
major harness discovers natively. The frontmatter must start at byte 0 and
satisfy the cross-agent standard: name of 1-64 lowercase alphanumeric chars
with single hyphens, description of at most 1024 chars.
"""

from __future__ import annotations

from byor.fsio import MANAGED_MARKER
from byor.rules import ALLOW_EXCEPTIONS_SENTENCE

SKILL_RELPATHS = (
    ".agents/skills/byor/SKILL.md",
    ".claude/skills/byor/SKILL.md",
)

SKILL_NAME = "byor"

SKILL_DESCRIPTION = (
    "Capture durable, mechanically checkable code feedback as ast-grep rules. "
    "Trigger when the user states a lasting preference about code syntax or "
    'structure — "never use X", "always do Y", "stop doing Z" — that a syntax '
    "pattern can check. Do not trigger on one-off requests about the current "
    "change or vague philosophy. If the preference cannot be expressed as a "
    "syntax pattern, follow the skill's decline guidance."
)

SKILL_BODY = f"""\
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

> {ALLOW_EXCEPTIONS_SENTENCE}

### 4. Create, then verify

Write the drafted YAML to a temp file and run:

```bash
byor add --scope SCOPE --from FILE
```

This validates the rule, syncs it into place, and runs doctor. Then prove the
rule catches the violation: write a minimal offending snippet to a scratch
file inside the repository (`ast-grep scan` only applies the project's rules
to files under the repo root — a snippet in /tmp matches nothing), run the
scan from the repo root, confirm the rule id appears, and delete the snippet:

```bash
ast-grep scan scratch_violation.py
```

## When to Decline

If the preference is not expressible as an ast-grep pattern — naming
philosophy, architectural taste, anything a formatter already owns — say so
and suggest recording it in the harness's instruction file instead
(CLAUDE.md, AGENTS.md, or .github/copilot-instructions.md). Do not force a
bad pattern.

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

Verify: write `print("debug")` to `scratch_violation.py` at the repo root,
run `ast-grep scan scratch_violation.py` from the repo root, confirm the
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
"""


# Frontmatter at byte 0, then the managed marker, then the body.
SKILL_MARKDOWN = (
    "---\n"
    f"name: {SKILL_NAME}\n"
    f"description: {SKILL_DESCRIPTION}\n"
    "---\n"
    f"{MANAGED_MARKER}\n"
    "\n"
    f"{SKILL_BODY}"
)
