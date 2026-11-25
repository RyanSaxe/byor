# Configuration reference

## File locations

| Path | Scope | Owner | Committed |
| --- | --- | --- | --- |
| `~/.config/byor/config.yml` | Global (`$XDG_CONFIG_HOME/byor`) | You | Dotfiles |
| `~/.config/byor/rules/` | Global file rules | You | Dotfiles |
| `~/.config/byor/commands/` | Global Bash command rules | You | Dotfiles |
| `~/.config/byor/packages/<name>/` | Opt-in bundles | You | Dotfiles |
| `~/.config/byor/scripts/` | Personal scripts | You | Dotfiles |
| `~/sgconfig.yml` | Uninitialized-repo `ast-grep` config | byor (`install`) | Machine-local |
| `~/.local/state/byor/state.yml` | Installed agents, `disabled_repos` (`$XDG_STATE_HOME/byor`) | byor | Machine-local |
| `~/.local/state/byor/repos.yml` | Registered repos for `sync --all` | byor | Machine-local |
| `.byor/config.yml` | Repo rules, checks, gate config | Team | Yes (no in `--private`) |
| `.byor/local.yml` | Per-repo exclusions, packages, vendored scripts | You | No |
| `sgconfig.yml` | Repo `ast-grep` `ruleDirs` | Team / byor | Yes (no in `--private`) |

## Global config (`~/.config/byor/config.yml`)

User-owned (never written by byor). Omitting `version` implies `1`.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `version` | int | `1` | Schema version (must be `1`) |
| `paths.rules` | string | `"rules"` | Global rules dir, relative to config dir |
| `paths.packages` | string | `"packages"` | Packages root, relative to config dir |
| `paths.commands` | string | `"commands"` | Global command rules dir |
| `paths.repos` | string | `"repos.yml"` | Registry path under state dir (or absolute) |
| `ast_grep.command` | string | `"auto"` | `"auto"` (bundled) or executable path |
| `output.concise` | bool | `false` | Concise text/hook output |
| `output.max_diagnostics` | int \| null | `null` | Max rendered diagnostics (`null` = unlimited) |
| `worktrees.copy_ignored` | bool | `false` | Copy all git-ignored files to new linked worktrees |
| `checks` | list[CheckDef] | `[]` | Global post-edit checks |
| `command_checks` | list[CommandCheckDef] | `[]` | Global pre-command checks |
| `init.private` | bool \| null | `null` | Default for `init --private` |
| `init.git_hooks` | bool \| null | `null` | Default for `init --git-hooks` |
| `init.gate` | bool \| null | `null` | Default for `init --gate` |
| `init.profile` / `init.profiles` | string / list[string] | `null` / `[]` | Default init profiles (merged) |
| `init.package` / `init.packages` | string / list[string] | `null` / `[]` | Default init packages (merged) |
| `profiles.<name>` | ProfileConfig | `{}` | Named exclusion templates |

### `CheckDef`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | string | required | Unique name (`repo > package > global`) |
| `extensions` | list[string] | `[]` | Extensions without dots (empty = all) |
| `run` | string | required | `shlex`-split command (no shell; `~` expands) |
| `tags` | list[string] | `[]` | Labels for filtering and exclusions |
| `gate` | bool | `true` | `false` omits from pre-commit and CI |

### `CommandCheckDef`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | string | required | Unique name |
| `run` | string | required | `shlex`-split command receiving command on stdin |
| `tags` | list[string] | `[]` | Labels for filtering and exclusions |

### `ProfileConfig`

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

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `version` | int | `1` | Required (`1`) |
| `project.name` | string \| null | repo dir name | Project label |
| `paths.sgconfig` | string | `"sgconfig.yml"` | Repo `sgconfig.yml` path |
| `paths.project_rules` | string | `".byor/rules/project"` | Committed team rules |
| `paths.personal_local_rules` | string | `".byor/rules/personal/local"` | Repo-local personal rules |
| `paths.personal_global_rules` | string | `".byor/rules/personal/global"` | Global rules mirror |
| `paths.personal_packages_rules` | string | `".byor/rules/personal/packages"` | Package rules mirror |
| `paths.command_rules` | string | `".byor/commands"` | Scoped Bash command rules (never in `sgconfig.yml`) |
| `checks` | list[CheckDef] | `[]` | Shared post-edit checks |
| `command_checks` | list[CommandCheckDef] | `[]` | Shared pre-command checks |
| `gate` | bool | `false` | Maintain `.pre-commit-config.yaml` and `byor-gate.yml` |
| `gate_branch` | string \| null | `null` | Branch recorded when `--gate` ran |
| `fail_on` | `"all"` \| `"error"` | `"all"` | `"all"` passes `--error`; `"error"` blocks errors only |

## Local config (`.byor/local.yml`)

Always private and git-ignored.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `version` | int | `1` | Required (`1`) |
| `global.excluded_rule_ids` | list[string] | `[]` | Excluded global/package rule IDs |
| `global.excluded_tags` | list[string] | `[]` | Excluded rule tags |
| `checks.excluded` | list[string] | `[]` | Excluded check names |
| `checks.excluded_tags` | list[string] | `[]` | Excluded check tags |
| `packages` | list[string] | `[]` | Installed package names (bare names only) |
| `vendored_scripts` | list[string] | `[]` | Repo-relative `.byor/scripts/...` paths vendored here |

## Machine state (`~/.local/state/byor/`)

Serialized via `.lock` file; written only by byor.

- `state.yml`: `version: 1`, `agents` (installed integrations), `disabled_repos`
  (absolute repo roots or directory prefixes).
- `repos.yml`: `version: 1`, `repos` (absolute paths for `byor sync --all`).

## Rule metadata

Optional `metadata.byor` block on any `ast-grep` rule YAML:

| Field | Used for |
| --- | --- |
| `rationale` | Human explanation of why the rule exists |
| `agent_prompt` | Directive emitted by `agent-check` / `command-check` (fallback: `message`) |
| `docs_url` | Link to fuller policy docs |
| `tags` | Labels for `byor list --tags`, profiles, and exclusions (default `[]`) |

## Package directory layout

Under `~/.config/byor/packages/<name>/`:

- `*.yml` / `*.yaml` (outside `commands/` and `checks.yml`): file rules mirrored
  to `.byor/rules/personal/packages/<name>/`.
- `checks.yml` (reserved): optional `checks:` and `command_checks:`.
- `commands/` (reserved): optional `language: Bash` command rules mirrored to
  `.byor/commands/personal/packages/<name>/`.
