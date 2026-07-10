# Command rules and command checks

byor's pre-command gate runs before every shell command an agent issues. It
matches the pending command against `language: Bash` ast-grep rules and, on a
match, denies the command with the rule's `agent_prompt` as the correction —
the agent rewrites and reruns. This is steering, not a sandbox: the gate
catches an agent typing a command plainly, and makes no claim to stop a
determined evasion (`sh -c "..."` embeds the command in a string the parser
correctly sees as a string). Never present a command rule as a security
boundary.

## Why patterns beat regex here

The command line is parsed as Bash by tree-sitter, so a pattern matches the
command *structure*, not the raw text:

- `pip install $$$ARGS` matches inside `cd docs && pip install x | tee log` —
  compound commands and pipelines are transparent.
- It does **not** match `echo "pip install x"` (a string) or
  `uv pip install x` (the command name is `uv`).

## The rule shape

Identical to a file rule; only the language and the tree it lives in differ:

```yaml
id: no-pip-install
language: Bash
severity: error
message: This machine manages Python dependencies with uv, not pip.
rule:
  any:
    - pattern: pip install $$$ARGS
    - pattern: pip3 install $$$ARGS
    - pattern: python -m pip install $$$ARGS
metadata:
  byor:
    rationale: >
      pip installs bypass uv's lockfile; the environment drifts from uv.lock.
    agent_prompt: >
      Use uv instead: `uv add <package>` to add a dependency, `uv sync` to
      install what the lockfile says.
    tags:
      - commands
      - deps
```

Authoring notes:

- **Any match denies** — there is no severity threshold. Only capture commands
  the user always wants rewritten.
- **The agent_prompt must name the replacement command.** A deny without a
  replacement stalls the agent; "use `uv add <package>`" unblocks it.
- **Cover the spellings** with `any:` (`pip`, `pip3`, `python -m pip`), and
  use `has:` with an anchored `regex` to match a flag at any argument position:

  ```yaml
  rule:
    pattern: git push $$$ARGS
    has: { kind: word, regex: ^(--force|-f)$, stopBy: end }
  ```

  The anchored regex keeps `--force-with-lease` legal.
- **Command rules live in their own tree** (`.byor/commands/`, global
  `~/.config/byor/commands/`) with the same project/local/global scopes and
  precedence as file rules. They never join sgconfig `ruleDirs`, so they
  cannot fire on shell-script files — and a `.sh`-file rule cannot fire on
  commands. IDs are a separate universe from file rules, but `byor exclude`
  silences both universes for one ID.

## Create and verify

```bash
byor add --scope SCOPE --command --from FILE
byor command-check --command 'pip install requests'   # expect the rule id, exit 2
byor command-check --command 'uv add requests'        # expect silence, exit 0
```

Always verify both directions: the offending command is denied and the
replacement passes. `byor add --command` refuses a rule ast-grep cannot load —
important because the gate fails open, so a broken rule on disk would silently
disable all command gating (`byor doctor` reports this).

## Command checks: the script escape hatch

When the decision needs runtime state a static pattern cannot see ($HOME
expansion, file existence, time), use a `command_checks` entry instead of a
rule:

```yaml
# .byor/config.yml or ~/.config/byor/config.yml
command_checks:
  - name: protect-ssh
    run: ~/.config/byor/scripts/protect-ssh.sh
    tags:
      - commands
```

The contract: byor pipes the pending command to the script's stdin; a nonzero
exit denies it and the script's output becomes the correction. Command checks
run on **every** command the agent issues, so they must be fast — well under
100ms, no network, no heavy interpreter startup. A check that hangs is cut off
and skipped, never blocking the agent. Prefer a command rule whenever a
pattern can express the policy.

## What command rules cannot do

- Gate commands run by a human in a terminal — the hook fires only inside the
  agent harness.
- Act as a security boundary (see the top of this file). For genuinely
  dangerous operations, rely on the harness's permission system and say so.
- Match inside strings, heredocs, or `sh -c` payloads — by design.
