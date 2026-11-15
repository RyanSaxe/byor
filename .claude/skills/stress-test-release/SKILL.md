---
name: stress-test-release
description: Stress-test a byor release candidate before cutting the release. Simulated users run the candidate in isolated sandboxes on the maintainer's machine, an independent agent reproduces each finding, and the bugs this release introduced get fixed on its branch. Use when a maintainer asks to stress-test, bug-hunt or check a byor release branch. Needs Claude Code's Workflow tool. Never runs in CI.
---

# Stress-test a byor release

This is a check a maintainer runs by hand before a release, on their own machine. A dozen
simulated users use the candidate the way people and their agents do, each in sandboxes with
their own HOME. The output is a list of confirmed bugs split into those the release introduced
and those the last release already had. It complements the test suite: it finds bugs no one
thought to write a test for. It needs Claude Code's Workflow tool and launches about a hundred
agents, so confirm the cost with the maintainer before step 5.

## Steps

1. **Pick a scratch root and the two versions.** Use a fresh folder outside every repository,
   such as your session's scratch directory. `RELEASE` is the candidate branch, and `LAST` is
   the last release tag.

   ```sh
   export STRESS_ROOT=/path/to/scratch/stress
   RELEASE=my-release-branch
   LAST=$(git describe --tags --abbrev=0 origin/main)   # for example v0.4.0
   mkdir -p "$STRESS_ROOT"
   git fetch origin --tags
   git worktree add "$STRESS_ROOT/staging" "$RELEASE"
   ```

2. **Install both builds apart from the maintainer's own byor.** Each gets its own uv tool
   folder, so `~/.local/bin/byor` stays as it is.

   ```sh
   UV_TOOL_DIR="$STRESS_ROOT/tools" UV_TOOL_BIN_DIR="$STRESS_ROOT/bin" \
     uv tool install --from "$STRESS_ROOT/staging" byor
   UV_TOOL_DIR="$STRESS_ROOT/tools-last" UV_TOOL_BIN_DIR="$STRESS_ROOT/bin-last" \
     uv tool install "byor==${LAST#v}"
   cp "$(git rev-parse --show-toplevel)/.claude/skills/stress-test-release/scripts/new-sandbox.sh" "$STRESS_ROOT/"
   ```

   Check one sandbox before going on: `. "$("$STRESS_ROOT/new-sandbox.sh" smoke)" && which byor`
   must print `$STRESS_ROOT/bin/byor`.

3. **Record the maintainer's real byor files.** Run this now, save the output, and run it again
   after step 5. The two must match.

   ```sh
   real_files() { (cd ~ && for f in .config/byor/config.yml .config/byor/repos.yml \
     .local/state/byor/state.yml .local/state/byor/repos.yml .claude/settings.json \
     .codex/hooks.json .copilot/hooks.json sgconfig.yml .config/opencode/plugin/byor.ts \
     .pi/agent/extensions/byor.ts .claude/skills/byor/SKILL.md .agents/skills/byor/SKILL.md; do
     if [ -e "$f" ]; then shasum "$f"; else echo "absent $f"; fi; done); }
   real_files > "$STRESS_ROOT/real-before.txt"
   ```

4. **Write the users' rules for this release.** Copy `references/users.md` to
   `$STRESS_ROOT/users.md` and replace its tokens: `<<STAGING>>` with `$STRESS_ROOT/staging`,
   `<<STRESS_ROOT>>` with the real path, and `<<WHAT_CHANGED>>` with a numbered list of what
   the release changes. Write that list from `git log --oneline "$LAST..$RELEASE"` and
   `git diff --stat "$LAST..$RELEASE"`: one plain sentence per user-visible change, naming the
   commands and files it touches. The users push hardest where this list points.

5. **Run the workflow.** Read `references/personas.md` and pass each section as
   `{ id, scenario }`:

   ```text
   Workflow({
     scriptPath: "<absolute path to this skill>/workflow.js",
     args: { usersFile: "<STRESS_ROOT>/users.md", personas: [{ id: "newuser", scenario: "…" }, …] }
   })
   ```

   Then rerun `real_files` and `diff` it against `real-before.txt`. Any difference means a user
   broke the rules. Find which one before trusting the run.

6. **Triage.** A finding is confirmed when its verdict has `reproduced` and `isBug` both true.
   Split confirmed findings by the verdict's `introducedBy`, then merge findings that share one
   cause. Bugs the release introduced block it. Bugs the last release already had go in their
   own issues or pull requests, so the release does not grow.

7. **Fix and recheck.** Fix each bug the release introduced on top of the release branch, with a
   regression test where one fits. Reinstall the candidate with
   `uv tool install --reinstall --from "$STRESS_ROOT/staging" byor` after pulling the fixes into
   the staging worktree, and rerun each confirmed finding's `minimalRepro` in a fresh sandbox
   until none reproduces.

8. **Hunt again where the fixes landed.** Fixes bring their own edge cases. Run the workflow
   again with a few personas aimed at the code the fixes changed, and with users.md's
   "what changed" list describing the fixes. Repeat steps 6 to 8 until a pass finds nothing
   new above low severity.

## What to report

Report the number of users, sandboxes, agents and passes, the confirmed counts by origin and
severity, each bug the release introduced with its fix and recheck result, the checksum
comparison from step 5, and the findings the verifiers rejected, with their reasons.
