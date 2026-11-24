# Documentation

Start with [Install](getting-started/install.md) to wire up your machine and
first repository, then read [Architecture](concepts/architecture.md) and
[Agent loop](concepts/agent-loop.md) to understand how scopes, mirrors, and
agent hooks fit together.

| Page | Topic |
| --- | --- |
| [Install](getting-started/install.md) | Requirements, machine install, agent setup, and first repository |
| [Architecture](concepts/architecture.md) | Scopes, precedence, copies vs symlinks, self-healing, state, and worktrees |
| [Agent loop](concepts/agent-loop.md) | Post-edit diagnostics, line scoping, pre-command gate, and rule-capture skill |
| [Authoring rules](guides/authoring-rules.md) | AST rules, exceptions, command rules, extra checks, and check scripts |
| [Packages and profiles](guides/packages-and-profiles.md) | Exclusions, profiles, opt-in packages, promotion, and real-world dotfiles setup |
| [Team gates and private mode](guides/team-gates-and-private-mode.md) | Committed CI and pre-commit gates, script vendoring, git shims, and private mode |
| [CLI reference](reference/cli.md) | Every subcommand, flag, default, and exit code |
| [Configuration reference](reference/configuration.md) | File locations and schemas for global, repo, local, state, and rule YAML |
| [Harnesses](reference/harnesses.md) | Per-agent hook configurations, ownership markers, and skill distribution |
| [Examples](../examples/README.md) | CI-tested starter rules, command rules, check scripts, and real-world setup |
| [Contributing](contributing/README.md) | Development setup, static checks, test suite, and package layout |
