#!/bin/sh
# A byor `check` script that polices the agent, not the code: it fails whenever
# the dependency list in pyproject.toml differs from the last commit, so an
# agent must stop and ask before adding or removing a package. Configure it
# with `gate: false` — in pre-commit or CI this would block a person changing
# dependencies on purpose.

# The `dependencies = [...]` block, from its opening line to the first `]`.
deps() { awk '/^dependencies = \[/ { open = 1 } open { print } open && /\]/ { exit }'; }

[ -f pyproject.toml ] || exit 0
git rev-parse --verify --quiet HEAD >/dev/null 2>&1 || exit 0 # no commits yet: nothing to compare

committed=$(git show HEAD:pyproject.toml 2>/dev/null | deps)
current=$(deps <pyproject.toml)
[ "$committed" = "$current" ] && exit 0

echo "The dependency list in pyproject.toml differs from the last commit."
echo "If you added or removed a package without being asked to, revert it and ask the user first."
exit 1
