#!/bin/sh
# Make one isolated byor user: its own HOME, XDG folders and git identity, and a PATH that holds
# only the byor build under test and the tools a user needs.
#
# Usage: new-sandbox.sh NAME [STRESS_ROOT]
# STRESS_ROOT defaults to the environment variable of the same name. The script prints the path
# of the sandbox's env.sh. A shell does not keep state between agent tool calls, so start every
# command with `. ENV_SH && ...`.
set -eu

name="${1:?usage: new-sandbox.sh NAME [STRESS_ROOT]}"
stress_root="${2:-${STRESS_ROOT:?set STRESS_ROOT or pass it as the second argument}}"
root="$stress_root/agents/$name"
toolbin="$stress_root/toolbin"

# Link the tools a sandbox may use, found on the caller's PATH before HOME changes. `ln -sf`
# because several sandboxes are often created at the same moment.
mkdir -p "$toolbin"
for tool in git uv uvx; do
  path="$(command -v "$tool")" || { echo "new-sandbox.sh: $tool is not on PATH" >&2; exit 1; }
  ln -sf "$path" "$toolbin/$tool"
done
if path="$(command -v jq)"; then ln -sf "$path" "$toolbin/jq"; fi

# Share the caller's uv cache, so each sandbox does not download byor's dependencies again.
uv_cache="$(uv cache dir)"

mkdir -p "$root/home/.config" "$root/home/.local/state" "$root/work"
printf '[user]\n\tname = Sandbox User\n\temail = sandbox@example.com\n[init]\n\tdefaultBranch = main\n' \
  > "$root/gitconfig"

cat > "$root/env.sh" <<ENV
export HOME="$root/home"
export XDG_CONFIG_HOME="$root/home/.config"
export XDG_STATE_HOME="$root/home/.local/state"
export XDG_CACHE_HOME="$root/home/.cache"
export UV_CACHE_DIR="$uv_cache"
export GIT_CONFIG_GLOBAL="$root/gitconfig"
export GIT_CONFIG_NOSYSTEM=1
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR
export PATH="$stress_root/bin:$toolbin:/usr/bin:/bin:/usr/sbin:/sbin"
export BYOR_LAST="$stress_root/bin-last/byor"
export SANDBOX="$root"
cd "$root/work"
ENV

echo "$root/env.sh"
