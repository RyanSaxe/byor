# Tune Which Rules Apply: Exclusions and Profiles

Global rules and checks apply in every repo by default. A repo tunes that with
**exclusions** — and a **profile** is a named, reusable bundle of exclusions.
Use these when a user wants a repo to *not* enforce something, rather than to
capture a new rule.

## Tags make a group addressable

A rule's `metadata.byor.tags` (and a check's `tags`) are the handle exclusions
and profiles grab. Tag with the groupings a user would plausibly toggle together
— language, strictness, `legacy-risk`, a subsystem — not one tag per rule. That
is what lets a single selector turn off a whole group. Inspect the vocabulary
first and reuse it:

```bash
byor list --scope all --tags
```

## One-off exclusions

To turn a single global rule or check off in *this* repo:

```bash
byor exclude RULE_ID          # a rule by id
byor exclude --tag TAG        # every global rule carrying TAG
byor exclude --check NAME     # a check by name
byor exclude --check-tag TAG  # every check carrying TAG
byor include ...              # undo any of the above
```

These write to the untracked `.byor/local.yml`, so they are personal to the
user's checkout. `byor include` reverses them (a project or local rule that owns
the same id still wins — byor says so).

## Per-line exceptions

When a single line legitimately violates a rule whose agent_prompt allows
exceptions, add `# ast-grep-ignore: RULE_ID` on its own line directly above
that line, with a short comment above it explaining why. (ast-grep also honors
the directive at the end of the offending line, but a formatter that splits
the line relocates it and silently invalidates the hatch — the line above is
immune.) The hatch is scoped to the named rule on the one line below —
nothing broader: a rule-scoped, explained exception is a decision; a blanket
suppression (`# noqa`, `# type: ignore`) is a hidden one.

Never commit a hatch naming a personal (global or package) rule to a repo
with a committed gate: the gate's runner does not know that rule, and
ast-grep treats an unknown-rule suppression as an error
(`unused-suppression`) by design, so the hatch itself fails CI. Promote the
rule to project scope first (`byor promote`), then suppress it.

## Profiles: a reusable bundle of exclusions

A profile is a named template in the global config (`~/.config/byor/config.yml`)
that applies a set of exclusions at once. Author one when the user keeps making
the same opt-outs across repos ("mature repos skip my strict + legacy-risk
rules"):

```yaml
profiles:
  existing:
    description: Low-friction defaults for mature repositories.
    rules:
      excluded_tags: [legacy-risk]
      excluded_rule_ids: []
    checks:
      excluded_tags: [strict]
      excluded: []
```

Apply a profile to a repo — additively, never clearing existing exclusions:

```bash
byor profile list             # what is configured
byor profile add existing     # merge its exclusions into this repo
byor init --profile existing  # or at init time
```

Profiles only subtract, and only from global rules/checks; project and local
rules stay owned by the repo. To make a preference *reusable and opt-in* rather
than on-by-default-minus-exclusions, author a package instead
(`references/packages.md`).
