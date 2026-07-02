# ast-grep Patterns and a Worked Example

The syntax a rule's `rule.pattern` uses, plus a complete worked example.
Before drafting tags for a new rule, run `byor list --scope all --tags` and
reuse fitting user-defined labels where possible.

## Pattern Primer

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

## Scope a rule to part of the repo

Rules accept ast-grep's `files:` and `ignores:` glob lists at the top level,
which is the right tool when a rule is correct for most of the repo but wrong
for one directory — a CLI's `bin/` legitimately printing while `src/` must
not, or generated code exempt from style rules:

```yaml
id: no-console-log-in-src
language: JavaScript
files:
  - src/**
rule:
  pattern: console.log($$$ARGS)
```

Prefer this over `byor exclude`, which disables a rule for the whole repo.
When an installed package's rule needs path scoping, exclude it and capture a
path-scoped project rule in its place — say so in the rule's rationale.
