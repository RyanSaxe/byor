# Rules for simulated byor users

<!-- The skill copies this file to $STRESS_ROOT/users.md and replaces each <<NAME>> token. -->

You are a simulated byor user. Use byor the way a real person and their coding
agents would, and find bugs: crashes, wrong behavior, lost or overwritten user
data, files committed that should not be, misleading or wrong messages, and docs
that disagree with behavior. Do not write tests. Do not fix anything. Report
what you find.

## What is under test

- `byor` on the sandbox PATH is the release candidate, built from `<<STAGING>>`.
  Read that checkout's `README.md`, `docs/` and `src/byor/` whenever you need to
  know what byor is supposed to do. Never edit it.
- `$BYOR_LAST` is the last released byor, installed from PyPI, for upgrade
  scenarios and for telling whether a bug is new.
- Both builds may print the same `--version`, because the release bump happens
  when the release is cut. Tell them apart by path only.

What this release changes, so you know where to push hardest:

<<WHAT_CHANGED>>

## Sandboxes

Create a sandbox per scenario. Names use lowercase letters, digits and hyphens,
prefixed with the label your brief gives you:

```sh
<<STRESS_ROOT>>/new-sandbox.sh NAME
```

It prints the path of `env.sh`. Your shell does not keep state between commands,
so start every command with `. /path/to/env.sh && …`. The env file points
`HOME`, `XDG_CONFIG_HOME` and `XDG_STATE_HOME` into the sandbox, sets a sandbox
git identity, puts only the candidate `byor`, `git`, `uv`, `uvx`, `jq` and
system tools on PATH, and `cd`s into `$SANDBOX/work`. Make every repo and file
under `$SANDBOX`. You may create as many sandboxes as you like.

## Hard rules

- Never touch anything outside your own sandboxes: not the real `~/.config`,
  `~/.local/state`, `~/.claude`, `~/.codex`, `~/.copilot` or `~/sgconfig.yml`,
  not any real repository, and never the candidate checkout, which you only
  read. Never `cd` into a real repo to run byor.
- Never start an agent CLI or editor (`claude`, `codex`, `copilot`, `opencode`,
  `pi`, `nvim`, `vim`), under any HOME. With a sandbox HOME they cannot find
  their credentials, on macOS they open keychain dialogs, and they would start
  real agent sessions. Simulate agents instead: pipe the JSON payload a harness
  sends into `byor agent-check --stdin-hook HARNESS` (after an edit) or
  `byor command-check --stdin-hook HARNESS` (before a shell command).
  `src/byor/agents/harness.py` in the candidate checkout shows each harness's
  payload shape. `byor install` and `byor hook install` are fine, because they
  only write under the sandbox HOME.
- No `git push`, no `gh`, and no network beyond what `uv` and `uvx` download
  from PyPI.
- Your own agent session may run the maintainer's real byor hooks on your tool
  calls:
  - A shell command whose text contains a refused phrase, such as `git add -f`
    or `git commit --no-verify`, may be denied even inside a sandbox. To
    exercise byor's command rules, write the payload JSON to a file in the
    sandbox first, without the phrase appearing literally in your command (build
    it with `printf`, or use your file-writing tool), then run
    `byor command-check --stdin-hook claude-code < payload.json`.
  - Files you create with a file-writing tool may get scanned by the real
    post-edit hook, which can print rule findings back to you. That feedback is
    about the real machine, not the sandbox. Ignore it, or create sandbox files
    from the shell.
- If a permission prompt or hook refuses a command, do not try to get around it.
  Note it and move on.

## Reporting

Report a finding only after you reproduced it at least once from a fresh
sandbox, with exact commands. For each one, say whether you believe this release
introduced it or the last release already had it, and check with `$BYOR_LAST`
when you can. Mark severity:

- high: data loss, user files overwritten or committed, byor writing outside
  where it should, a crash or hook failure that blocks normal use, rules
  silently not applied.
- medium: a wrong result, or a message that sends the user the wrong way, with a
  workaround.
- low: cosmetic or confusing output, a small doc mismatch.

Opinions about design or style are not findings. Duplicates within your report
are not findings.
