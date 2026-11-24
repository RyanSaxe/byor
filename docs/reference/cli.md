# CLI reference

Every subcommand supports `--help`. `byor --version` prints the installed
version. Repo commands accept `--repo PATH` to override upward repo discovery.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success, clean scan, or hook fail-open |
| `1` | Tool, config, validation, or doctor failure |
| `2` | Diagnostics found (`agent-check`, `command-check --command`) |
| `3` | Repository stale (`sync --check`) |

## Setup and health

### `byor install`

```sh
byor install [--agents LIST] [--non-interactive]
```

Writes `~/sgconfig.yml`, installs selected agent integrations and the `byor`
skill, and saves selections in `state.yml`.

| Flag | Description |
| --- | --- |
| `--agents LIST` | Comma-separated: `claude-code`, `codex`, `copilot`, `antigravity`, `opencode`, `pi`, `skill` |
| `--non-interactive` | Reuse agents recorded in `state.yml` without prompting |

### `byor init`

```sh
byor init [--repo PATH] [--private | --no-private] [--git-hooks | --no-git-hooks]
          [--gate | --no-gate] [--non-interactive] [--no-register]
          [--replace-sgconfig] [--profile NAME | --profiles NAME ... | --no-profile]
          [--package NAME | --packages NAME ... | --no-package]
```

| Flag | Description |
| --- | --- |
| `--private` / `--no-private` | Hide files in `.git/info/exclude`, or switch to shared `.gitignore` |
| `--git-hooks` / `--no-git-hooks` | Install `post-merge` and `post-checkout` `byor sync` shims |
| `--gate` / `--no-gate` | Promote rules/checks and generate pre-commit + CI gates (local shim in `--private`) |
| `--non-interactive` | Use global `init:` defaults (or `false`) without prompting |
| `--no-register` | Skip adding repo to `repos.yml` |
| `--replace-sgconfig` | Back up and replace an existing `sgconfig.yml` |
| `--profile`, `--profiles`, `--no-profile` | Apply profiles on init or skip `init.profile(s)` defaults |
| `--package`, `--packages`, `--no-package` | Install packages on init or skip `init.package(s)` defaults |

### `byor hook`

```sh
byor hook install --agent AGENT
byor hook uninstall --agent AGENT
```

`AGENT`: `claude-code`, `codex`, `copilot`, `antigravity`, `opencode`, `pi`, or
`skill`.

### `byor doctor`

```sh
byor doctor [--repo PATH] [--quick] [--json]
```

Read-only machine and repo health checks; exits `1` on failure.

| Flag | Description |
| --- | --- |
| `--quick` | Skip rule parsing, ID conflicts, and gate/vendor/git-shim staleness |
| `--json` | Emit `{"ok": bool, "checks": [{"id", "ok", "message"}, ...]}` |

## Rules and checks

### `byor add`

```sh
byor add --scope project|local|global [--repo PATH] [--id RULE_ID]
         [--language LANGUAGE] [--command] [--allow-exceptions]
         [--from FILE | --edit]
```

Without `--from` or `--edit`, prints the template without writing.

| Flag | Description |
| --- | --- |
| `--scope` | Required: `project`, `local`, or `global` |
| `--id RULE_ID` | Rule ID for generated template |
| `--language LANGUAGE` | Template language (default: `Python`) |
| `--command` | Write under `commands/` instead of `rules/` |
| `--allow-exceptions` | Append `# ast-grep-ignore` sentence to `agent_prompt` |
| `--from FILE` | Copy and validate an existing YAML file |
| `--edit` | Open template in `$EDITOR` (default `vi`); keeps draft on error |

### `byor list`

```sh
byor list [--repo PATH] [--scope project|local|global|package|effective|all]
          [--tag TAG] [--tags] [--json]
```

| Flag | Description |
| --- | --- |
| `--scope` | Default: `effective` (`all` includes skipped and excluded) |
| `--tag TAG` | Filter rules and checks by tag |
| `--tags` | List discovered tags |
| `--json` | Emit JSON |

### `byor edit` and `byor remove`

```sh
byor edit RULE_ID [--repo PATH] [--scope project|local|global|auto]
byor remove RULE_ID [--repo PATH] [--scope project|local|global|auto]
```

`--scope auto` (default) resolves `project` -> `local` -> canonical `global`.

### `byor promote`

```sh
byor promote RULE_ID --from local|global|package [--to project] [--replace] [--repo PATH]
byor promote --check NAME [--repo PATH]
```

| Flag | Description |
| --- | --- |
| `--from` | Rule source: `local`, `global`, or `package` |
| `--to` | Destination (`project` only) |
| `--replace` | Overwrite existing destination file |
| `--keep-local` | Always errors (duplicate project/local IDs are invalid) |
| `--check NAME` | Copy check into `.byor/config.yml` (vendors `~/` scripts if `gate: true`) |

### `byor exclude` and `byor include`

```sh
byor exclude [RULE_ID] [--tag TAG] [--check NAME] [--check-tag TAG] [--repo PATH]
byor include [RULE_ID] [--tag TAG] [--check NAME] [--check-tag TAG] [--repo PATH]
```

Updates `.byor/local.yml` and syncs.

## Packages and profiles

```sh
byor package list
byor package add NAME [--repo PATH]
byor package remove NAME [--repo PATH]

byor profile list
byor profile add NAME [--repo PATH]
```

## Sync and repository control

### `byor sync`

```sh
byor sync [--repo PATH] [--all] [--check] [--prune]
```

| Flag | Description |
| --- | --- |
| `--all` | Sync every repo in `repos.yml` and its linked worktrees |
| `--check` | Report staleness without writing; exit `3` if stale |
| `--prune` | With `--all` (not `--check`): drop missing repo paths |

### `byor disable` and `byor enable`

```sh
byor disable [PATH]
byor enable [PATH]
```

Defaults to enclosing repo root (or cwd outside git). `enable` removes an exact
entry from `state.yml`.

## Hook entry points

### `byor agent-check`

```sh
byor agent-check [--repo PATH] [--files FILE ... | --stdin-hook HARNESS]
                 [--scope edit|diff|file] [--format text|json] [--concise]
```

| Flag | Description |
| --- | --- |
| `--files FILE ...` | Target files (default: whole repo) |
| `--stdin-hook HARNESS` | Stdin hook mode (`claude-code`, `codex`, `copilot`, `antigravity`) |
| `--scope` | `edit`, `diff`, or `file` (default: `edit` in hook mode, `file` otherwise) |
| `--format` | `text` (default) or `json` |
| `--concise` | One header line plus fix instruction per finding |

### `byor command-check`

```sh
byor command-check (--command STRING | --stdin-hook HARNESS) [--repo PATH]
```

| Flag | Description |
| --- | --- |
| `--command STRING` | Test command directly; exit `2` on match |
| `--stdin-hook HARNESS` | Stdin hook mode (`claude-code`, `codex`, `copilot`, `antigravity`); JSON deny on stdout, always exits `0` |
