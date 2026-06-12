# BYOLSP v0.1 Spec

Build Your Own LSP

## 1. Product Definition

BYOLSP is a small command-line application that makes ast-grep rules easy to reuse across repositories, easy to install into a repository, and easy to expose to AI coding agents.

BYOLSP does not scan source code itself. BYOLSP does not implement a language server. BYOLSP manages files and configuration so that the normal ast-grep CLI and the normal ast-grep LSP work without wrappers.

v0.1 has three responsibilities:

1. Create and maintain the ast-grep project configuration needed for custom diagnostics.
2. Manage three rule scopes: project, local personal, and global personal.
3. Install AI-agent instructions and hook entrypoints that run ast-grep and render rule-specific feedback.

The core rule engine is ast-grep. The editor integration is ast-grep LSP. The human CLI integration is ast-grep scan. BYOLSP exists to make those integrations trivial to set up and keep consistent.

## 2. Design Constraints

An implementation that violates these constraints is not v0.1 BYOLSP.

1. ast-grep is the only scanner. ast-grep LSP is the only editor integration. BYOLSP wraps neither.
2. BYOLSP introduces no second rule language. Rules on disk are real ast-grep YAML.
3. No symlinks for rules, no packs, no daemon.
4. BYOLSP only mutates files it owns: BYOLSP config, `sgconfig.yml`, ignore entries, AI integration files, git hook shims it installs, and rule files the invoked command explicitly targets. Never arbitrary source files.
5. Existing user content in `sgconfig.yml` is preserved.
6. ast-grep treats duplicate rule IDs as a hard error (verified: exit 8, scan refuses to run). BYOLSP must fail loudly before producing duplicate IDs, and must self-heal stale state that would produce them.
7. BYOLSP is usable through `uvx byolsp`.

## 3. Sync UX Model

This section is the heart of the product. Everything in sections 12–14 implements it.

Global rules are canonical in `~/.config/byolsp/rules/` and copied into each repo's `.byolsp/rules/personal/global/` because ast-grep needs plain files in plain `ruleDirs` (see section 22). Copies can go stale. The UX principle:

**Staleness is self-healing, not prevented by user discipline.**

1. **Self-heal everywhere.** Every byolsp command that operates on a repository first runs a cheap staleness check (compare the generated directory against what sync would produce) and silently syncs if stale, printing one summary line only when it changed something. `byolsp sync --check` is the single exception: it reports without writing and exits 3 when stale.
2. **Mutating commands fan out.** `add`/`edit` with global scope sync the current repo and all registered repos.
3. **Git hooks close the pull gap.** A teammate can commit a project rule whose ID matches a synced global copy; after `git pull`, the repo has duplicate IDs and `ast-grep scan`/LSP are hard-broken until sync runs — and editor-only sessions never run byolsp. `byolsp init` therefore offers opt-in `post-merge`/`post-checkout` shims that run `byolsp sync` when `.byolsp/` exists.
4. **Fresh clones degrade gracefully.** Tracked `.gitkeep` files keep the personal rule directories present, so `ast-grep scan` works with project rules immediately, before the new user has byolsp installed at all.

The user's mental model: running byolsp *anything* makes this repo correct; `byolsp sync --all` makes every repo correct.

## 4. Implementation Language

Python 3.11+. BYOLSP is not a hot-path parser — the hot path remains ast-grep. BYOLSP performs filesystem operations, YAML edits, validation, and subprocess calls.

```toml
[project]
name = "byolsp"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "ruamel.yaml>=0.18",
]

[project.scripts]
byolsp = "byolsp.cli:main"
```

Use `argparse`. No heavy CLI framework. Plain text output by default; JSON only behind explicit `--json` flags.

Use `ruamel.yaml` so comments and ordering in existing `sgconfig.yml` are preserved as much as practical. No regex-based YAML mutation.

## 5. External Dependency Contract

BYOLSP requires ast-grep 0.43.0 or newer, installed separately. Resolve the executable in this order:

1. `$BYOLSP_AST_GREP`, if set.
2. `ast-grep` on `PATH`.
3. `sg` on `PATH`.

If none is found, commands that need ast-grep exit nonzero with:

```text
ast-grep is required but was not found.

Install it, then rerun this command:
  brew install ast-grep

Other install options:
  https://ast-grep.github.io/guide/quick-start.html
```

Do not auto-install ast-grep. `byolsp doctor` must print the detected version.

## 6. Repository Layout

After `byolsp init`:

```text
repo/
  sgconfig.yml
  .byolsp/
    config.yml
    local.yml
    rules/
      project/
        .gitkeep
      personal/
        local/
          .gitkeep
        global/
          .gitkeep
    agents/
      README.md
```

Tracked:

```text
sgconfig.yml
.byolsp/config.yml
.byolsp/rules/project/.gitkeep
.byolsp/rules/personal/local/.gitkeep
.byolsp/rules/personal/global/.gitkeep
.byolsp/agents/README.md
```

Ignored:

```text
.byolsp/local.yml
.byolsp/rules/personal/local/**/*.yml
.byolsp/rules/personal/local/**/*.yaml
.byolsp/rules/personal/global/**/*.yml
.byolsp/rules/personal/global/**/*.yaml
```

`.byolsp/rules/project/` is shared team policy and committed. `.byolsp/rules/personal/local/` is private to this user and repo. `.byolsp/rules/personal/global/` is a generated build artifact — wholly owned by byolsp, mirrored by sync, never edited by hand (section 12.3).

## 7. Global Layout

The global directory is `$XDG_CONFIG_HOME/byolsp` if `XDG_CONFIG_HOME` is set, else `~/.config/byolsp`, on every platform. (Do not use platformdirs: on macOS it resolves to `~/Library/Application Support`, which is wrong for a dotfiles-friendly tool.)

```text
~/.config/byolsp/
  config.yml
  repos.yml
  rules/
    python/
      no-python-cast.yml
```

Canonical global rules live under `rules/`, optionally nested by language or topic. BYOLSP discovers `.yml` and `.yaml` files recursively.

## 8. ast-grep Config Contract

`sgconfig.yml` must include:

```yaml
ruleDirs:
  - .byolsp/rules/project
  - .byolsp/rules/personal/local
  - .byolsp/rules/personal/global
```

If `sgconfig.yml` does not exist, `byolsp init` creates it. If it exists, init preserves all existing keys and appends only missing `ruleDirs` entries.

If `ruleDirs` exists with an invalid type, init fails clearly instead of guessing:

```text
Cannot update sgconfig.yml: expected ruleDirs to be a list.
Edit sgconfig.yml manually or rerun with --replace-sgconfig.
```

`--replace-sgconfig` may overwrite, but first creates `sgconfig.yml.byolsp-backup-YYYYMMDD-HHMMSS`.

After init, these must work directly, with no byolsp in the loop:

```bash
ast-grep scan
ast-grep lsp
```

## 9. Git Ignore Contract

`byolsp init` offers two ignore modes:

1. Project `.gitignore` (default — the team should know byolsp has local generated state).
2. Local `.git/info/exclude` (private experimentation without changing shared ignore policy).

The ignored patterns are exactly the "Ignored" list in section 6, written as one marked block. Writing the block is idempotent. Do not rely on `assume-unchanged` or `skip-worktree`.

**Keeping git-ignored rules visible to ast-grep.** ast-grep's rule discovery respects gitignore, so the ignored personal rule files would otherwise never load inside a git repository (verified empirically). ast-grep also reads `.ignore` files, which git does not. Init therefore writes a byolsp-marked `.ignore` file containing `!*.yml` / `!*.yaml` negations into each personal rule directory: git ignores the rule copies, ast-grep still loads them. `doctor` checks this visibility; an unmarked user-owned `.ignore` is never touched.

## 10. Config File Schemas

All BYOLSP config files contain a top-level `version: 1`.

### 10.1 Repository Config — `.byolsp/config.yml`

```yaml
version: 1
project:
  name: null
paths:
  sgconfig: sgconfig.yml
  project_rules: .byolsp/rules/project
  personal_local_rules: .byolsp/rules/personal/local
  personal_global_rules: .byolsp/rules/personal/global
ai:
  agents: []
```

`project.name` is optional display metadata. `paths.*` are POSIX-style paths relative to repo root. `ai.agents` records installed AI integrations (used by `doctor` and `hook uninstall`).

### 10.2 Repository Local Config — `.byolsp/local.yml`

```yaml
version: 1
global:
  excluded_rule_ids: []
```

`global.excluded_rule_ids` disables specific canonical global rules for this repository only. Disabling removes the generated copy on the next sync.

### 10.3 Global Config — `~/.config/byolsp/config.yml`

```yaml
version: 1
paths:
  rules: rules
  repos: repos.yml
ast_grep:
  command: auto
```

`paths.*` are relative to the global config directory unless absolute. `ast_grep.command` may be `auto`, `ast-grep`, `sg`, or an absolute path.

### 10.4 Global Repo Registry — `~/.config/byolsp/repos.yml`

```yaml
version: 1
repos:
  - /Users/example/projects/my-repo
```

A plain list of absolute repo roots. `byolsp init` registers the current repository unless `--no-register`. `byolsp sync --all` syncs every registered repo, warning and skipping paths that no longer exist.

There is deliberately no state file. Staleness and copy provenance are derived (section 13).

## 11. Rule Files

### 11.1 Format

BYOLSP rule files are valid ast-grep YAML with optional BYOLSP metadata:

```yaml
id: no-python-cast
language: Python
severity: warning
message: Avoid typing.cast in Python code.
rule:
  pattern: cast($TYPE, $VALUE)
metadata:
  byolsp:
    rationale: >
      casting hides type model problems and can make invalid assumptions
      invisible to both reviewers and type checkers.
    agent_prompt: >
      Do not use typing.cast here. Fix the type by narrowing, changing the
      signature, introducing a protocol, or restructuring the value flow. If
      the cast is genuinely necessary, leave a concise comment explaining the
      invariant that the type checker cannot see.
    allow_with_comment: true
    tags:
      - python
      - typing
```

Required ast-grep fields: `id`, `language`, `rule`, `message`. Recommended: `severity`.

Optional `metadata.byolsp` fields: `rationale`, `agent_prompt`, `allow_with_comment`, `docs_url`, `tags`. AI hooks use `agent_prompt`, falling back to `message` when absent.

No separate compiled rule format.

### 11.2 Rule IDs

Rule IDs must be unique across the three repo rule directories combined, and unique within the canonical global rules directory. Recommended pattern:

```text
[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)*
```

Warn on IDs outside this pattern (but accept whatever ast-grep accepts). Reject duplicates before ast-grep ever sees them.

## 12. Rule Scope Semantics

### 12.1 Project Rules — `.byolsp/rules/project/`

Shared team policy, committed. Project rules override global rules by ID: sync never copies a global rule whose ID a project rule owns.

### 12.2 Local Personal Rules — `.byolsp/rules/personal/local/`

Private to the current user and repo: experiments, personal preferences, temporary diagnostics. Local rules override global rules by ID, same as project rules.

### 12.3 Global Personal Rules

Canonical source: `~/.config/byolsp/rules/`. Repo copy: `.byolsp/rules/personal/global/`.

The repo copy directory is a **build artifact**. byolsp owns it completely: sync mirrors it wholesale, and any YAML file found there that sync did not produce — including hand edits to generated copies — is overwritten or removed without ceremony on the next sync. This is documented behavior, not data loss.

Edit canonical rules with `byolsp edit --scope global RULE_ID` or by editing files under `~/.config/byolsp/rules/` directly. Commands that edit global rules sync automatically; after out-of-band edits (e.g. a dotfiles pull), any byolsp command heals the current repo and `byolsp sync --all` heals everywhere.

## 13. Sync Algorithm

Sync is a pure function from inputs to the mirrored directory — no state file.

Inputs: repo root, `.byolsp/config.yml`, `.byolsp/local.yml`, canonical global rules directory.

```text
1. Discover project, local personal, and canonical global rules recursively.
2. Parse each rule enough to read id, language, message, metadata.
3. Fail on duplicate IDs within project rules, within local rules, or within
   canonical global rules.
4. blocked_ids = project IDs ∪ local IDs ∪ local.yml excluded_rule_ids.
5. desired = { relpath below global rules root → file content }
   for each canonical global rule whose ID is not blocked.
6. Mirror .byolsp/rules/personal/global/ to exactly `desired`:
   copy new/changed files, delete YAML files not in `desired`,
   remove empty subdirectories. Leave non-YAML files (.gitkeep) alone.
7. Validate the combined effective rule set has unique IDs.
```

Relative paths below the global rules root are preserved (`rules/python/no-python-cast.yml` → `personal/global/python/no-python-cast.yml`). Never flatten; flattening invites filename conflicts.

**Staleness check** (the self-heal preamble, section 15): compute `desired`, compare with the actual directory contents by path and content hash. Stale iff they differ. This is a handful of small YAML reads — fast enough to run on every command.

**Provenance** (for `edit`): a generated copy at `personal/global/REL` maps back to canonical `~/.config/byolsp/rules/REL`. No bookkeeping needed.

## 14. Duplicate and Conflict Behavior

A conflict is two rule files with the same `id`.

| Where | Behavior |
| --- | --- |
| Within project rules | Error. User must fix. |
| Within local personal rules | Error. User must fix. |
| Within canonical global rules | Error. User must fix. |
| Project ID matches global ID | Not an error. Project wins; sync skips the copy. |
| Local ID matches global ID | Not an error. Local wins; sync skips the copy. |
| Project ID matches local ID | Error. ast-grep would see both. |

A local variation of a project rule requires a different ID.

## 15. CLI Specification

The executable is `byolsp`. Every command supports `--version`, `--help`, and `COMMAND --help`.

Commands that operate on a repository accept `--repo PATH`. If omitted, search upward from the current directory: prefer the nearest `.byolsp/config.yml`, else the nearest `.git/`, else the current directory.

**Self-heal preamble:** every repo-operating command runs the staleness check first and syncs if stale, printing one line (e.g. `byolsp: synced 2 updated global rules`) only when it changed something. Exceptions: `sync --check` (reports, never writes, exits 3 when stale) and `init` (which runs a full sync as one of its steps).

### 15.1 init

```bash
byolsp init [--repo PATH] [--agents AGENTS] [--ignore-mode project|local]
            [--git-hooks] [--non-interactive] [--no-register] [--replace-sgconfig]
```

Behavior:

1. Create the global directory, global config, and repo registry if missing.
2. Create `.byolsp/` directories, `config.yml`, `local.yml`, and `.gitkeep` files.
3. Update or create `sgconfig.yml` (section 8).
4. Write the ignore block (section 9).
5. Install requested AI agent files (section 16).
6. Install git hook shims if requested (section 15.11).
7. Register the repo globally unless `--no-register`.
8. Run sync, then `doctor --quick`.

Interactive mode (default without `--non-interactive`) asks three plain numbered-choice questions — AI integrations (`generic`, `claude-code`, `codex`, `copilot`), ignore mode, and git hooks. No terminal UI library. `--agents` takes a comma-separated list.

Running init repeatedly is safe: no duplicated YAML entries, no duplicated ignore blocks, no overwriting user-edited config without an explicit flag.

### 15.2 sync

```bash
byolsp sync [--repo PATH] [--all] [--check]
```

`sync` mirrors the current repo. `--all` mirrors every registered repo. `--check` validates without writing and exits 3 if changes would be made.

Output example:

```text
Synced 7 global rules into /path/to/repo
Skipped 2 global rules:
  no-python-cast: overridden by project rule
  no-one-line-wrapper: excluded in .byolsp/local.yml
```

### 15.3 doctor

```bash
byolsp doctor [--repo PATH] [--quick] [--json]
```

Checks: ast-grep resolvable and version readable; repo config exists; `sgconfig.yml` exists with required `ruleDirs`; rule directories exist; rule YAML parses with required fields; effective rule IDs unique; sync fresh (after the self-heal preamble this reports what it healed); registered repo paths exist; AI hook files exist for configured agents.

`--quick` may skip recursive rule validation but still checks ast-grep, sgconfig, and directories.

JSON shape:

```json
{
  "ok": true,
  "checks": [
    { "id": "ast_grep_found", "ok": true, "message": "ast-grep 0.43.0" }
  ]
}
```

### 15.4 add

```bash
byolsp add --scope project|local|global [--language LANGUAGE] [--id RULE_ID]
           [--from FILE] [--edit] [--repo PATH]
```

`--from FILE` copies an existing ast-grep YAML rule into the scope. `--edit` opens a generated template in `$EDITOR`. With neither, print the template and ask the user to rerun with one of them.

Template:

```yaml
id: REPLACE_ME
language: Python
severity: warning
message: REPLACE_ME
rule:
  pattern: REPLACE_ME
metadata:
  byolsp:
    rationale: REPLACE_ME
    agent_prompt: REPLACE_ME
    allow_with_comment: false
    tags: []
```

Validation: YAML parses; required ast-grep fields present; ID does not conflict illegally (section 14). Deep semantic validation is doctor's job.

Post-action: write rule → sync current repo (global scope also syncs all registered repos) → `doctor --quick`.

### 15.5 edit

```bash
byolsp edit RULE_ID [--scope project|local|global|auto] [--repo PATH]
```

Opens an existing rule in `$EDITOR`. `auto` resolves project, then local, then canonical global. Never open a generated copy: if resolution lands under `personal/global/REL`, open the canonical file at the same `REL` under the global rules root instead.

Post-action: validate → sync (global scope also syncs all registered repos) → `doctor --quick`.

### 15.6 promote

```bash
byolsp promote RULE_ID --from local|global --to project [--repo PATH] [--keep-local] [--replace]
```

`--from local`: copy the local rule into project rules; remove the local original unless `--keep-local`.

`--from global`: copy the canonical global rule into project rules; never remove the canonical original. Sync then skips the global copy because the project owns the ID. Do not touch `excluded_rule_ids` — the ID conflict is the suppression mechanism, and removing the project rule later lets the global rule return naturally.

If the destination project rule exists, fail unless `--replace`. Post-action: sync → `doctor --quick`.

### 15.7 exclude / include

```bash
byolsp exclude RULE_ID [--repo PATH]
byolsp include RULE_ID [--repo PATH]
```

`exclude` adds the ID to `local.yml` `global.excluded_rule_ids` and syncs (removing the copy). `include` removes it and syncs; if a project or local rule still owns the ID, the global rule stays skipped. These commands only affect global rules.

### 15.8 list

```bash
byolsp list [--repo PATH] [--scope project|local|global|effective|all] [--json]
```

Shows rules and their origin. `effective` shows what ast-grep sees after sync:

```text
project  python.no-missing-type-hints  .byolsp/rules/project/python/no-missing-type-hints.yml
local    python.experimental-api       .byolsp/rules/personal/local/python/experimental-api.yml
global   no-python-cast                .byolsp/rules/personal/global/python/no-python-cast.yml
```

`all` additionally shows skipped global rules with reasons:

```text
skipped  no-python-cast       overridden by project rule
skipped  no-one-line-wrapper  excluded in .byolsp/local.yml
```

### 15.9 agent-check

```bash
byolsp agent-check [--repo PATH] [--files FILE ...] [--format text|json] [--max-results N]
```

Runs ast-grep against files an AI agent changed and renders diagnostics for injection back into the agent context. Humans keep using `ast-grep scan` directly.

Behavior:

1. Self-heal preamble (like every command).
2. Run:

```bash
ast-grep scan --json=compact --include-metadata --color never [--max-results N] FILES...
```

3. Parse the JSON. Relevant fields per match (verified against ast-grep 0.42+): `file`, `range.start.line`/`column` (0-based), `ruleId`, `severity`, `message`, `lines`, and `metadata` when `--include-metadata` is passed.
4. Render per match: path, 1-based line:column, rule ID, severity, message, `metadata.byolsp.agent_prompt` (falling back to `message`), and the matched line.
5. Exit 0 with no diagnostics, 2 with diagnostics, 1 on tool/config error.

Rendering requirements: group by file, sort by line then rule ID. Render at most 20 diagnostics by default; if more exist, append:

```text
...and N more diagnostics. Run ast-grep scan for the full list.
```

Output must be concise, directive, and specific:

```text
BYOLSP found 1 issue in AI-written code.

src/model.py:42:13
Rule: no-python-cast
Severity: warning
Message: Avoid typing.cast in Python code.

Instruction:
Do not use typing.cast here. Fix the type by narrowing, changing the signature,
introducing a protocol, or restructuring the value flow. If the cast is genuinely
necessary, leave a concise comment explaining the invariant that the type checker
cannot see.
```

### 15.10 hook install / uninstall

```bash
byolsp hook install --agent generic|claude-code|codex|copilot [--repo PATH]
byolsp hook uninstall --agent generic|claude-code|codex|copilot [--repo PATH]
```

Every agent adapter wraps the same command: `byolsp agent-check --files <changed files>`.

- `generic`: write `.byolsp/agents/README.md` documenting the command.
- `claude-code`: install a real hook configuration if the local Claude Code hook format is detected; otherwise write `.byolsp/agents/claude-code.md` with exact wiring instructions.
- `codex` / `copilot`: write to a supported instruction location if detected; otherwise `.byolsp/agents/<agent>.md`.

v0.1 may ship instruction files for all agents and real post-write hooks only where the agent exposes a stable hook API. Do not block v0.1 on perfect hook support.

`uninstall` removes only files carrying the BYOLSP-managed marker (section 17). User-edited files are preserved with an actionable message.

### 15.11 Git hook shims

Installed by init when the user opts in. For each of `post-merge` and `post-checkout`:

- If `.git/hooks/<name>` does not exist, write a byolsp-marked `#!/bin/sh` shim:

```sh
#!/bin/sh
# Managed by BYOLSP. Manual edits may be overwritten.
[ -d .byolsp ] && command -v byolsp >/dev/null 2>&1 && byolsp sync || true
```

- If it exists with the byolsp marker, update it.
- If it exists without the marker, or `core.hooksPath` is set (husky, lefthook, etc.), do not touch anything — print the one line to add to the user's existing hook setup.

Shims must never block git operations (hence `|| true`). Uninstall = delete the marked files.

## 16. AI Instruction Files

Generated instructions are direct and operational. The generic file:

````markdown
# BYOLSP Agent Instructions

This repository uses BYOLSP to expose custom ast-grep diagnostics.

After writing or editing code, run:

```bash
byolsp agent-check --files <changed files>
```

If BYOLSP reports a diagnostic, fix it before continuing.

If a rule says an exception is allowed with a comment, only keep the violating
code when the code is genuinely necessary and add a concise comment explaining why.
````

Agent-specific files may add harness-specific wiring but keep the same core instruction.

## 17. File Ownership and Safety

BYOLSP may create and modify:

```text
sgconfig.yml
.gitignore
.git/info/exclude
.git/hooks/post-merge and post-checkout (only byolsp-marked shims)
.byolsp/**
~/.config/byolsp/**
```

Ownership rules:

- `.byolsp/rules/personal/global/` is wholly owned: sync mirrors it without asking (section 12.3).
- Generated markdown, hook, and shim files carry the marker `<!-- Managed by BYOLSP. Manual edits may be overwritten. -->` (or the `#` comment equivalent) and are only ever updated or removed when the marker is present.
- Everywhere else, prefer preserving and failing with an actionable message over overwriting.

All generated writes are atomic: write to a temp file in the same directory, flush, rename into place.

Store repo-config paths as POSIX-style relative paths; absolute paths appear only in the global registry. If two registry entries resolve to the same directory, `doctor` warns.

## 18. Error Model

```python
class ByolspError(Exception):
    exit_code: int = 1

class AstGrepNotFound(ByolspError): ...
class ConfigError(ByolspError): ...
class DuplicateRuleId(ByolspError): ...
class RuleValidationError(ByolspError): ...
class UnsafeOverwrite(ByolspError): ...
class RepoNotInitialized(ByolspError): ...
```

Expected errors print clean messages without tracebacks. `BYOLSP_DEBUG=1` enables tracebacks for unexpected errors.

Exit codes:

```text
0 success
1 configuration/tool/runtime error
2 diagnostics found by agent-check
3 sync --check found staleness
```

## 19. Security

- Never execute code from rule files; never treat YAML values as shell fragments.
- Subprocess calls pass argv lists, not shell strings. Quote paths in generated hook commands.
- Split `$EDITOR` with `shlex.split`, defaulting to `vi`.
- Never send rule contents or source code to external services.

## 20. Architecture Notes

Keep ast-grep subprocess handling (executable resolution, version parsing, JSON scans) in one isolated module with no rule-indexing logic in it. Beyond that, let the implementation find its own factoring — module layout is not part of this spec.

Type discipline: all public functions typed; avoid `Any`, `object`, and `typing.cast`; use narrow type guards for YAML values.

Tooling:

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
```

## 21. Tests

Use pytest. Tests are contract coverage, not an enumerated checklist. Every behavior in these contracts must have at least one test:

- **Init contract** (sections 8–9): file creation in an empty repo, `sgconfig.yml` preservation, invalid `ruleDirs` rejection, idempotency of init and both ignore modes.
- **Conflict table** (section 14): one test per row.
- **Sync algorithm** (section 13): copy, update, delete, exclusion, project/local override suppression, wholesale-mirror behavior on hand-dropped files, `--check` exiting 3 when stale, self-heal preamble healing a stale repo.
- **ast-grep integration** (sections 5, 15.9): executable resolution order, missing-executable message, JSON parsing, `agent_prompt` rendering with `message` fallback, exit codes 0/1/2.
- **Hooks** (sections 15.10–15.11): install idempotency, uninstall only removing marked files, unmarked git hooks left untouched.
- **CLI**: `--help`, unknown command nonzero, expected errors without tracebacks, `--repo` from outside the repo.

Deterministic rendered text, so output assertions are stable.

## 22. Why Copies Instead of Symlinks

ast-grep follows a `ruleDirs` entry that is itself a symlink, but does not load symlinked files or symlinked child directories inside a real rule directory, and `ruleDirs` does not accept globs. Because v0.1 needs plain `ast-grep scan` and plain `ast-grep lsp` to work with a normal `sgconfig.yml`, BYOLSP copies global rules instead of linking them. The cost is duplication; the benefit is compatibility. Document this in the README.

After v0.1, consider upstream ast-grep issues for: `ruleDirs` globs, symlinked rule files/directories, a config-level per-ID disable that works in LSP, and global/project config merge semantics. Any of these would let sync simplify.

## 23. Documentation Requirements

```text
README.md          BYOLSP = Build Your Own LSP; ast-grep is the engine; byolsp
                   wraps nothing; quickstart below.
docs/rules.md      Scopes, metadata, adding rules per scope, promotion, exclusion.
docs/ai-agents.md  Generic integration, per-agent status, agent-check output format.
docs/sync-model.md Why copies; self-heal model; what happens on global change,
                   promotion, and exclusion; the git-pull collision case and
                   the git hook shims.
```

README quickstart:

```bash
uvx byolsp init
ast-grep scan
ast-grep lsp
byolsp add --scope global --edit
byolsp sync --all
byolsp agent-check --files src/example.py
```

Docs must include the worked rule examples: `no-python-cast` (section 11.1) and `no-trivial-delegating-function`:

```yaml
id: no-trivial-delegating-function
language: Python
severity: warning
message: Avoid one-line functions that only delegate to another function.
rule:
  pattern: |
    def $NAME($$$ARGS):
        return $CALLEE($$$CALL_ARGS)
metadata:
  byolsp:
    rationale: >
      A function that only routes to another function often adds call-stack
      noise without creating a useful abstraction.
    agent_prompt: >
      Do not add a one-line delegating function unless it creates a meaningful
      boundary, stable public API, semantic name, or compatibility layer. Inline
      the call or use the real function directly. If this wrapper is genuinely
      necessary, leave a concise comment explaining the boundary it protects.
    allow_with_comment: true
    tags:
      - python
      - abstraction
```

These are documentation examples, labeled as starting points — not enabled defaults.

## 24. Acceptance Criteria

v0.1 is acceptable when:

1. `uvx byolsp --help` works.
2. `uvx byolsp init` creates the repository layout; existing `sgconfig.yml` content is preserved.
3. `ast-grep scan` and `ast-grep lsp` work directly after init and see project rules.
4. A canonical global rule can be added; `byolsp sync` copies it; `ast-grep scan` sees it.
5. A project or local rule with the same ID suppresses the generated copy; `exclude`/`include` work; `promote --from global` creates a project rule without duplicate IDs.
6. A stale repo is healed by running any byolsp command, and the opt-in git hooks heal the pulled-collision case.
7. `byolsp agent-check` renders `metadata.byolsp.agent_prompt` with correct exit codes.
8. `byolsp doctor` reports actionable diagnostics.
9. Tests, ruff, and ty pass.
10. Docs explain that BYOLSP means Build Your Own LSP and why copies are used.

## 25. Explicitly Deferred

Not in v0.1: rule packs, symlink-based sync, any LSP wrapper or custom LSP server, remote rule registries, rule publishing, automatic ast-grep installation, non-ast-grep engines, GUI, daemon.

## 26. Implementation Order

1. Package skeleton and CLI help.
2. Path/config loading.
3. `init` with repository layout and sgconfig editing.
4. Rule discovery and validation.
5. `sync` and the self-heal preamble.
6. `doctor`, `list`.
7. `exclude`, `include`, `add`, `edit`, `promote`.
8. `agent-check`.
9. Hook and git-shim installation.
10. Docs, full tests, type checks.

Hooks come last: they depend on the rule and sync model. There is no LSP step; ast-grep already provides the LSP.

## 27. Core Principle

BYOLSP makes custom diagnostics feel native everywhere by arranging plain files so ast-grep already knows what to do.

```bash
uvx byolsp init
byolsp add --scope global --edit
ast-grep scan
ast-grep lsp
```

No wrapper. No daemon. No custom editor protocol. Durable rule files, self-healing sync, sharp AI feedback.
