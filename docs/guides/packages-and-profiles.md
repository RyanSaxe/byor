# Packages and profiles

Global rules apply everywhere by default. Use exclusions, profiles, packages,
and promotion to tune enforcement per repository without duplicating rules.

## Exclusions

Disable individual global or package rules, tags, or checks in the current
repository by writing selectors to `.byor/local.yml` (private, git-ignored):

```sh
byor exclude RULE_ID              # exclude one global or package rule
byor include RULE_ID              # re-enable it
byor exclude --tag legacy-risk    # exclude every personal rule with this tag
byor include --tag legacy-risk
byor exclude --check ruff         # exclude a check by name
byor exclude --check-tag strict   # exclude checks by tag
byor include --check-tag strict
```

Project and local rules belong to the repository, so edit or remove them
directly; `byor exclude` refuses IDs that no global or installed-package rule
provides.

Inspect available tags and skipped rules with `byor list`:

```sh
byor list --tags
byor list --scope all
```

```text
project  python.no-typing-cast  .byor/rules/project/python.no-typing-cast.yml
skipped  no-one-line-wrapper    excluded in .byor/local.yml
```

## Profiles

Profiles are subtractive templates defined in `~/.config/byor/config.yml`. They
copy exclusion selectors into `.byor/local.yml` when applied; sync reads only
`.byor/local.yml` afterward.

```yaml
profiles:
  existing:
    description: Low-friction defaults for mature repositories.
    rules:
      excluded_tags:
        - legacy-risk
      excluded_rule_ids: []
    checks:
      excluded_tags:
        - strict
      excluded: []
```

Apply profiles at `init` time, later with `byor profile`, or set global defaults
under `init:` in `~/.config/byor/config.yml`:

```sh
byor profile list
byor profile add existing
byor init --profile existing
byor init --profiles legacy prototyping
byor init --no-profile                      # skip init.profile / init.profiles defaults
```

```yaml
init:
  profile: existing
  profiles:
    - legacy
    - prototyping
```

When both `init.profile` and `init.profiles` are configured, they merge.
`byor profile add` merges into existing `.byor/local.yml` exclusions without
clearing prior entries; remove individual selectors with `byor include`.

## Packages

Where a profile subtracts, a package adds. A package is a named folder under
`~/.config/byor/packages/` that a repository opts into explicitly. Package rules
never appear in `~/sgconfig.yml`, so they apply only in repositories where the
package is installed.

```text
~/.config/byor/packages/
  python-strict/
    no-cast.yml           # file rule (standard ast-grep YAML)
    checks.yml            # optional: checks and command_checks for this package
    commands/             # optional reserved dir: Bash command rules
      no-pip.yml
```

`checks.yml` accepts the same `checks:` and `command_checks:` lists as global
and repository config files:

```yaml
checks:
  - name: ruff-strict
    extensions: [py]
    run: uv run ruff check --select ALL
```

Manage packages per repository or configure defaults in `init:`:

```sh
byor package list
byor package add python-strict
byor package remove python-strict
byor init --packages python-strict web-conventions
byor init --no-package
```

```yaml
init:
  packages:
    - python-strict
```

`byor package add` records the package in `.byor/local.yml` and mirrors its
rules into `.byor/rules/personal/packages/<name>/` (and command rules into
`.byor/commands/personal/packages/<name>/`). Precedence stays uniform:
`project` > `local` > `package` > `global`, so opting into a package cleanly
overrides same-ID global rules or same-name global checks.

Editing a package under `~/.config/byor/packages/` updates every opted-in
repository on its next sync. `byor package remove` works even after the package
directory is deleted; `byor doctor` flags missing packages across all registered
repositories.

## Promotion

Promote a personal or package rule or check into tracked repository policy:

```sh
byor promote RULE_ID --from local                   # moves local rule into project/
byor promote RULE_ID --from global                  # copies canonical global rule into project/
byor promote RULE_ID --from package                 # copies installed package rule into project/
byor promote RULE_ID --from global --replace        # overwrite an existing destination file
byor promote --check NAME                           # copy global or package check into .byor/config.yml
```

Behavior by source:

- `--from local` copies the rule into `.byor/rules/project/` (preserving
  relative subdirectories) and deletes the local original. Keeping both would
  leave duplicate IDs across `project` and `local`, so `--keep-local` always
  fails.
- `--from global` and `--from package` leave the source untouched; sync then
  skips the mirrored copy because `project` owns the ID. Removing the project
  rule later lets the global or package rule return on the next sync.
- `--check NAME` copies the check into `.byor/config.yml`. In a gated repository
  (`gate: true`), it also vendors any `~/` scripts into `.byor/scripts/` and
  refreshes gate files.

## Real-world personal setup

For a full, real-world layout using global defaults, command safety, and opt-in
packages together, see
[RyanSaxe/dotfiles `config/byor`](https://github.com/RyanSaxe/dotfiles/tree/main/config/byor).
It organizes enforcement into three layers:

1. **Global layer (`config.yml`, `commands/`, `scripts/`).** Sets
   `output.concise: true`, `init.private: true`, and `init.packages: [craft]`.
   Seven `language: Bash` command rules (`git-add-force`,
   `git-commit-no-verify`, `git-push-force`, `no-bare-python`, `no-env-dump`,
   `no-global-installs`, `no-pip-install`) plus `guard-commands.py` prevent
   high-blast-radius shell mistakes. A single global post-edit check
   (`run-repo-linters.py`, `gate: false`) runs only the linters and type
   checkers configured in the current repository (Python `ruff`/`ty`, JS/TS
   `oxlint`, Markdown `rumdl`, Go `golangci-lint`, Lua
   `emmylua_check`/`luacheck`, Shell `shellcheck`, Nix `deadnix`/`statix`)
   read-only with a 5-second timeout.
2. **Committed repository layer.** Each repository runs its own formatters,
   linters, and project `ast-grep` rules through `prek` and CI.
3. **Opt-in design-craft packages (`packages/`).**
   - `craft`: seven cross-language check scripts (`module-headers`,
     `thin-docstrings`, `routing-wrappers`, `file-lines`, `dir-sprawl`,
     `suppression-reason`, `manifest-deps`), all marked `gate: false`.
   - `python`: PEP 723 script check plus `python.no-sys-path` and
     `python.question-monkeypatch` rules.
   - `web`: JS/TS/TSX mock-questioning rules and React Compiler
     `react.no-try-finally` rules.
