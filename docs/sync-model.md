# The Sync Model

This document exists so the inner loop is dependable: rules resolve the same way
on every machine, and a fresh clone can lint with no byor installed.

## Why copies, not symlinks

Your global rules are canonical in `~/.config/byor/rules/` and copied into each
repo's `.byor/rules/personal/global/`. The primary reason is the override model:
because each repo holds its own copy of a global rule, a project or local rule
can shadow it by ID, a rule can be promoted from global into the project, and a
teammate's committed rule can win on the next sync. A single shared symlink
target could not express those behaviors. Copies also let a fresh clone lint
with zero byor installed.

A symlinked *directory* in `ruleDirs` does load, so symlinking is not strictly
impossible. But ast-grep does not follow symlinked rule *files* or symlinked
child directories inside a rule directory, and `ruleDirs` does not accept globs,
so copies are also the only approach that works reliably with plain
`ast-grep scan` and `ast-grep lsp`. The cost is duplication; the benefits are
the override model and compatibility.

The same limit applies to your global rules folder: in a repo without
`byor init`, the edit hook scans through `~/sgconfig.yml`, so a rule file in
`~/.config/byor/rules` that is itself a symlink never fires there. `byor doctor`
reports such files. Link the whole folder instead, or copy the files. When byor
writes a file you linked, such as `~/.claude/settings.json`, it writes through
the link into the file it points at. byor never replaces a file you made
read-only, linked or not: writing one is an error that names the file, and
self-heal reports it as a warning for that agent. A link that points nowhere,
such as `~/.config/byor/rules` after the dotfiles checkout moved, is named by
`byor install` and `byor doctor`.

Copies can go stale. The UX principle:

**Staleness is self-healing, not prevented by user discipline.**

## The mirror is a build artifact

`.byor/rules/personal/global/` is wholly owned by byor. Sync mirrors it
wholesale to exactly the set of enabled global rules: new and changed files are
copied, YAML files sync did not produce — including hand edits to generated
copies — are overwritten or removed without ceremony, empty subdirectories are
pruned, and non-YAML files (`.gitkeep`) are left alone. Relative paths below the
global rules root are preserved (`rules/python/no-typing-cast.yml` →
`personal/global/python/no-typing-cast.yml`).

A global rule is *not* copied when:

- a project rule owns its ID (`overridden by project rule`),
- a local rule owns its ID (`overridden by local rule`),
- a surviving installed-package rule owns its ID (`overridden by package rule`),
  or
- it is listed in `.byor/local.yml` `excluded_rule_ids`
  (`excluded in .byor/local.yml`), or
- one of its `metadata.byor.tags` entries is listed in `.byor/local.yml`
  `excluded_tags` (`excluded by tag '<tag>' in .byor/local.yml`).

There is no state file: staleness and provenance are derived by comparing the
mirror's contents against what sync would produce.

## Self-healing

Every byor command that operates on a repository first runs a cheap staleness
check and silently syncs if stale, printing one line only when it changed
something:

```text
byor: synced 2 updated global rules
```

The exceptions: `byor sync` itself (its body is the sync), `byor sync --check`,
which reports without writing and exits 3 when stale, `byor init`,
`byor profile add`, and `byor package add`, which sync as one of their own
steps, and `byor doctor`, which is read-only — it reports staleness and the
command that fixes it instead of repairing anything.

The agent hooks, `byor agent-check --stdin-hook` and
`byor command-check --stdin-hook`, sync the copies the same way before they
check, and print nothing about it, since their output goes to the agent. So a
rule that arrives without byor, such as through a git pull into a linked
`~/.config/byor/rules/`, applies from the agent's next edit or command.

In a repo with `gate: true`, `byor sync` also regenerates the gate files byor
owns, and `byor sync --check` counts a stale one.

The same step, and `byor sync`, also update a byor ignore block that an older
byor wrote before it gained a personal path, such as the command-rule copies
under `.byor/commands/personal/`. byor adds the patterns the block lacks just
before its end marker. Every line already in the file stays as it was, including
lines you added inside the markers, and the block keeps the setup it was written
for, shared or private. A symlinked ignore file is updated through the link:

```text
byor: updated the byor ignore block in .gitignore
```

byor leaves a block alone when the file has no block, when its markers do not
pair up as one begin and one end, when its lines no longer say whether the setup
is shared or private, when the file is not UTF-8, or when the file is not
writable. `byor doctor` names each of these cases, and `byor sync` names a file
it cannot write. A block that cannot be updated never stops the rules from
syncing.

When git itself runs byor, as the post-checkout and post-merge shims do, byor
leaves a tracked `.gitignore` alone, so checking out a branch with an older
block never leaves the work tree dirty. The next byor command you run updates
it.

```bash
byor sync           # mirror this repo
byor sync --all     # mirror every registered repo
byor sync --check   # report; exit 3 when stale, including an outdated ignore block or gate file
```

`init` registers each repo in `~/.local/state/byor/repos.yml` (skip with
`--no-register`); `sync --all` syncs every registered repo, warning and skipping
paths that no longer exist. `byor sync --all --prune` drops those dead entries
from the registry; existing repos are never pruned.

The mental model: running byor *anything* makes this repo correct; `byor doctor`
tells you what is wrong without touching it; `byor sync --all` makes every repo
correct.

## Your settings and byor's records

byor keeps your settings and its own records in separate directories:

| Directory | Contains | Written by |
| --------- | -------- | ---------- |
| `~/.config/byor/` (`$XDG_CONFIG_HOME/byor`) | `config.yml`, `rules/`, `commands/`, `packages/`, `scripts/` | You. byor writes rules there only when you ask, as with `byor add`, and never writes `config.yml`, so a dotfiles repo can supply it. |
| `~/.local/state/byor/` (`$XDG_STATE_HOME/byor`) | `state.yml` (installed agents, disabled paths) and `repos.yml` | byor: `install`, `hook`, `disable`, `enable` and `init`. Any self-healing command also records an agent whose byor hooks or plugin file are installed but missing from `state.yml`. |

Older byor versions kept `ai.agents` and `disabled_repos` in `config.yml` and
wrote `repos.yml` beside it. The first run of this version copies all three into
the state directory and leaves the old copies alone; `byor doctor` then names
them so you can delete them. Every command makes that one-time copy, hooks and
`byor doctor` included, and it is the only write doctor ever makes. The copy
also fills a state directory that already exists, such as one linked from a
dotfiles repo, and writes `.moved-from-config` there when it finishes. After
that, a `state.yml` you delete starts empty rather than bringing the old records
back. If the copy fails, say because `config.yml` is malformed or a file cannot
be read, a command stops with an error naming the file, while a hook prints it,
keeps honoring the old `disabled_repos`, and carries on.

## Turning byor off for a repo

Global rules are opt-out: the post-edit hook applies them in any repo you edit,
including repos that never ran `byor init`. When a repo should get no byor
feedback at all — an old codebase, a team repo with conflicting standards —
disable it without writing anything into the repo:

```bash
byor disable                   # this repo (outside git: the cwd itself)
byor disable ~/work/clients    # a directory prefix: covers every repo under it
byor enable                    # undo (an exact entry only, never a prefix)
```

Entries are resolved absolute paths recorded under `disabled_repos` in byor's
state file, `~/.local/state/byor/state.yml`. On a case-insensitive volume, the
macOS and Windows default, an entry also covers paths spelled in another case.
In a covered repo the hook exits silently, self-heal leaves the repo alone
(machine-level healing still runs), and `byor sync --all` skips it with a note.
Explicit commands (`byor list`, `byor sync`, ...) print one line saying the repo
is disabled instead of running; `byor init` asks to re-enable an exact entry
before continuing, and `byor doctor` lists the disabled paths. Enabling a repo
covered by a prefix entry names that entry rather than removing it, since
lifting the prefix would re-enable every repo under it.

## What happens on changes

**A global rule changes.** `byor add`/`byor edit` with global scope sync the
current repo *and* all registered repos immediately. After out-of-band edits
(e.g. a dotfiles pull changes `~/.config/byor/rules/`), the next byor command or
agent hook heals the repo it runs in, and `byor sync --all` heals everywhere.

**A rule is promoted.** `byor promote RULE_ID --from global` copies the
canonical rule into project rules; the canonical original stays. Sync then
removes the repo's generated copy because the project owns the ID — the ID
conflict is the suppression mechanism, not `excluded_rule_ids`. Delete the
project rule later and the global rule returns naturally on the next sync.

**A rule is excluded.** `byor exclude RULE_ID` records the ID in
`.byor/local.yml` and the generated copy is removed on the same sync. `include`
removes the entry and the copy comes back — unless a project or local rule still
owns the ID, in which case it stays skipped.

**A tag is excluded.** `byor exclude --tag TAG` records the tag in
`.byor/local.yml`, and every personal global rule carrying that tag is removed
from the generated copy on the same sync. `include --tag TAG` removes the tag
selector and those global rules come back unless another skip reason still
applies.

**A package is installed.** `byor package add NAME` records the opt-in in
`.byor/local.yml` and syncs. The package's rules mirror into
`.byor/rules/personal/packages/NAME/` — a second generated copy alongside the
global mirror, kept correct by the same self-heal — and its checks apply. A
package rule a repo-owned scope already provides (project or local) is skipped;
a same-ID global rule is skipped instead, because opting into a package is an
easy avenue to override your global setup. Two installed packages claiming one
ID is a hard error.

**A fresh clone, before byor is installed.** Tracked `.gitkeep` and `.ignore`
files keep the rule directories present and ast-grep-visible, so `ast-grep scan`
works with project rules immediately. Personal rules appear after `byor init`
(or any byor command) runs.

That property makes CI cheap: a fresh clone can gate on the committed project
rules with zero byor installed. Use `--error` so warning severities fail the
build (a plain `ast-grep scan` exits 0 on warnings):

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

**A new worktree.** `git worktree add` checks out only tracked files, so the
worktree starts without `.byor/local.yml` and the generated copies, and under
`--private` without any of `.byor/` or `sgconfig.yml`. The first byor command or
agent hook in it, `byor init` and `byor sync` included, copies those files from
the main checkout, which byor finds through the worktree's `.git` file, then
syncs and prints one line:

```text
byor: copied 17 byor files from the main checkout /home/me/project
```

byor copies only files that git ignores in the main checkout and would ignore in
the worktree too. A copy therefore never shows up in `git status`, and a rule
the worktree's branch deleted stays deleted. A branch from before the repo
adopted byor has no ignore block, so it gets nothing. byor never overwrites a
file the worktree already has, and it writes `.byor/local.yml` last: that file
marks a finished copy, so a copy that failed partway is retried by the next
command, which first deletes any partial files the failed copy left. A linked
folder, such as a symlink to shared rules, is copied as the same link. Hooks
that fire at the same moment wait for one copy to finish. A command that still
finds another copy running after a few seconds stops and asks you to run it
again, rather than run against a half-copied worktree.

To also bring the other files git ignores, such as `.env` or a local build
cache, set `worktrees.copy_ignored` in `config.yml`:

```yaml
worktrees:
  copy_ignored: true # default false: copy only byor's files
```

The same rules apply to those files: byor copies one only when the worktree's
git ignores it too, and never overwrites one the worktree has. byor copies its
own files first and the others after. The command or hook that copies waits for
all of them, but hooks that fire meanwhile wait only for byor's files, which
take milliseconds, and check with the repo's rules while a large ignored folder,
such as `node_modules`, is still copying. The copy line counts the two apart:

```text
byor: copied 17 byor files and 1204 other ignored files from the main checkout /home/me/project
```

Turning `copy_ignored` on later brings the ignored files into existing worktrees
too, once, on their next byor command.

`byor doctor` and `byor sync --check` never copy. In a worktree that has not
copied yet, both say to run `byor sync`. byor treats a worktree of a repo you
disabled as disabled too, so it copies nothing there and doctor stays quiet.
Worktrees of a byor repo are never added to the registry. `byor sync --all` and
global rule changes reach them through their main checkout's entry instead, once
their copy has finished. A worktree whose main checkout is not a byor repo
registers like any other repo. A separate clone has no link to another checkout,
so it still needs `byor init`.

## The team gate

`byor init --gate` automates that CI file and a matching
`.pre-commit-config.yaml`, and first makes them self-contained. It
**promotes everything**: every effective global and package rule into
`.byor/rules/project/`, every global and package check into `.byor/config.yml`,
and any check that runs a `~/` script into a committed copy under
`.byor/scripts/` with the command repointed.

Each vendored copy carries a provenance marker recording its source as the check
names it, such as `~/.config/byor/scripts/lint.sh`, even when that folder is a
symlink. `byor init --gate` and `byor promote --check` list the copies they
write in `.byor/local.yml`, and self-heal and `byor sync` re-vendor only those,
when their source changes. A teammate's clone has no such list, so a same-named
script in their home never replaces the committed copy. A gate set up by an
older byor has no list either: run `byor init --gate` once on the machine that
maintains it. Removing the marker makes the copy user-owned, never rewritten.

The emitted artifacts run `ast-grep scan` through uvx with a pinned
`ast-grep-cli` version, so the gate never drifts with upstream releases, and run
each check directly. The whole gate enforces with
**no byor and no `~/.config/byor`**: only uv, ast-grep, and the check commands.
pre-commit passes each check its staged matching files (via a `files:` filter
from `extensions`); CI runs each check whole-repo, mirroring byor's two scan
modes.

The workflow gates pushes to the branch recorded as `gate_branch` in
`.byor/config.yml` at install time, so regenerating from a feature-branch
checkout never rewrites it.

`fail_on` in `.byor/config.yml` sets how strict the gate is. The default,
`fail_on: all`, appends `--error` so every rule blocks regardless of severity.
`fail_on: error` runs a bare `ast-grep scan`: only error-severity rules block,
while warnings and infos still print. Both gate files render from the same
setting, and doctor's staleness check respects it.

The artifacts are byor-owned build products, like the rule mirror. A committed
`gate: true` in `.byor/config.yml` marks the repo, and any self-healing byor
command, and `byor sync`, regenerates the CI workflow and the pre-commit config
it owns from the current committed rules and checks (`byor doctor` only reports
them stale). So the maintainer adds a rule or check with byor as usual and the
gate refreshes itself; teammates only ever run it, and need nothing byor. byor
owns each file by its marker header (the whole file, not a block within it): a
pre-existing, unmarked `.pre-commit-config.yaml` is never overwritten.
`byor init --gate` prints the block to paste in instead, and `byor doctor` fails
`gate_files` while the file lacks one of the gate's commands.

Under `--private` the gate commits nothing: byor installs a local
`.git/hooks/pre-commit` shim that runs `byor agent-check` on the staged files
(and no-ops when byor is not installed), consistent with private mode hiding
byor's whole footprint.

## The git-pull collision case

A teammate can commit a project rule whose ID matches one of your synced global
copies. After `git pull`, the repo contains duplicate IDs and
`ast-grep scan`/LSP are hard-broken (ast-grep refuses to run on duplicate IDs)
until sync removes the now-overridden copy. Any self-healing byor command heals
it (doctor reports it). But editor-only sessions never run byor.

That gap is closed by opt-in git hook shims: `byor init --git-hooks` (or
answering yes when `byor init` asks) installs `post-merge` and `post-checkout`
hooks:

```sh
#!/bin/sh
# Managed by BYOR. Manual edits may be overwritten.
[ -d .byor ] && command -v byor >/dev/null 2>&1 && byor sync || true
```

The trailing `|| true` means a shim can never block a git operation, and the
guards make it a no-op in repos without `.byor/` or without byor installed.

Shim safety rules:

- A hook that does not exist gets the marked shim.
- A hook that exists with the `# Managed by BYOR` marker is updated.
- A hook that exists without the marker, or a repo with `core.hooksPath` set
  (husky, lefthook, ...), is never touched — byor prints the one `byor sync`
  line to add to your existing hook setup instead.

## Git-ignored, yet visible to ast-grep

`init` gitignores personal rule files with the patterns
`.byor/rules/personal/{local,global,packages}/**/*.yml` (and `.yaml`): local
rules are private and the global and package mirrors are generated, so none
belong in the team's history. But ast-grep's rule discovery respects gitignore,
which would hide those very files from `ast-grep scan`/LSP inside a git
repository.

ast-grep also reads `.ignore` files, which git does not. `init` therefore writes
a tracked `.ignore` file into each personal rule directory whose negations
un-ignore the rules for ast-grep alone:

```text
!*.yml
!*.yaml
```

Git never reads these files, so the personal rules stay out of `git status`.
Sync restores a mirror's `.ignore` if it goes missing (the mirrors are wholly
byor-owned), and `byor doctor` flags any personal directory whose `.ignore` no
longer keeps the rules visible.

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
  post-checkout for sync; pre-commit for the private gate above) — also inside
  `.git/`.

Because the project rule directory is now git-ignored too, init writes the
`.ignore` visibility file into *every* rule directory (not just the personal
ones), keeping all rules loadable by ast-grep.

`.git/info/exclude` only affects untracked files, so if `sgconfig.yml` is
already committed (the team uses ast-grep independently), byor's edits to it
still show in `git status`; init warns when it detects this.

Running `byor init` again keeps a private setup private.
`byor init --no-private` turns it shared: it removes byor's block from
`.git/info/exclude`, keeping any lines you added inside it, and writes the
shared block to `.gitignore`.

There is no deinit command yet, so offboarding a private setup is manual:

- Delete `.byor/` and the repo-root `sgconfig.yml` (unless the team owns it).
- Remove the `Managed by BYOR` block from `.git/info/exclude`.
- Delete any `Managed by BYOR` hooks from `.git/hooks/`, including a
  `pre-commit.legacy` left by `pre-commit install`: it carries the marker and
  must be deleted too.
- Drop the repo's line from `~/.local/state/byor/repos.yml` (under
  `$XDG_STATE_HOME/byor` when that is set), so `sync --all` and `doctor` stop
  looking for it.
