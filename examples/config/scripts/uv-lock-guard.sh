#!/bin/sh
# A byor `check` script that polices the agent, not the code: a hand-edited
# uv.lock is silently wrong. The post-edit hook fires only on the agent's own
# file edits, so `uv add` or `uv sync` run in a terminal never trips this.
# Configure it with `gate: false`; a lockfile that reaches pre-commit or CI
# was written by uv.
for file in "$@"; do
  case "$(basename "$file")" in
  uv.lock)
    echo "Do not edit uv.lock by hand. Revert your edit and run uv add, uv remove, or uv sync so uv rewrites the lockfile itself."
    exit 1
    ;;
  esac
done
exit 0
