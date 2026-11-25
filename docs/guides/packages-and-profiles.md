# Packages and profiles

Global rules apply everywhere by default. Use exclusions, profiles, packages,
and promotion to tune enforcement per repository without duplicating rules.

## Exclusions

Disable global or package rules, tags, or checks in `.byor/local.yml` (private,
git-ignored):

```sh
byor exclude RULE_ID              # exclude a global or package rule
byor include RULE_ID              # re-enable it
byor exclude --tag legacy-risk    # exclude personal rules by tag
byor include --tag legacy-risk
byor exclude --check ruff         # exclude a check by name
byor exclude --check-tag strict   # exclude checks by tag
byor include --check-tag strict
```

Project and local rules belong to the repository; edit or remove them directly
(`byor exclude` rejects IDs not provided by a global or installed-package rule).
Inspect tags and skipped entries with `byor list --tags` or
`byor list --scope all`.

## Profiles

Profiles are subtractive exclusion templates in `~/.config/byor/config.yml`.
Applying one merges its selectors into `.byor/local.yml`; sync reads only
`.byor/local.yml` afterward:

```yaml
profiles:
  existing:
    description: Low-friction defaults for mature repositories.
    rules:
      excluded_tags: [legacy-risk]
      excluded_rule_ids: []
    checks:
      excluded_tags: [strict]
      excluded: []
```

```sh
byor profile list
byor profile add existing
byor init --profile existing
byor init --profiles legacy prototyping
byor init --no-profile                      # skip init.profile(s) defaults
```

Set defaults in `~/.config/byor/config.yml` via `init.profile` and/or
`init.profiles` (merged if both are set). `byor profile add` merges without
clearing prior `.byor/local.yml` entries; remove individual selectors with
`byor include`.

## Packages

Packages are opt-in bundles under `~/.config/byor/packages/<name>/`. They never
appear in `~/sgconfig.yml`, so they apply only in opted-in repositories:

```text
~/.config/byor/packages/
  python-strict/
    no-cast.yml           # file rule (standard ast-grep YAML)
    checks.yml            # optional: checks and command_checks
    commands/             # optional reserved dir: Bash command rules
      no-pip.yml
```

```sh
byor package list
byor package add python-strict
byor package remove python-strict
byor init --packages python-strict web-conventions
byor init --no-package                      # skip init.package(s) defaults
```

Set default packages via `init.package` / `init.packages` in
`~/.config/byor/config.yml`. `byor package add` records the package in
`.byor/local.yml` and mirrors its rules into
`.byor/{rules,commands}/personal/packages/<name>/`. Precedence stays
`project > local > package > global`, so a package cleanly overrides same-ID
global rules or same-name global checks.

Editing a package updates every opted-in repo on next sync.
`byor package remove` still works if the folder was deleted; `byor doctor` flags
missing packages across registered repos.

## Promotion

Copy or move a personal or package rule or check into tracked repository policy:

```sh
byor promote RULE_ID --from local                   # moves local rule into project/
byor promote RULE_ID --from global                  # copies global rule into project/
byor promote RULE_ID --from package                 # copies package rule into project/
byor promote RULE_ID --from global --replace        # overwrite existing destination
byor promote --check NAME                           # copy global/package check into .byor/config.yml
```

- `--from local` deletes the local copy after copying to `.byor/rules/project/`
  (`--keep-local` always fails because duplicate project/local IDs error).
- `--from global` and `--from package` leave sources untouched; sync skips the
  losing mirror while `project` owns the ID.
- `--check NAME` copies the check into `.byor/config.yml` and, when
  `gate: true`, vendors any `~/` scripts into `.byor/scripts/` and refreshes
  gate files.

## Real-world personal setup

See
[RyanSaxe/dotfiles `config/byor`](https://github.com/RyanSaxe/dotfiles/tree/main/config/byor)
for a full three-layer setup:

1. **Global (`config.yml`, `commands/`, `scripts/`):** sets
   `output.concise: true`, `init.private: true`, and `init.packages: [craft]`;
   enforces seven Bash safety rules plus `guard-commands.py`; runs
   `run-repo-linters.py` (`gate: false`, read-only, 5s timeout) across Python,
   JS/TS, Markdown, Go, Lua, Shell, and Nix.
2. **Repository layer:** committed project `ast-grep` rules, linters, and
   formatters run in `prek` and CI.
3. **Opt-in packages (`packages/`):** `craft` (seven cross-language design-craft
   checks, `gate: false`), `python` (PEP 723, `sys.path`, `monkeypatch`), and
   `web` (JS/TS/TSX mocks and React Compiler `try ... finally`).
