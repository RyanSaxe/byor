# Set Up byor and Import Existing Preferences

Onboarding confirms the install, wires up the harness you are running in, and
turns the preferences the user already wrote in prose into enforced rules and
checks. Draft any ast-grep rule with the capture workflow in `SKILL.md`, using
`references/patterns.md` for the pattern syntax.

## 1. Confirm the install and wire this harness

Run `byor doctor` as a quick sanity check. You are reading this skill, so byor
already installed it — doctor mainly confirms which harnesses are wired and
flags anything to repair. (If `byor` is somehow not on PATH, which is unexpected
since the skill was installed by it, the user can repair with
`uv tool install byor && byor install`.)

You are running **inside** a specific harness (Claude Code, Codex, Copilot,
OpenCode, or Pi). Its post-edit hook is what feeds diagnostics back to you here,
so always make sure it is installed for *this* harness. If doctor does not list
it, offer to add it:

```bash
byor hook install --agent <this-harness>
```

If the user wants byor in other harnesses too, `byor install --agents <names>`
adds them; re-running it is safe and idempotent.

## 2. Initialize the repo (only if needed)

`byor init` is **optional**. It is only needed for rules scoped to *this* repo or
shared with a team — global/personal rules work everywhere without it. Ask once
whether the user wants repo-scoped or team-shared rules; if not, skip init.

```bash
byor init
```

## 3. Discover existing preferences

Read the user's instruction files directly — no need to ask first (a harness may
prompt for permission on files outside the repo; that is expected). Read the
canonical locations that exist:

- Global: `~/.claude/CLAUDE.md` (and any files it imports with `@`),
  `~/.codex/AGENTS.md`, `~/.agents/AGENTS.md`
- Repo: `./CLAUDE.md`, `./AGENTS.md`, `.github/copilot-instructions.md`, and
  nested variants in subdirectories

## 4. Extract and classify candidates

Pull out the durable, mechanically checkable preferences. Classify each by the
mechanism that fits (see "Choose the mechanism" in `SKILL.md`):

- **ast-grep rule** — a syntax shape, e.g. a forbidden call or banned import
  (see `references/patterns.md`)
- **formatter / linter / type checker** — see `references/checks.md`
- **byor check script** — bespoke logic over the file; see `references/checks.md`
- **not checkable** — naming taste, architecture: leave it in prose, do not force
  a check

## 5. Confirm the batch in one question

Setup can surface many candidates, so confirm the whole batch with a single
question rather than one question per rule. Present a single table —

| preference | proposed mechanism | proposed scope |

— and ask which to convert. Wait for the answer before creating anything.

## 6. Create and verify each approved candidate

For each one the user approved, follow the capture workflow in `SKILL.md` (and
`references/patterns.md` for ast-grep syntax): draft the rule, then
`byor add --scope SCOPE --from FILE` — or set up the tool/check per
`references/checks.md` — then trip it once to prove it fires.

## 7. Offer a one-time cleanup pass (repo only, optional)

Adopting byor on an existing repo can surface a wall of violations. Offer a
single isolated cleanup so the user starts clean instead of fighting the tool:

1. Create a dedicated branch, e.g. `byor/initial-cleanup`.
2. Run every rule **and check** across the whole repo and capture the output to
   a tmp file so it is referenceable and does not flood context. With no
   `--files`, `byor agent-check` scans the whole repo and runs the configured
   checks (linters, type checkers, scripts) too — not just ast-grep rules — so a
   linter you just wired in gets fixed on this branch as well:

   ```bash
   byor agent-check > /tmp/byor-cleanup-<repo>.txt 2>&1
   ```

3. Work through that file, fixing each violation per its rule's `agent_prompt`,
   committing in small atomic chunks. Leave the branch for the user to review and
   merge.

## 8. Offer to tidy the prose (optional, never automatic)

byor writes no instruction files, so leave CLAUDE.md / AGENTS.md untouched by
default. After rules exist, you may offer: "These are now enforced
mechanically — want me to remove or annotate the matching lines in your
instruction files?" Make each edit only on explicit confirmation.
