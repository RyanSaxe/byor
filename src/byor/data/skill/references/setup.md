# Set Up byor and Import Existing Preferences

Onboarding confirms the install, optionally initializes the repo, and turns the
preferences the user already wrote in prose into enforced rules. Draft any rule
with the capture workflow in `SKILL.md`.

## 1. Confirm the install is healthy

Run `byor doctor` and read its report.

- If `byor` is not on PATH, byor itself is not installed yet. Tell the user the
  one bootstrap step they must run in a terminal — a skill cannot do it, because
  this skill is delivered *by* it:

  ```bash
  uv tool install byor && byor install
  ```

- If doctor reports a harness's hook missing, or the user wants byor in more
  harnesses, run `byor install --agents <names>` to add or repair them. Re-running
  `byor install` is safe and idempotent.

## 2. Initialize the repo (only if needed)

`byor init` is **optional**. It is only needed for rules scoped to *this* repo or
shared with a team — global/personal rules work everywhere without it. Ask once
whether the user wants repo-scoped or team-shared rules; if not, skip init.

```bash
byor init
```

## 3. Discover existing preferences (opt-in)

Ask before reading anything: "Want me to scan your instruction files for
preferences worth enforcing as rules?" Only on yes, read the canonical
locations that exist:

- Global: `~/.claude/CLAUDE.md` (and any files it imports with `@`),
  `~/.codex/AGENTS.md`, `~/.agents/AGENTS.md`
- Repo: `./CLAUDE.md`, `./AGENTS.md`, `.github/copilot-instructions.md`, and
  nested variants in subdirectories

Reading files outside the repo may prompt for permission in some harnesses —
that is expected.

## 4. Extract and classify candidates

Pull out the durable, mechanically checkable preferences. Classify each:

- **ast-grep rule** — a syntax shape (forbidden call, banned import)
- **formatter / linter / type checker** — see `references/checks.md`
- **byor check script** — bespoke logic over the file; see `references/checks.md`
- **not checkable** — naming taste, architecture: leave it in prose, do not force
  a rule

## 5. Confirm the batch in one question

Setup can surface many candidates, so confirm the whole batch with a single
question rather than one question per rule. Present a single table —

| preference | proposed tool | proposed scope |

— and ask which to convert. Wait for the answer before creating anything.

## 6. Create and verify each approved candidate

For each one the user approved, follow the capture workflow in `SKILL.md`: draft
the rule, then `byor add --scope SCOPE --from FILE` (or set up the tool/check),
then trip it once to prove it fires.

## 7. Offer a one-time cleanup pass (repo only, optional)

Adopting byor on an existing repo can surface a wall of violations. Offer a
single isolated cleanup so the user starts clean instead of fighting the tool:

1. Create a dedicated branch, e.g. `byor/initial-cleanup`.
2. Run every effective rule and check across the whole repo from the root, and
   capture the combined output to a tmp file so it is referenceable and does not
   flood context:

   ```bash
   ast-grep scan > /tmp/byor-cleanup-<repo>.txt 2>&1
   ```

   Append each configured byor check's output (run its `run` command over the
   repo's files) to the same file. `byor list` shows the effective checks.
3. Work through that file, fixing each violation per its rule's `agent_prompt`,
   committing in small atomic chunks. Leave the branch for the user to review and
   merge.

## 8. Offer to tidy the prose (optional, never automatic)

byor writes no instruction files, so leave CLAUDE.md / AGENTS.md untouched by
default. After rules exist, you may offer: "These are now enforced
mechanically — want me to remove or annotate the matching lines in your
instruction files?" Make each edit only on explicit confirmation.
