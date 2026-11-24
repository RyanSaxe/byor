# Configuration reference

## File locations

| Path | Scope | Owner | Committed |
| --- | --- | --- | --- |
| `~/.config/byor/config.yml` | Global (`$XDG_CONFIG_HOME/byor`) | You | Personal / dotfiles |
| `~/.config/byor/rules/` | Global file rules | You | Personal / dotfiles |
| `~/.config/byor/commands/` | Global Bash command rules | You | Personal / dotfiles |
| `~/.config/byor/packages/<name>/` | Opt-in rule and check bundles | You | Personal / dotfiles |
| `~/.config/byor/scripts/` | Personal check and command scripts | You | Personal / dotfiles |
| `~/sgconfig.yml` | Global `ast-grep` discovery (uninitialized repos) | byor (`install`) | Machine-local |
| `~/.local/state/byor/state.yml` | Installed agents and disabled paths (`$XDG_STATE_HOME/byor`) | byor | Machine-local |
| `~/.local/state/byor/repos.yml` | Registered repositories for `byor sync --all` | byor | Machine-local |
| `.byor/config.yml` | Repository rules, checks, and gate config | Team | Yes (no in `--private`) |
| `.byor/local.yml` | Per-repo exclusions, opted-in packages, vendored script list | You, this repo | No (git-ignored) |
| `sgconfig.yml` | Repository `ast-grep` rule directory list | Team / byor | Yes (no in `--private`) |

## Global config (`~/.config/byor/config.yml`)

byor never writes this file; missing keys use built-in defaults, and omitting
`version` implies `version: 1`.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `version` | int | `1` | Schema version (must be `1` when present) |
| `paths.rules` | string | `"rules"` | Canonical global rules directory, relative to config dir |
| `paths.packages` | string | `"packages"` | Opt-in packages root, relative to config dir |
| `paths.commands` | string | `"commands"` | Canonical global command rules directory |
| `paths.repos` | string | `"repos.yml"` | Registry filename resolved under `~/.local/state/byor/` (or an absolute path) |
| `ast_grep.command` | string | `"auto"` | `"auto"` (bundled `ast-grep`) or explicit executable path |
| `output.concise` | bool | `false` | Default concise output for text and hook feedback |
| `output.max_diagnostics` | int \| null | `null` | Positive integer cap on rendered diagnostics (`null` is unlimited) |
| `worktrees.copy_ignored` | bool | `false` | Copy all git-ignored files (not just byor files) into new linked worktrees |
| `checks` | list[CheckDef] | `[]` | Global post-edit checks |
| `command_checks` | list[CommandCheckDef] | `[]` | Global pre-command script checks |
| `init.private` | bool \| null | `null` | Default answer for `byor init --private` |
| `init.git_hooks` | bool \| null | `null` | Default answer for `byor init --git-hooks` |
| `init.gate` | bool \| null | `null` | Default answer for `byor init --gate` |
| `init.profile` / `init.profiles` | string / list[string] | `null` / `[]` | Profiles applied by default during `byor init` (merged if both set) |
| `init.package` / `init.packages` | string / list[string] | `null` / `[]` | Packages installed by default during `byor init` (merged if both set) |
| `profiles.<name>` | ProfileConfig | `{}` | Named exclusion templates (`description`, `rules`, `checks`) |

### `CheckDef` entry

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | string | required | Unique check name (repo > package > global) |
| `extensions` | list[string] | `[]` | File extensions without leading dots (empty matches all files) |
| `run` | string | required | `shlex`-split command executed without a shell; leading `~` expands |
| `tags` | list[string] | `[]` | Free-form labels for filtering and exclusions |
| `gate` | bool | `true` | Set `false` for agent-only checks omitted from pre-commit and CI gates |

### `CommandCheckDef` entry

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | string | required | Unique command-check name |
| `run` | string | required | `shlex`-split command receiving the pending shell command on stdin |
| `tags` | list[string] | `[]` | Free-form labels for filtering and exclusions |

### `ProfileConfig` entry

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

## Repository config (`.byor/config.yml`)

Created by `byor init`. Committed in shared mode; git-ignored in `--private`.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `version` | int | `1` | Required schema version (`1`) |
| `project.name` | string \| null | repo dir name | Human-readable project label |
| `paths.sgconfig` | string | `"sgconfig.yml"` | Path to repository `sgconfig.yml` |
| `paths.project_rules` | string | `".byor/rules/project"` | Committed team rules directory |
| `paths.personal_local_rules` | string | `".byor/rules/personal/local"` | Per-repo personal rules directory |
| `paths.personal_global_rules` | string | `".byor/rules/personal/global"` | Synced global rules mirror |
| `paths.personal_packages_rules` | string | `".byor/rules/personal/packages"` | Synced package rules mirror |
| `paths.command_rules` | string | `".byor/commands"` | Root for scoped Bash command rules (never in `sgconfig.yml`) |
| `checks` | list[CheckDef] | `[]` | Repository-shared extra checks |
| `command_checks` | list[CommandCheckDef] | `[]` | Repository-shared pre-command checks |
| `gate` | bool | `false` | Whether byor maintains `.pre-commit-config.yaml` and `.github/workflows/byor-gate.yml` |
| `gate_branch` | string \| null | `null` | Branch recorded when `--gate` was installed |
| `fail_on` | `"all"` \| `"error"` | `"all"` | Gate strictness (`"all"` passes `--error` to `ast-grep scan`; `"error"` blocks on error severity only) |

## Local config (`.byor/local.yml`)

Always private and git-ignored.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `version` | int | `1` | Required schema version (`1`) |
| `global.excluded_rule_ids` | list[string] | `[]` | Global or package rule IDs excluded in this repo |
| `global.excluded_tags` | list[string] | `[]` | Rule tags excluded in this repo |
| `checks.excluded` | list[string] | `[]` | Check names excluded in this repo |
| `checks.excluded_tags` | list[string] | `[]` | Check tags excluded in this repo |
| `packages` | list[string] | `[]` | Installed package directory names (bare names only, no path separators) |
| `vendored_scripts` | list[string] | `[]` | Repo-relative `.byor/scripts/...` paths vendored by this checkout |

## Machine state (`~/.local/state/byor/`)

Written exclusively by byor under a `.lock` file.

- **`state.yml`:** `version: 1`, `agents` (list of installed agent
  integrations), and `disabled_repos` (resolved absolute repo roots or directory
  prefixes).
- **`repos.yml`:** `version: 1` and `repos` (resolved absolute paths registered
  for `byor sync --all`).

## Rule metadata

Every `ast-grep` rule YAML may carry an optional `metadata.byor` mapping
(ignored by `ast-grep` itself):

| Field | Used for |
| --- | --- |
| `rationale` | Why the rule exists, for human readers |
| `agent_prompt` | Directive returned by `byor agent-check` and `byor command-check` (falls back to `message`) |
| `docs_url` | Link to fuller policy documentation |
| `tags` | Free-form labels used by `byor list --tags`, profiles, and exclusions (default `[]`) |

## Package directory layout

Each package lives at `~/.config/byor/packages/<name>/`:

- `*.yml` / `*.yaml` (outside `commands/` and `checks.yml`): file rules mirrored
  to `.byor/rules/personal/packages/<name>/`.
- `checks.yml` (reserved filename): optional `checks:` and `command_checks:`
  lists.
- `commands/` (reserved directory): optional `language: Bash` command rules
  mirrored to `.byor/commands/personal/packages/<name>/`.
