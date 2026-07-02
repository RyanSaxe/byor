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
(`rules/python/no-typing-cast.yml` →
`personal/global/python/no-typing-cast.yml`).

A global rule is *not* copied when:

- a project rule owns its ID (`overridden by project rule`),
- a local rule owns its ID (`overridden by local rule`),
- a surviving installed-package rule owns its ID
  (`overridden by package rule`), or
- it is listed in `.byor/local.yml` `excluded_rule_ids`
  (`excluded in .byor/local.yml`), or
- one of its `metadata.byor.tags` entries is listed in `.byor/local.yml`
  `excluded_tags` (`excluded by tag '<tag>' in .byor/local.yml`).

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
`byor init`, `byor profile add`, and `byor package add`, which sync as one of
their own steps, and `byor doctor`, which is read-only — it reports staleness
and the command that fixes it instead of repairing anything itself.

```bash
byor sync           # mirror this repo
byor sync --all     # mirror every registered repo
byor sync --check   # report; exit 3 when stale
```

`init` registers each repo in `~/.config/byor/repos.yml` (skip with
`--no-register`); `sync --all` syncs every registered repo, warning and
skipping paths that no longer exist.

The mental model: running byor *anything* makes this repo correct;
`byor doctor` tells you what is wrong without touching it;
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

**A tag is excluded.** `byor exclude --tag TAG` records the tag in
`.byor/local.yml`, and every personal global rule carrying that tag is removed
from the generated copy on the same sync. `include --tag TAG` removes the tag
selector and those global rules come back unless another skip reason still
applies.

**A package is installed.** `byor package add NAME` records the opt-in in
`.byor/local.yml` and syncs. The package's rules mirror into
`.byor/rules/personal/packages/NAME/` — a second generated copy alongside the
global mirror, kept correct by the same self-heal — and its checks apply. A
package rule a repo-owned scope already provides (project or local) is
skipped; a same-ID global rule is skipped instead, because opting into a
package is an easy avenue to override your global setup. Two installed
packages claiming one ID is a hard error.

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
      - uses: astral-sh/setup-uv@v6
      - run: uvx --from ast-grep-cli ast-grep scan --error
```

## The team gate

`byor init --gate` automates that CI file and a matching
`.pre-commit-config.yaml`, and first makes them self-contained. It **promotes
everything** — every effective global and package rule into `.byor/rules/project/`,
every global and package check into `.byor/config.yml`, and any check that runs
a `~/` script into a committed copy under `.byor/scripts/` with the command
repointed. Each vendored copy carries a provenance marker recording its source:
self-heal re-vendors the copy when that source changes on your machine, and
removing the marker makes the copy user-owned, never rewritten. The emitted
artifacts then run `ast-grep scan` through uvx with a pinned `ast-grep-cli`
version — so the gate never drifts with upstream releases — and each check
directly, so the whole gate enforces with **no byor and no `~/.config/byor`**
— just uv, ast-grep, and the check commands. pre-commit passes each check its staged matching files
(via a `files:` filter from `extensions`); CI runs each check whole-repo,
mirroring byor's two scan modes. The workflow gates pushes to the branch
recorded as `gate_branch` in `.byor/config.yml` at install time, so
regenerating from a feature-branch checkout never rewrites it.

`fail_on` in `.byor/config.yml` sets how strict the gate is. The default,
`fail_on: all`, appends `--error` so every rule blocks regardless of severity.
`fail_on: error` runs a bare `ast-grep scan`: only error-severity rules block,
while warnings and infos still print. Both gate files render from the same
setting, and doctor's staleness check respects it.

The artifacts are byor-owned build products, like the rule mirror. A committed
`gate: true` in `.byor/config.yml` marks the repo, and any self-healing byor
command regenerates the CI workflow and the pre-commit config it owns from the
current committed rules and checks (`byor doctor` only reports them stale). So the maintainer adds a rule or check with byor as
usual and the gate refreshes itself; teammates only ever run it, and need
nothing byor. byor owns each file by its marker header (the whole file, not a
block within it): a pre-existing, unmarked `.pre-commit-config.yaml` is never
overwritten — byor prints the block to paste in instead.

Under `--private` the gate commits nothing: byor installs a local
`.git/hooks/pre-commit` shim that runs `byor agent-check` on the staged files
(and no-ops when byor is not installed), consistent with private mode hiding
byor's whole footprint.

## The git-pull collision case

A teammate can commit a project rule whose ID matches one of your synced
global copies. After `git pull`, the repo contains duplicate IDs and
`ast-grep scan`/LSP are hard-broken (ast-grep refuses to run on duplicate
IDs) until sync removes the now-overridden copy. Any self-healing byor command
heals it (doctor reports it) — but editor-only sessions never run byor.

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
`.byor/rules/personal/{local,global,packages}/**/*.yml` (and `.yaml`): local
rules are private and the global and package mirrors are generated, so none
belong in the team's history. But ast-grep's rule discovery respects gitignore,
which would hide those very files from `ast-grep scan`/LSP inside a git repository.

ast-grep also reads `.ignore` files, which git does not. `init` therefore
writes a tracked `.ignore` file into each personal rule directory whose
negations un-ignore the rules for ast-grep alone:

```text
!*.yml
!*.yaml
```

Git never reads these files, so the personal rules stay out of `git status`.
Sync restores a mirror's `.ignore` if it goes missing (the mirrors are wholly
byor-owned), and `byor doctor` flags any personal directory whose `.ignore`
no longer keeps the rules visible.

## Private setups

By default the personal-file ignore block goes in `.gitignore`, so every
teammate's clone ignores stray personal byor files too — the config, project
rules, and `sgconfig.yml` stay tracked and shared.

`byor init --private` is for using byor on a repo the team has not adopted it
for. Everything byor creates stays untracked, so `git status` stays clean and
nothing byor lands in a commit:

- `.byor/` — config, rule directories, and local state, ignored as a unit.
- `sgconfig.yml` at the repo root, so ast-grep still discovers the rules.
- An ignore block covering both of the above, written to `.git/info/exclude`
  instead of `.gitignore` — `.git/` itself is never tracked.
- With `--git-hooks` or `--gate`: shims under `.git/hooks/` (post-merge and
  post-checkout for sync; pre-commit for the private gate above) — also
  inside `.git/`.

Because the project rule directory is now git-ignored too, init writes the
`.ignore` visibility file into *every* rule directory (not just the personal
ones), keeping all rules loadable by ast-grep.

`.git/info/exclude` only affects untracked files, so if `sgconfig.yml` is
already committed (the team uses ast-grep independently), byor's edits to it
still show in `git status`; init warns when it detects this.

There is no deinit command yet, so offboarding a private setup is manual:
delete `.byor/` and the repo-root `sgconfig.yml` (unless the team owns it),
remove the `Managed by BYOR` block from `.git/info/exclude`, delete any
`Managed by BYOR` hooks from `.git/hooks/`, and drop the repo's line from
`~/.config/byor/repos.yml` so `sync --all` and `doctor` stop looking for it.
