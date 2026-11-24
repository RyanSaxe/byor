# CLI reference

Every subcommand supports `--help`. Pass `--version` to `byor` to print the
installed version. Repository commands accept `--repo PATH` to override upward
repository discovery from the current working directory.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success, clean scan, or hook fail-open |
| `1` | Tool, config, validation, or doctor failure |
| `2` | Diagnostics found (`agent-check`, `command-check --command`) |
| `3` | Repository is stale (`sync --check`) |

## Setup and health

### `byor install`

Register global `~/sgconfig.yml`, AI agent hooks or plugins, and the `byor`
skill once per machine.

```sh
byor install [--agents LIST] [--non-interactive]
```

| Flag | Description |
| --- | --- |
| `--agents LIST` | Comma-separated integrations: `claude-code`, `codex`, `copilot`, `antigravity`, `opencode`, `pi`, `skill` |
| `--non-interactive` | Reuse agents recorded in `state.yml` without prompting |

### `byor init`

Initialize `.byor/`, `sgconfig.yml`, ignore rules, optional git shims, and
optional team gates in a repository.

```sh
byor init [--repo PATH] [--private | --no-private] [--git-hooks | --no-git-hooks]
          [--gate | --no-gate] [--non-interactive] [--no-register]
          [--replace-sgconfig] [--profile NAME | --profiles NAME ... | --no-profile]
          [--package NAME | --packages NAME ... | --no-package]
```

| Flag | Description |
| --- | --- |
| `--private` / `--no-private` | Hide all byor files in `.git/info/exclude`, or switch a private setup to shared `.gitignore` |
| `--git-hooks` / `--no-git-hooks` | Install `post-merge` and `post-checkout` `byor sync` shims |
| `--gate` / `--no-gate` | Promote rules/checks and generate pre-commit + CI gates (or a local pre-commit shim under `--private`) |
| `--non-interactive` | Apply global `init:` defaults (or `false`) without prompting |
| `--no-register` | Do not add the repository to `repos.yml` for `byor sync --all` |
| `--replace-sgconfig` | Back up and replace an existing `sgconfig.yml` |
| `--profile NAME`, `--profiles NAME ...`, `--no-profile` | Apply profile exclusions on init, or skip global `init.profile(s)` defaults |
| `--package NAME`, `--packages NAME ...`, `--no-package` | Install opt-in packages on init, or skip global `init.package(s)` defaults |

### `byor hook`

Install or remove a single machine-wide AI integration.

```sh
byor hook install --agent AGENT
byor hook uninstall --agent AGENT
```

`AGENT` is one of `claude-code`, `codex`, `copilot`, `antigravity`, `opencode`,
`pi`, or `skill`.

### `byor doctor`

Run read-only machine and repository health checks. Exits `1` if any check
fails.

```sh
byor doctor [--repo PATH] [--quick] [--json]
```

| Flag | Description |
| --- | --- |
| `--quick` | Skip rule parsing, ID-conflict validation, and gate, vendored-script, and git-shim staleness checks |
| `--json` | Emit `{"ok": bool, "checks": [{"id", "ok", "message"}, ...]}` |

## Rules and checks

### `byor add`

Create and validate a file rule or Bash command rule, then sync.

```sh
byor add --scope project|local|global [--repo PATH] [--id RULE_ID]
         [--language LANGUAGE] [--command] [--allow-exceptions]
         [--from FILE | --edit]
```

| Flag | Description |
| --- | --- |
| `--scope` | Target scope: `project`, `local`, or `global` (required) |
| `--id RULE_ID` | Rule ID for generated template |
| `--language LANGUAGE` | Language for generated template (default: `Python`) |
| `--command` | Create a command rule under `commands/` instead of a file rule |
| `--allow-exceptions` | Append the standard `# ast-grep-ignore` sentence to `agent_prompt` |
| `--from FILE` | Copy an existing YAML rule file |
| `--edit` | Open a template in `$EDITOR` (default `vi`); saves draft path on validation failure |

Without `--from` or `--edit`, prints the template and exits without writing.

### `byor list`

List rules, checks, or tags with their scope origins.

```sh
byor list [--repo PATH] [--scope project|local|global|package|effective|all]
          [--tag TAG] [--tags] [--json]
```

| Flag | Description |
| --- | --- |
| `--scope` | Filter view (default: `effective`, what `ast-grep` sees; `all` includes skipped and excluded rules) |
| `--tag TAG` | Show only rules and checks carrying `TAG` |
| `--tags` | List discovered rule and check tags instead of rules |
| `--json` | Emit machine-readable JSON |

### `byor edit` and `byor remove`

Open an existing file rule in `$EDITOR` or delete it, then validate and sync.

```sh
byor edit RULE_ID [--repo PATH] [--scope project|local|global|auto]
byor remove RULE_ID [--repo PATH] [--scope project|local|global|auto]
```

`--scope auto` (default) resolves `project`, then `local`, then canonical
`global` under `~/.config/byor/rules/`.

### `byor promote`

Move or copy a personal or package rule or check into shared repository policy.

```sh
byor promote RULE_ID --from local|global|package [--to project] [--replace] [--repo PATH]
byor promote --check NAME [--repo PATH]
```

| Flag | Description |
| --- | --- |
| `--from` | Source scope when promoting a rule: `local`, `global`, or `package` |
| `--to` | Destination scope (only `project` is supported) |
| `--replace` | Overwrite an existing project rule file at the destination path |
| `--keep-local` | Rejected with an error (duplicate project/local IDs are invalid in `ast-grep`) |
| `--check NAME` | Copy a global or package check into `.byor/config.yml` (and vendor `~/` scripts when `gate: true`) |

### `byor exclude` and `byor include`

Disable or re-enable global or package rules, rule tags, checks, or check tags
in `.byor/local.yml`, then sync.

```sh
byor exclude [RULE_ID] [--tag TAG] [--check NAME] [--check-tag TAG] [--repo PATH]
byor include [RULE_ID] [--tag TAG] [--check NAME] [--check-tag TAG] [--repo PATH]
```

## Packages and profiles

### `byor package`

List global packages or opt a repository into or out of a package.

```sh
byor package list
byor package add NAME [--repo PATH]
byor package remove NAME [--repo PATH]
```

### `byor profile`

List global profiles or merge a profile's exclusions into `.byor/local.yml`.

```sh
byor profile list
byor profile add NAME [--repo PATH]
```

## Sync and repository control

### `byor sync`

Mirror enabled global and package rules, refresh byor ignore blocks, and
regenerate owned gate files.

```sh
byor sync [--repo PATH] [--all] [--check] [--prune]
```

| Flag | Description |
| --- | --- |
| `--all` | Fan out across every repository in `repos.yml` and their linked worktrees |
| `--check` | Report staleness without writing; exit `3` when stale |
| `--prune` | With `--all` (and not `--check`): drop registry entries whose directories no longer exist |

### `byor disable` and `byor enable`

Turn byor off or back on for a repository or a directory prefix without writing
into the repository.

```sh
byor disable [PATH]
byor enable [PATH]
```

Omitting `PATH` targets the enclosing repository root (or the current directory
outside git). `enable` only removes an exact entry from `state.yml`.

## Hook entry points

### `byor agent-check`

Run `ast-grep` and extra `checks` and render agent-facing diagnostics.

```sh
byor agent-check [--repo PATH] [--files FILE ... | --stdin-hook HARNESS]
                 [--scope edit|diff|file] [--format text|json] [--concise]
```

| Flag | Description |
| --- | --- |
| `--files FILE ...` | Files to scan (default: whole repository) |
| `--stdin-hook HARNESS` | Read hook payload from stdin (`claude-code`, `codex`, `copilot`, `antigravity`) and emit harness-native output |
| `--scope` | Diagnostic line filter: `edit`, `diff`, or `file` (default: `edit` in hook mode, `file` otherwise) |
| `--format` | `text` (default) or `json` |
| `--concise` | Render one header line plus fix instruction per finding |

### `byor command-check`

Gate a pending shell command against `language: Bash` command rules and
`command_checks`.

```sh
byor command-check (--command STRING | --stdin-hook HARNESS) [--repo PATH]
```

| Flag | Description |
| --- | --- |
| `--command STRING` | Test a shell command directly; prints deny text and exits `2` on findings |
| `--stdin-hook HARNESS` | Read pre-command payload from stdin (`claude-code`, `codex`, `copilot`, `antigravity`) and emit a JSON deny decision (always exits `0`) |
