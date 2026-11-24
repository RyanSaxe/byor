# Team gates and private mode

byor supports two opposite workflows: committing shared team policy with
blocking pre-commit and CI gates, or keeping your personal byor setup completely
invisible to git in repositories that have not adopted it.

## Fresh-clone CI without byor

In a shared repository initialized with `byor init`, tracked `.gitkeep` and
`.ignore` files make `ast-grep scan` work immediately on a fresh clone with zero
byor installation. Use `--error` so warnings fail the build (plain
`ast-grep scan` exits `0` on warnings):

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

## Committed team gate (`--gate`)

`byor init --gate` automates both CI and pre-commit enforcement so teammates and
CI runners need only `uv`, `ast-grep`, and your check tools, with no `byor` and
no `~/.config/byor`:

```sh
byor init --gate
uvx pre-commit install   # activate the local pre-commit hook
```

What `byor init --gate` does:

1. **Promotes effective rules and checks.** Copies every active global and
   package rule into `.byor/rules/project/` and every active global and package
   check into `.byor/config.yml`, then re-syncs so mirrors clear.
2. **Vendors `~/` check scripts.** Any check referencing a `~/` script (and any
   literal `~/.config/byor/scripts/...` helper referenced inside it) is copied
   into `.byor/scripts/` with its `run` command rewritten to the repo-relative
   path.
3. **Generates pinned gate files.** Sets `gate: true` and records `gate_branch`
   in `.byor/config.yml`, then writes `.pre-commit-config.yaml` (running checks
   on staged matching files) and `.github/workflows/byor-gate.yml` (running
   checks whole-repo), using a pinned `ast-grep-cli` version via `uvx`.

### Gate strictness and agent-only checks

- **`fail_on` in `.byor/config.yml`:** Defaults to `all`, which passes `--error`
  to `ast-grep scan` so every severity blocks. Set `fail_on: error` to block
  only on `error`-severity rules while still printing warnings and info.
- **`gate: false` on a check:** Keeps the check in `.byor/config.yml` for every
  contributor's agent hook while omitting it from `.pre-commit-config.yaml` and
  `.github/workflows/byor-gate.yml`.

### Ownership and automatic refresh

byor tracks ownership using marker headers:

- **Gate files (`.pre-commit-config.yaml`, `.github/workflows/byor-gate.yml`).**
  Files carrying `# Managed by BYOR. Manual edits may be overwritten.` are
  regenerated automatically by `byor sync` and self-healing commands whenever
  rules or checks change. Pre-existing unmarked files are never overwritten;
  `byor init --gate` prints the snippets to paste in, and `byor doctor` verifies
  that every required gate command is present.
- **Vendored scripts (`.byor/scripts/`).** Each copy embeds a provenance marker
  recording its `~/` source path, and this checkout records the copy under
  `vendored_scripts` in `.byor/local.yml`. Self-heal and `byor sync` re-vendor a
  script only on the checkout that vendored it, so a teammate's same-named home
  script never overwrites your committed copy. Remove the provenance marker to
  make a vendored script user-owned.

## Git sync shims (`--git-hooks`)

If a teammate commits a project rule whose ID matches one of your synced global
copies, `git pull` leaves duplicate IDs on disk and breaks `ast-grep scan` / LSP
until the next sync. Any byor command or agent hook heals it, but editor-only
sessions do not invoke byor.

`byor init --git-hooks` installs `post-merge` and `post-checkout` shims:

```sh
#!/bin/sh
# Managed by BYOR. Manual edits may be overwritten.
[ -d .byor ] && command -v byor >/dev/null 2>&1 && byor sync || true
```

The trailing `|| true` guarantees a shim never blocks a git command. Existing
hooks without the `# Managed by BYOR` marker, and repositories using
`core.hooksPath` (such as husky or lefthook), are left untouched; byor prints
the `byor sync` line to add yourself.

## Private mode (`--private`)

Use `--private` in repositories where the team has not adopted byor:

```sh
byor init --private
```

Private mode keeps `git status` clean and commits nothing:

- Writes an ignore block covering `.byor/` and `sgconfig.yml` into
  `.git/info/exclude` instead of `.gitignore`.
- Writes `.ignore` visibility files into every rule directory, including
  `project/`, so `ast-grep` still loads rules inside the ignored `.byor/` tree.
- With `--gate`, writes a local `.git/hooks/pre-commit` shim running
  `byor agent-check` on staged files instead of committing CI or pre-commit
  configs.
- Warns if `sgconfig.yml` is already tracked in git (`.git/info/exclude` cannot
  hide tracked files).

Re-running `byor init` preserves private mode. Switch a private repository to
shared mode with:

```sh
byor init --no-private
```

This removes byor's block from `.git/info/exclude` (preserving any lines you
added inside it) and writes the shared block to `.gitignore`.

### Offboarding a private setup

There is no `deinit` command yet. To remove a private setup manually:

1. Delete `.byor/` and `sgconfig.yml` (unless the team owns `sgconfig.yml`).
2. Remove the `Managed by BYOR` block from `.git/info/exclude`.
3. Delete any `Managed by BYOR` hooks under `.git/hooks/` (including a
   `pre-commit.legacy` hook if present).
4. Remove the repository path from `~/.local/state/byor/repos.yml` (or run
   `byor sync --all --prune` after removing the repo).

## Disabling byor for a repository

Because global hooks run in every repository, you can turn byor off for a single
repository or an entire directory tree without writing anything inside the repo:

```sh
byor disable                   # disable the enclosing repo (or cwd outside git)
byor disable ~/work/clients    # disable every repository under this directory prefix
byor enable                    # re-enable an exact entry
byor enable ~/work/clients     # lift a prefix entry
```

Disabled paths are stored in `~/.local/state/byor/state.yml` under
`disabled_repos` (case-insensitive on macOS and Windows volumes). In a disabled
repository (and its worktrees):

- Agent hooks exit `0` silently.
- Repo-mutating CLI commands print a single notice and exit `0`.
- `byor sync --all` skips the repository.
- `byor init` prompts to re-enable an exact entry (or refuses under
  `--non-interactive` or a prefix entry).
- `byor doctor` lists active disabled paths under `disabled_paths`.
