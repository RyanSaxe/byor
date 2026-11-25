#!/bin/sh
# A read-only byor `check` script: forwards ruff lint and format diagnostics to
# the agent without modifying files on disk underneath an active edit session.
# byor appends in-scope files as arguments and runs from the repo root; no
# arguments means scan the whole repo, respecting ignored files.
export NO_COLOR=1 # the agent reads this output; keep it plain text
unset FORCE_COLOR CLICOLOR_FORCE

if [ "$#" -eq 0 ]; then
  # Rebuild "$@" one line at a time so spaces and glob characters survive;
  # newlines in filenames are out of scope for this teaching example.
  while IFS= read -r file; do
    [ -n "$file" ] && set -- "$@" "$file"
  done <<EOF
$(git ls-files -co --exclude-standard -- '*.py' '*.pyi')
EOF
  [ "$#" -eq 0 ] && exit 0
fi

lint=$(uvx ruff check --quiet --output-format concise "$@" 2>/dev/null)
lint_status=$?
format_needed=""
if ! uvx ruff format --check --quiet "$@" >/dev/null 2>&1; then
  format_needed="Files need formatting (run \`uvx ruff format\`)."
fi

if [ "$lint_status" -eq 0 ] && [ -z "$format_needed" ]; then
  exit 0
fi
[ -n "$lint" ] && printf '%s\n' "$lint"
[ -n "$format_needed" ] && printf '%s\n' "$format_needed"
exit 1
