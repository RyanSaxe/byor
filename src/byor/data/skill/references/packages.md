# Author an Opt-In Package

A **package** is a named bundle of rules (and optional checks) that repos install
deliberately, instead of being applied everywhere. Reach for one when a
preference is not a single rule for *this* repo, but a reusable set that only
*some* repos should enforce.

## When a package is the right home

Compare against the three `byor add` scopes:

- `project` / `local` — this repo only. Not reusable elsewhere.
- `global` — applies in every repo by default; a repo opts *out* of specific
  rules or tags (`byor exclude`, or a profile), never in. Prefer this for a
  genuine personal standard — the opt-out escape hatch means it is not a
  commitment to enforce it everywhere no matter what.
- **package** — the inverse of global: applies nowhere until a repo opts *in*.

So a package fits phrasing like "a strict-Python rule set I can turn on per
project", "our React conventions for the repos that use React", or any group of
rules too situational to force on every repo but worth reusing.

## Author the package

A package is just a directory under the global config; each is a subdirectory
of `~/.config/byor/packages/`. It holds ordinary rule files (same format as any
other rule) and an optional `checks.yml`:

```text
~/.config/byor/packages/python-strict/
  no-cast.yml          # a rule, identical in format to a byor add rule
  no-print.yml
  checks.yml           # optional: checks this package contributes
```

`checks.yml` is a `checks:` list, the same shape as the repo and global configs
(see references/checks.md for the check contract):

```yaml
checks:
  - name: ruff-strict
    extensions: [py]
    run: uv run ruff check --select ALL
```

Draft the rules exactly as in the hub (id, language, severity, message,
`metadata.byor`), and write them into a new package directory rather than
running `byor add`. Package rule IDs must be unique across every package a repo
installs, so give them specific names.

## Install, inspect, and share

```bash
byor package list                 # packages available to install
byor package add python-strict    # opt this repo in (records it in .byor/local.yml)
byor init --packages a b          # or opt in at init time (init.packages sets a default)
byor list --scope package         # the installed package rules ast-grep now reads
```

Installing is personal and per-repo: it records the opt-in in the untracked
`.byor/local.yml` and mirrors the package's rules into
`.byor/rules/personal/packages/`, so the rules apply for you in this repo but are
**not** committed. To share a package's rules or checks with the team, promote
them into tracked config:

```bash
byor promote RULE_ID --from package   # a package rule -> .byor/rules/project/
byor promote --check NAME             # a package check -> .byor/config.yml
```

Editing a package under `~/.config/byor/packages/` and re-syncing upgrades it
everywhere it is installed, just like editing a global rule.
