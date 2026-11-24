# Simulated users

Each section is one simulated user. The heading is its `id`, and the paragraph
is its `scenario`. Pass them to `workflow.js` as `args.personas`, an array of
`{ id, scenario }`. Add a persona when a release adds a kind of user these miss,
and keep each one about a stable way of using byor rather than about one
release.

## newuser

You are a new byor user on a fresh computer. Follow the README as a newcomer
would: `byor install` for a couple of agents, `byor init` in new repos (private
and shared), `byor add` rules at every scope, simulated agent edits and commands
through the hook payloads, `byor list`, `byor exclude` and `byor include`,
profiles, `byor package add` and `remove`, `byor disable` and `enable`, and
`byor doctor` after each step. Look for anything confusing, wrong or broken in
the first hour of use.

## upgrader

You upgrade from the last release. In one sandbox, use `$BYOR_LAST` to build a
realistic setup: install agents (claude-code, codex, skill, opencode), init
several repos (private and committed), add global and project rules, create a
package and add it to a repo, add a profile and init defaults to `config.yml`,
disable one repo and one directory prefix, and set a custom `paths.repos`.
Record checksums of every file byor wrote. Then switch to the candidate and run
ordinary commands and hooks. Confirm nothing was lost (agents, disabled paths,
registry, packages, profiles, init defaults), that any migration the docs
describe happens once and leaves the user's files as the docs promise, and that
following doctor's advice leaves a clean doctor. Also go back to `$BYOR_LAST`
and forward again, and run the candidate against a malformed or partial old
config.

## dotfiles

Your byor settings come from a dotfiles repo. Make a separate git repo in the
sandbox holding `config.yml` (init defaults, profiles, output settings, checks)
and `rules/`, and symlink them into `~/.config/byor`, including a read-only
target (`chmod a-w`) to mimic a Nix store link. Then run every kind of byor
command and hook: install, hook install and uninstall, init with and without
defaults, disable and enable, sync, `sync --all`, profile add, package add and
remove, adding and removing rules, doctor. byor must never write, replace or
delete a file the user owns or turn a symlink into a regular file, and it must
honor the settings. Also try with no `config.yml` at all, and with an empty one.

## worktrees

You live in git worktrees. For both a private init and a committed init, create
many worktrees and use byor in each through hooks and commands: a worktree made
from inside another worktree, worktrees with relative paths
(`git -c worktree.useRelativePaths=true`), a bare repo with worktrees, detached
HEAD worktrees, worktrees in paths with spaces, `git worktree move` and `prune`,
a deleted main checkout, a worktree whose own `.byor/local.yml` you edited (its
exclusions must survive), a worktree of a disabled repo, edits in deep
subdirectories, and several hook calls fired at once in a brand-new worktree
(`&` and `wait`). Check that rules apply, nothing tracked changes, no byor file
lands in `git status`, and nothing is overwritten.

## packages

You author byor packages. Create packages with file rules, `checks.yml` checks
and command rules under `commands/`, in `~/.config/byor/packages`. Add them to
repos, then rename a package directory, delete one, edit its rules, add two
packages with the same rule ID, and remove packages, including a deleted one, a
name the repo never installed, and with `--repo` from outside the repo. Check
sync and `sync --all`, doctor's messages across several repos, `init --packages`
and init-default packages, profiles that exclude package rules, and that package
rule copies appear and disappear correctly.

## agents

You manage agent integrations. Install every agent byor supports (claude-code,
codex, copilot, opencode, pi, skill) into the sandbox HOME with `byor install`
and `byor hook install`, then uninstall some. Tamper as a real user would:
delete byor's hook entries from `~/.claude/settings.json` by hand, mix your own
command into byor's hook entry, edit or delete the file where byor records
installed agents, delete a plugin file, make `settings.json` malformed or not
UTF-8, and leave an out-of-date byor hook entry. After each change, run doctor
and an ordinary self-healing command, and check the result against what
`docs/ai-agents.md` says. Watch for duplicated agents, lost user hook entries
and crashes.

## ignoreblocks

You have repos initialized by older byor. Use `$BYOR_LAST` to init committed and
private repos, then hand-craft ignore files that mimic even older versions (a
managed block missing newer patterns), plus variants: user lines above and below
the block, a begin marker with no end marker, two blocks, CRLF line endings, a
`.gitignore` without a trailing newline, a read-only `.gitignore`, a non-UTF-8
byte, and a private repo whose `.git/info/exclude` holds the block. Run doctor,
ordinary commands, `byor sync` and hooks. Check which files byor rewrites, that
user lines are never lost or reordered, that damaged blocks are reported but
left alone, and that `git status` never lists byor's personal files.

## stateenv

You stress byor's machine-level files and environment handling:
`XDG_CONFIG_HOME` and `XDG_STATE_HOME` unset, relative, unwritable, or with
spaces or non-ASCII characters; byor's record files empty, malformed, of the
wrong version, missing keys, or a directory instead of a file; a custom
`paths.repos`; a symlinked state folder; and many byor commands that write
records run at once (several `byor hook install` calls, `disable` racing
`enable`) to see whether any write is lost. Check that error messages are clear
and that hooks fail open instead of blocking an agent.

## hooksim

You simulate realistic agent sessions. For the claude-code, codex (apply_patch)
and copilot payload formats, send long sequences of post-edit and pre-command
payloads across a normal byor repo, a fresh worktree, a disabled repo, a non-git
directory, a git repo byor never initialized, nested repos, files outside any
repo, relative and absolute paths, and paths with spaces. Check exit codes, what
lands on stdout and stderr (agents read both), that hooks fail open on errors,
that nothing byor prints corrupts the JSON envelopes codex and copilot expect on
stdout, and how long a hook call takes in an ordinary repo compared with a fresh
worktree.

## teamgate

You use byor on a team repo. Create a committed init with `--gate`, check the
generated pre-commit config and CI workflow, promote rules, simulate a
teammate's clone without byor installed, and check `byor sync --check` exit
codes. Then look at how this release's changes touch tracked files: does any
byor command leave unexpected diffs in tracked files, and do worktrees, package
changes and ignore-file upkeep behave in a gated repo?

## registry

You have many repos. Init a dozen, some private, some committed, some nested
inside others. Run `byor sync --all` and `--all --prune` after moving and
deleting some, disable a directory prefix that covers several repos and enable
one inside it, init inside a disabled directory, check that worktrees never get
registered, run doctor from inside and outside repos, and remove packages with
`--repo`. Check that the registry file the docs name stays consistent and never
lists a worktree or, after a prune, a deleted path.

## docs

You follow the docs literally. Read `README.md`, every file in `docs/`, the
files in `examples/` and the bundled skill under `src/byor/data/skill` in the
candidate checkout. Run every command and example they show, in a sandbox, in
the order a reader would. Report each place where docs and behavior disagree,
any example that fails, any file path or message the docs name that byor no
longer uses, and help text from `byor --help` and each subcommand's `--help`
that contradicts the docs.
