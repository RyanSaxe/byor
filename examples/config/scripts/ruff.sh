#!/usr/bin/env zsh
# A byor `check` script (wired in config.yml). The pattern it demonstrates:
# autofix what is safe, tell the agent exactly what changed, then report only
# the irreducible remainder — so the agent acts on real problems, not noise.
# byor appends the in-scope files as arguments; with none, there is nothing to do.
[[ $# -eq 0 ]] && exit 0

# The agent reads this output; keep it plain text, never ANSI color.
export NO_COLOR=1
unset FORCE_COLOR CLICOLOR_FORCE

# Apply lint fixes, capturing a summary of what was fixed (--show-fixes).
fixed=$(uvx ruff check --fix-only --show-fixes "$@" 2>/dev/null)

# Apply formatting; ruff reports "reformatted" only when it rewrote something.
format_out=$(uvx ruff format "$@" 2>&1)
reformatted=""
[[ "$format_out" == *reformatted* ]] && reformatted="ruff format reformatted the file(s)."

# What ruff could not fix — the only thing the agent must act on. --quiet prints
# the concise violations and nothing else (no "All checks passed!" when clean).
remaining=$(uvx ruff check --quiet --output-format concise "$@" 2>/dev/null)

report=""
[[ -n "$fixed" ]] && report+="Autofixed by ruff (no action needed):"$'\n'"$fixed"$'\n'
[[ -n "$reformatted" ]] && report+="$reformatted"$'\n'
[[ -n "$remaining" ]] && report+="Remaining ruff issues to fix:"$'\n'"$remaining"$'\n'

# Stay silent only when the file was already clean; otherwise surface the report
# (a nonzero exit is what byor feeds back) so the agent knows what changed.
[[ -z "$report" ]] && exit 0
print -rn -- "$report"
exit 2
