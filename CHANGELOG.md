# Changelog

All notable changes to BYOR are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - unreleased

The initial release: custom ast-grep diagnostics that are easy to set up,
share across repositories, and expose to AI coding agents.

### Added

- `byor init` — creates the repository layout and writes/merges `sgconfig.yml`
  (preserving existing keys) and the git ignore block, so plain `ast-grep scan`
  and `ast-grep lsp` work with no byor in the loop.
- Three rule scopes — project (shared), local personal, and global personal —
  with a self-healing sync that mirrors global rules into each repo and a
  conflict model where project and local rules override global by ID.
- Rule commands: `add` (with `--allow-exceptions`), `edit`, `remove`,
  `promote`, `exclude`, `include`, `list`, and `doctor`.
- `byor agent-check` — runs ast-grep on changed files and renders each rule's
  `metadata.byor.agent_prompt` as directive feedback, with
  `--scope edit|diff|file` so hooks report only the lines an agent touched.
- The byor rule-capture skill, rendered for Claude Code, Codex, Copilot, and
  OpenCode, teaching agents to turn durable feedback into ast-grep rules.
- Post-edit hook adapters for five harnesses (Claude Code, Codex, Copilot,
  OpenCode, Cursor) at project and global registration scopes.
- A configurable extra-checks runner that folds ruff/ty/etc. output into the
  same agent feedback channel.
- Git hook shims (`post-merge`, `post-checkout`) that close the pull-collision
  gap, and the `# ast-grep-ignore` suppression idiom for allowed exceptions.

[Unreleased]: https://github.com/RyanSaxe/byor/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/RyanSaxe/byor/releases/tag/v0.1.0
