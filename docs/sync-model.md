# The Sync Model

## Why copies, not symlinks

Your global rules are canonical in `~/.config/byolsp/rules/` and copied into
each repo's `.byolsp/rules/personal/global/`. Copies, because ast-grep needs
plain files in plain `ruleDirs`: it follows a `ruleDirs` entry that is itself
a symlink, but does not load symlinked files or symlinked child directories
inside a rule directory, and `ruleDirs` does not accept globs. Since v0.1
requires plain `ast-grep scan` and plain `ast-grep lsp` to work with a normal
`sgconfig.yml`, BYOLSP copies. The cost is duplication; the benefit is
compatibility.

Copies can go stale. The UX principle:

**Staleness is self-healing, not prevented by user discipline.**

## The mirror is a build artifact

`.byolsp/rules/personal/global/` is wholly owned by byolsp. Sync mirrors it
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
- it is listed in `.byolsp/local.yml` `excluded_rule_ids`
  (`excluded in .byolsp/local.yml`).

There is no state file: staleness and provenance are derived by comparing the
mirror's contents against what sync would produce.

## Self-healing

Every byolsp command that operates on a repository first runs a cheap
staleness check and silently syncs if stale, printing one line only when it
changed something:

```text
byolsp: synced 2 updated global rules
```

The exceptions: `byolsp sync` itself (its body is the sync) and
`byolsp sync --check`, which reports without writing and exits 3 when stale.

```bash
byolsp sync           # mirror this repo
byolsp sync --all     # mirror every registered repo
byolsp sync --check   # report; exit 3 when stale
```

`init` registers each repo in `~/.config/byolsp/repos.yml` (skip with
`--no-register`); `sync --all` syncs every registered repo, warning and
skipping paths that no longer exist.

The mental model: running byolsp *anything* makes this repo correct;
`byolsp sync --all` makes every repo correct.

## What happens when...

**A global rule changes.** `byolsp add`/`byolsp edit` with global scope sync
the current repo *and* all registered repos immediately. After out-of-band
edits (e.g. a dotfiles pull changes `~/.config/byolsp/rules/`), the next
byolsp command heals the current repo, and `byolsp sync --all` heals
everywhere.

**A rule is promoted.** `byolsp promote RULE_ID --from global` copies the
canonical rule into project rules; the canonical original stays. Sync then
removes the repo's generated copy because the project owns the ID — the ID
conflict is the suppression mechanism, not `excluded_rule_ids`. Delete the
project rule later and the global rule returns naturally on the next sync.

**A rule is excluded.** `byolsp exclude RULE_ID` records the ID in
`.byolsp/local.yml` and the generated copy is removed on the same sync.
`include` removes the entry and the copy comes back — unless a project or
local rule still owns the ID, in which case it stays skipped.

**A fresh clone, before byolsp is installed.** Tracked `.gitkeep` files keep
the rule directories present, so `ast-grep scan` works with project rules
immediately. Personal rules appear after `uvx byolsp init` (or any byolsp
command) runs.

## The git-pull collision case

A teammate can commit a project rule whose ID matches one of your synced
global copies. After `git pull`, the repo contains duplicate IDs and
`ast-grep scan`/LSP are hard-broken (ast-grep refuses to run on duplicate
IDs) until sync removes the now-overridden copy. Any byolsp command heals it —
but editor-only sessions never run byolsp.

That gap is closed by opt-in git hook shims: `byolsp init --git-hooks` (or
answering yes to init's third question) installs `post-merge` and
`post-checkout` hooks:

```sh
#!/bin/sh
# Managed by BYOLSP. Manual edits may be overwritten.
[ -d .byolsp ] && command -v byolsp >/dev/null 2>&1 && byolsp sync || true
```

The trailing `|| true` means a shim can never block a git operation, and the
guards make it a no-op in repos without `.byolsp/` or without byolsp
installed.

Shim safety rules:

- A hook that does not exist gets the marked shim.
- A hook that exists with the `# Managed by BYOLSP` marker is updated.
- A hook that exists without the marker, or a repo with `core.hooksPath` set
  (husky, lefthook, ...), is never touched — byolsp prints the one
  `byolsp sync` line to add to your existing hook setup instead.

## Known limitation: gitignored rule files in git repositories

`init` gitignores personal rule files with the patterns
`.byolsp/rules/personal/{local,global}/*.yml` (and `.yaml`). Inside a git
repository, ast-grep's rule discovery respects gitignore, so a rule file
sitting *directly* at one of those directory roots is skipped by
`ast-grep scan`/LSP — while a rule nested one level down
(e.g. `personal/global/python/no-python-cast.yml`) is loaded but not
gitignored, because `*` does not cross `/`. Note that `byolsp add` writes new
rules directly at the scope root. Until this is resolved, keep canonical
global and local rules nested by language or topic — move an added rule into
a subdirectory like `python/` — so the files ast-grep reads stay visible to
it.
