#!/bin/sh
# A byor `command_check`: the pre-command hook pipes the shell command an
# agent is about to run to stdin; a nonzero exit denies it and this output
# becomes the agent's correction. Command checks run on every command an
# agent issues, so keep them fast — no network, no heavy interpreter startup.
#
# Why a script and not a command rule: the match depends on runtime state
# ($HOME expansion), which a static ast-grep pattern cannot see.

command=$(cat)
case "$command" in
*"$HOME/.ssh"* | *'~/.ssh'*)
    echo "Do not read or modify ~/.ssh. If the task needs SSH keys or config, stop and ask the user."
    exit 1
    ;;
esac
exit 0
