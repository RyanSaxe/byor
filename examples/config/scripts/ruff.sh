#!/bin/sh
# A byor `check` script: autofix what is safe, tell the agent exactly what
# changed, then report only the irreducible remainder. byor appends in-scope
# files as arguments and runs from the repo root; no arguments means scan the
# whole repo, respecting ignored files.
export NO_COLOR=1 # the agent reads this output; keep it plain text
unset FORCE_COLOR CLICOLOR_FORCE

if [ "$#" -eq 0 ]; then
  set -- $(git ls-files -co --exclude-standard -- '*.py' '*.pyi')
  [ "$#" -eq 0 ] && exit 0
fi

fixed=$(uvx ruff check --fix-only --show-fixes "$@" 2>/dev/null) # apply + summarize
reformatted=""
case "$(uvx ruff format "$@" 2>&1)" in
*reformatted*) reformatted="ruff format reformatted the file(s)." ;;
esac
remaining=$(uvx ruff check --quiet --output-format concise "$@" 2>/dev/null)

[ -z "$fixed$reformatted$remaining" ] && exit 0 # already clean: stay silent
[ -n "$fixed" ] && printf 'Autofixed by ruff (no action needed):\n%s\n' "$fixed"
[ -n "$reformatted" ] && printf '%s\n' "$reformatted"
[ -n "$remaining" ] && printf 'Remaining ruff issues to fix:\n%s\n' "$remaining"
exit 2
