# Pick the Right Tool, or Author a Check Script

ast-grep matches **syntax**. It is the right tool when the policy is about the
shape of the code: a forbidden call, a banned import, a required construct. For
feedback that is really about something else, prefer the tool built for it.

## Pick the right tool

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

## Author a check script

Some policies are mechanically checkable but are neither a syntax shape ast-grep
matches nor something an off-the-shelf tool already owns — they need real logic
over the file. For these, write a small script and wire it as a byor `check`.
Like the tool setup above, this is a normal engineering task you do directly,
not through the byor rule commands.

A byor check runs your command on the in-scope files **without a shell** (so no
`&&`, pipe, or alias) and appends them as trailing path arguments. So the
script:

- accepts a list of file paths as its arguments;
- treats an empty argument list as "scan the whole repository", using the
  repository's normal ignored-file rules (for git repos, `git ls-files -co
  --exclude-standard` is the right shape). This is required because CI and
  generated gates may run checks without filenames, while hooks and agent
  feedback usually pass a narrowed file list;
- exits nonzero, with concise plain-text output, when any file still violates —
  that output is fed verbatim into the agent's context, so keep it short and
  free of ANSI color;
- may autofix in place first and then report only what it could not fix, so the
  next agent spends tokens only on the remainder. When it does autofix, it must
  also say what it changed and exit nonzero on any change — the harness tells the
  agent "a hook modified the file," and without a reason the agent is surprised
  its code changed.

Put it where it is callable and matches the policy's scope: a personal standard
near the global config (`~/.config/byor/scripts/`, referenced in `run` with
`~/`, which byor expands); a repo policy committed with the repo
(`.byor/scripts/`, referenced by its repo-relative path). Make it executable
with a shebang, or name the interpreter in `run`. Then add the check — to the
global config for a personal standard, or to `.byor/config.yml` for a project
one:

```yaml
checks:
  - name: no-banned-env
    extensions: [py]
    run: ~/.config/byor/scripts/no-banned-env.sh
    tags:
      - environment
```

A check that outgrows one file shares code as a path-referenced subprocess,
never a Python import — there is no package to import from, and `sys.path`
tricks break when scripts move between the two homes. A repo script resolves
its helper relative to itself (`Path(__file__).parent / "lib" / "helper.py"`).
A `~/` script must instead spell out the literal
`~/.config/byor/scripts/lib/helper.py` string, because `byor init --gate`
vendors scripts by following exactly those literal references — copying the
helper to `.byor/scripts/lib/helper.py` and rewriting the string in place — and
a `__file__`-relative reference is invisible to that scan.

Check tags are arbitrary user-defined labels, like rule tags. Use
`byor list --tags` to inspect the existing vocabulary and reuse fitting tags;
tags let profiles or `byor exclude --check-tag TAG` disable a group of checks in
one repo. Verify it like a rule: `byor list` (or `byor doctor`) shows the
effective check and its origin; then trip it on an example file to confirm it
fires.
