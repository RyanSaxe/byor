# The Sync Model

## Why copies, not symlinks

Your global rules are canonical in `~/.config/byor/rules/` and copied into
each repo's `.byor/rules/personal/global/`. The primary reason is the override
model: because each repo holds its own copy of a global rule, a project or
local rule can shadow it by ID, a rule can be promoted from global into the
project, and a teammate's committed rule can win on the next sync — behaviors a
single shared symlink target could not express. Copies are also what let a
fresh clone lint with zero byor installed.

A symlinked *directory* in `ruleDirs` would in fact load, so symlinking is not
strictly impossible — but ast-grep does not follow symlinked rule *files* or
symlinked child directories inside a rule directory, and `ruleDirs` does not
accept globs, so copies are also the only approach that works reliably with
plain `ast-grep scan` and `ast-grep lsp`. The cost is duplication; the benefits
are the override model and compatibility.

Copies can go stale. The UX principle:

**Staleness is self-healing, not prevented by user discipline.**

## The mirror is a build artifact

`.byor/rules/personal/global/` is wholly owned by byor. Sync mirrors it
wholesale to exactly the set of enabled global rules: new and changed files
are copied, YAML files sync did not produce — including hand edits to
generated copies — are overwritten or removed without ceremony, empty
subdirectories are pruned, and non-YAML files (`.gitkeep`) are left alone.
Relative paths below the global rules root are preserved
(`rules/python/no-python-cast.yml` →
`personal/global/python/no-python-cast.yml`).

A global rule is *not* copied when:

- a project rule owns its ID (`overridden by project rule`),
- a local rule owns its ID (`overridden by local rule`), or
- it is listed in `.byor/local.yml` `excluded_rule_ids`
  (`excluded in .byor/local.yml`).

There is no state file: staleness and provenance are derived by comparing the
mirror's contents against what sync would produce.

## Self-healing

Every byor command that operates on a repository first runs a cheap
staleness check and silently syncs if stale, printing one line only when it
changed something:

```text
byor: synced 2 updated global rules
```

The exceptions: `byor sync` itself (its body is the sync),
`byor sync --check`, which reports without writing and exits 3 when stale,
and `byor init`, which runs a full sync as one of its steps.

```bash
byor sync           # mirror this repo
byor sync --all     # mirror every registered repo
byor sync --check   # report; exit 3 when stale
```

`init` registers each repo in `~/.config/byor/repos.yml` (skip with
`--no-register`); `sync --all` syncs every registered repo, warning and
skipping paths that no longer exist.

The mental model: running byor *anything* makes this repo correct;
`byor sync --all` makes every repo correct.

## What happens when...

**A global rule changes.** `byor add`/`byor edit` with global scope sync
the current repo *and* all registered repos immediately. After out-of-band
edits (e.g. a dotfiles pull changes `~/.config/byor/rules/`), the next
byor command heals the current repo, and `byor sync --all` heals
everywhere.

**A rule is promoted.** `byor promote RULE_ID --from global` copies the
canonical rule into project rules; the canonical original stays. Sync then
removes the repo's generated copy because the project owns the ID — the ID
conflict is the suppression mechanism, not `excluded_rule_ids`. Delete the
project rule later and the global rule returns naturally on the next sync.

**A rule is excluded.** `byor exclude RULE_ID` records the ID in
`.byor/local.yml` and the generated copy is removed on the same sync.
`include` removes the entry and the copy comes back — unless a project or
local rule still owns the ID, in which case it stays skipped.

**A fresh clone, before byor is installed.** Tracked `.gitkeep` and
`.ignore` files keep the rule directories present and ast-grep-visible, so
`ast-grep scan` works with project rules immediately. Personal rules appear
after `byor init` (or any byor command) runs.

That property is what makes CI cheap: a fresh clone can gate on the committed
project rules with zero byor installed. Use `--error` so warning severities
fail the build (a plain `ast-grep scan` exits 0 on warnings):

```yaml
# .github/workflows/byor-rules.yml
jobs:
  rules:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm install -g @ast-grep/cli
      - run: ast-grep scan --error
```

## The git-pull collision case

A teammate can commit a project rule whose ID matches one of your synced
global copies. After `git pull`, the repo contains duplicate IDs and
`ast-grep scan`/LSP are hard-broken (ast-grep refuses to run on duplicate
IDs) until sync removes the now-overridden copy. Any byor command heals it —
but editor-only sessions never run byor.

That gap is closed by opt-in git hook shims: `byor init --git-hooks` (or
answering yes when `byor init` asks) installs `post-merge` and `post-checkout`
hooks:

```sh
#!/bin/sh
# Managed by BYOR. Manual edits may be overwritten.
[ -d .byor ] && command -v byor >/dev/null 2>&1 && byor sync || true
```

The trailing `|| true` means a shim can never block a git operation, and the
guards make it a no-op in repos without `.byor/` or without byor
installed.

Shim safety rules:

- A hook that does not exist gets the marked shim.
- A hook that exists with the `# Managed by BYOR` marker is updated.
- A hook that exists without the marker, or a repo with `core.hooksPath` set
  (husky, lefthook, ...), is never touched — byor prints the one
  `byor sync` line to add to your existing hook setup instead.

## Git-ignored, yet visible to ast-grep

`init` gitignores personal rule files with the patterns
`.byor/rules/personal/{local,global}/**/*.yml` (and `.yaml`): local rules
are private and the global mirror is generated, so neither belongs in the
team's history. But ast-grep's rule discovery respects gitignore, which would
hide those very files from `ast-grep scan`/LSP inside a git repository.

ast-grep also reads `.ignore` files, which git does not. `init` therefore
writes a tracked `.ignore` file into each personal rule directory whose
negations un-ignore the rules for ast-grep alone:

```text
!*.yml
!*.yaml
```

Git never reads these files, so the personal rules stay out of `git status`.
Sync restores the mirror's `.ignore` if it goes missing (the mirror is wholly
byor-owned), and `byor doctor` flags either directory when its `.ignore`
no longer keeps the rules visible.
