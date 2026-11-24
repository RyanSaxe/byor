# Team gates and private mode

Commit shared team rules with blocking pre-commit and CI gates, or keep your
personal byor setup completely hidden from git.

## Fresh-clone CI without byor

In a shared repo initialized with `byor init`, tracked `.gitkeep` and `.ignore`
files let `ast-grep scan` run on a fresh clone with zero byor installed. Pass
`--error` so warnings fail CI:

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

```sh
byor init --gate
uvx pre-commit install   # activate the local pre-commit hook
```

`byor init --gate` automates CI and pre-commit enforcement so teammates and CI
need only `uv`, `ast-grep`, and your check tools (no `byor`, no
`~/.config/byor`):

1. **Promotes active rules and checks** into `.byor/rules/project/` and
   `.byor/config.yml`, then clears mirrors.
2. **Vendors `~/` scripts** (and transitive literal `~/.config/byor/scripts/...`
   helpers) into `.byor/scripts/` and rewrites `run` to repo-relative paths.
3. **Generates pinned gate files** (`.pre-commit-config.yaml` on staged files
   and `.github/workflows/byor-gate.yml` whole-repo, pinned via
   `uvx ast-grep-cli`) and sets `gate: true` + `gate_branch` in
   `.byor/config.yml`.

### Strictness and agent-only checks

- `fail_on` in `.byor/config.yml`: `all` (default) passes `--error` so every
  severity blocks; `error` blocks on error severity only.
- `gate: false` on a check: keeps it in `.byor/config.yml` for agents while
  omitting it from `.pre-commit-config.yaml` and CI.

### Ownership and refresh

- **Gate files:** files carrying
  `# Managed by BYOR. Manual edits may be overwritten.` regenerate automatically
  on `byor sync` and self-heal. Unmarked files are never overwritten;
  `byor init --gate` prints snippets to paste, and `byor doctor` checks that
  required commands are present.
- **Vendored scripts:** each copy embeds a provenance marker and is listed under
  `vendored_scripts` in `.byor/local.yml`. Only the checkout that vendored it
  re-vendors on sync, preventing a teammate's home script from overwriting
  committed copies. Delete the marker to make a script user-owned.

## Git sync shims (`--git-hooks`)

If a teammate commits a project rule sharing an ID with your global mirror,
`git pull` can leave duplicates until the next sync. `byor init --git-hooks`
installs non-blocking `post-merge` and `post-checkout` shims:

```sh
#!/bin/sh
# Managed by BYOR. Manual edits may be overwritten.
[ -d .byor ] && command -v byor >/dev/null 2>&1 && byor sync || true
```

Existing unmarked hooks and repos using `core.hooksPath` are left untouched;
byor prints the `byor sync` line to add manually.

## Private mode (`--private`)

Use `--private` when a team has not adopted byor:

```sh
byor init --private
```

- Writes the ignore block for `.byor/` and `sgconfig.yml` into
  `.git/info/exclude` instead of `.gitignore` (warns if `sgconfig.yml` is
  already tracked).
- Writes `.ignore` files into every rule directory, including `project/`, so
  `ast-grep` still sees rules inside ignored `.byor/`.
- With `--gate`, writes a local `.git/hooks/pre-commit` shim running
  `byor agent-check` on staged files instead of committing CI or pre-commit
  configs.

Re-running `byor init` preserves private mode. Switch to shared mode with
`byor init --no-private` (moves the ignore block from `.git/info/exclude` to
`.gitignore`).

### Offboarding a private setup

No `deinit` command exists yet. Remove manually:

1. Delete `.byor/` and `sgconfig.yml` (unless the team owns `sgconfig.yml`).
2. Remove the `Managed by BYOR` block from `.git/info/exclude`.
3. Delete `Managed by BYOR` hooks under `.git/hooks/` (and `pre-commit.legacy`
   if present).
4. Remove the repo from `~/.local/state/byor/repos.yml` (or run
   `byor sync --all --prune` after deleting the repo).

## Disabling byor for a repository

Turn byor off for a repository or directory tree without modifying the repo:

```sh
byor disable                   # enclosing repo (or cwd outside git)
byor disable ~/work/clients    # every repo under this prefix
byor enable                    # re-enable exact entry
byor enable ~/work/clients     # lift prefix entry
```

Stored in `~/.local/state/byor/state.yml` under `disabled_repos`
(case-insensitive on macOS and Windows). In disabled repos and their worktrees:
agent hooks exit `0` silently, repo-mutating CLI commands print one notice and
exit `0`, `byor sync --all` skips them, `byor init` prompts to re-enable exact
entries (or refuses on `--non-interactive` / prefix entries), and `byor doctor`
reports them under `disabled_paths`.
