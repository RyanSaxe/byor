"""Give a linked git worktree the byor files that its main checkout keeps out of git.

`git worktree add` checks out only tracked files, and byor keeps a repo's personal files untracked:
all of `.byor/` and `sgconfig.yml` under a private init, `.byor/local.yml` and the rule mirrors under
a committed one. Without them a new worktree runs only the global rules, or no rules at all, until
someone runs `byor init` there again. Only files git ignores in both checkouts are copied, so a copy
can never be committed and never brings back a tracked rule the worktree's branch deleted. With
`worktrees.copy_ignored` set in config.yml, every such ignored file comes along, not only byor's.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from byor.config import disabled_entry, load_global_config, load_repo_config, local_config_path, repo_config_path
from byor.errors import WorktreeCopyBusyError
from byor.io.gitio import git_stdout
from byor.io.lock import file_lock
from byor.io.paths import global_config_dir

if TYPE_CHECKING:
    from byor.config import MachineState

__all__ = (
    "PendingCopy",
    "copy_from_main_checkout",
    "linked_worktrees",
    "main_checkout_root",
    "pending_copy",
    "worktree_disabled_entry",
)

GITDIR_PREFIX = "gitdir: "
LOCK_NAME = "byor-copy.lock"
PARTIAL_MARK = ".byor-copy-"
WORKTREE_PREFIX = "worktree "
LOCK_WAIT_SECONDS = 5.0
# A copy takes milliseconds, so a lock this old was left by a process that died mid-copy.
STALE_LOCK_SECONDS = 30.0


@dataclass(frozen=True)
class PendingCopy:
    """The byor files a linked worktree still needs from its main checkout.

    `relpaths` are relative to both checkouts, in copy order: `.byor/local.yml` comes last because
    its presence is what tells every later command and hook that the copy finished, and
    `.byor/config.yml` just before it, so a copy that fails partway never leaves a private worktree
    looking initialized without its sgconfig.yml. Doctor and `byor sync --check` read this to
    report a worktree that has not copied yet.
    """

    main_root: Path
    relpaths: list[str]


def main_checkout_root(worktree_root: Path) -> Path | None:
    """Return the main checkout of a linked worktree, or None for any other directory.

    A linked worktree's `.git` is a file naming the worktree's own git dir, and that dir's
    `commondir` file leads to the repository all of its worktrees share. The main checkout is the
    directory holding that shared `.git`. Reading the two files keeps a git subprocess off every
    hook call. A bare repository has no main checkout to copy from, so it returns None.
    """
    git_dir = _linked_git_dir(worktree_root)
    if git_dir is None:
        return None
    commondir_file = git_dir / "commondir"
    if not commondir_file.is_file():
        return None
    common_dir = (git_dir / commondir_file.read_text(encoding="utf-8").strip()).resolve()
    if common_dir.name != ".git":
        return None
    return common_dir.parent


def linked_worktrees(main_root: Path) -> list[Path]:
    """Return the existing linked worktrees of a main checkout, as `git worktree list` names them.

    Worktrees stay out of the registry, so a removed one never becomes a dead entry; the fan-outs
    that keep registered repos current reach a registered main checkout's worktrees through this
    instead. The main checkout itself, and worktrees whose folders are gone, are left out.
    """
    output = git_stdout(main_root, "worktree", "list", "--porcelain")
    paths = [
        Path(line.removeprefix(WORKTREE_PREFIX))
        for line in (output or "").splitlines()
        if line.startswith(WORKTREE_PREFIX)
    ]
    resolved_main = main_root.resolve()
    return [path for path in paths if path.is_dir() and path.resolve() != resolved_main]


def worktree_disabled_entry(repo_root: Path, state: MachineState) -> Path | None:
    """Return the `disabled_repos` entry covering a checkout or, for a worktree, its main checkout.

    `byor disable` names a path, but a worktree of a disabled repo lives elsewhere; checking the
    main checkout too keeps the copy, doctor and `sync --check` from treating such a worktree as
    one byor still manages.
    """
    if not state.disabled_repos:
        return None
    entry = disabled_entry(repo_root, state)
    if entry is not None:
        return entry
    main_root = main_checkout_root(repo_root)
    return None if main_root is None else disabled_entry(main_root, state)


def pending_copy(repo_root: Path) -> PendingCopy | None:
    """Return the byor files a linked worktree still needs from its main checkout, or None.

    `.byor/local.yml` marks a finished copy, because `byor init` always writes it, git never tracks
    it, and the copy writes it last; checking it costs one stat in every initialized checkout. Past
    that, a file qualifies only when git ignores it in the main checkout and would ignore it in the
    worktree too. A worktree whose branch predates byor's ignore block therefore gets nothing, since
    a copy there could be committed. With `worktrees.copy_ignored` set in config.yml, every ignored
    file in the main checkout qualifies, not only byor's, under the same two-sided rule. Raises
    ConfigError for a malformed main `.byor/config.yml` or config.yml.
    """
    if local_config_path(repo_root).is_file():
        return None
    main_root = main_checkout_root(repo_root)
    if main_root is None or not repo_config_path(main_root).is_file():
        return None
    sgconfig = load_repo_config(main_root).paths.sgconfig
    copy_ignored = load_global_config(global_config_dir()).worktrees_copy_ignored
    pathspec = () if copy_ignored else ("--", ".byor", sgconfig)
    ignored_in_main = _nul_split(
        git_stdout(main_root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z", *pathspec)
    )
    # git lists a nested repository as one `dir/` entry, which is not a file to copy, and a
    # symlink as itself; lexists keeps a copied dangling symlink from counting as missing.
    missing = [
        relpath for relpath in ignored_in_main if not relpath.endswith("/") and not os.path.lexists(repo_root / relpath)
    ]
    if not missing:
        return None
    ignored_here = set(
        _nul_split(git_stdout(repo_root, "check-ignore", "--stdin", "-z", stdin="\0".join(missing) + "\0"))
    )
    marker = local_config_path(repo_root).relative_to(repo_root).as_posix()
    config = repo_config_path(repo_root).relative_to(repo_root).as_posix()
    relpaths = sorted(
        (relpath for relpath in missing if relpath in ignored_here),
        key=lambda relpath: (relpath == marker, relpath == config),
    )
    if not relpaths:
        return None
    return PendingCopy(main_root=main_root, relpaths=relpaths)


def copy_from_main_checkout(repo_root: Path) -> PendingCopy | None:
    """Copy what `pending_copy` names into the worktree, under a lock; return what was copied.

    Hooks fire in parallel, so the copy holds a lock file in the worktree's own git dir, writes each
    file whole through a rename, and writes the local.yml marker last. A process that finds the lock
    held waits for the copy to finish and then finds nothing pending. If the lock is still held
    after a few seconds it raises WorktreeCopyBusyError, because the copy is still pending and a
    command that went on would see the worktree half-copied. A copy that fails partway leaves no
    marker, so the next command or hook retries it, and that retry first deletes the partial files
    an interrupted copy left, which git would otherwise show.
    """
    git_dir = _linked_git_dir(repo_root)
    if git_dir is None or pending_copy(repo_root) is None:
        return None
    with file_lock(git_dir / LOCK_NAME, wait_seconds=LOCK_WAIT_SECONDS, stale_seconds=STALE_LOCK_SECONDS) as held:
        if not held:
            msg = (
                f"another byor process is copying byor's files into {repo_root} from its main checkout; "
                "run the command again in a moment"
            )
            raise WorktreeCopyBusyError(msg)
        _remove_partials(repo_root)
        pending = pending_copy(repo_root)
        if pending is None:
            return None
        for relpath in pending.relpaths:
            _copy_whole(pending.main_root / relpath, repo_root / relpath)
    return pending


def _linked_git_dir(worktree_root: Path) -> Path | None:
    dot_git = worktree_root / ".git"
    if not dot_git.is_file():
        return None
    pointer = dot_git.read_text(encoding="utf-8").strip()
    if not pointer.startswith(GITDIR_PREFIX):
        return None
    return (worktree_root / pointer.removeprefix(GITDIR_PREFIX)).resolve()


def _nul_split(output: str | None) -> list[str]:
    return [entry for entry in (output or "").split("\0") if entry]


def _copy_whole(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}{PARTIAL_MARK}{os.getpid()}")
    try:
        # A symlink, such as a linked folder of shared rules, is recreated as the same link: copying
        # its content would fail for a directory or a dangling target.
        if source.is_symlink():
            partial.symlink_to(source.readlink())
        else:
            shutil.copy2(source, partial)
        partial.replace(target)
    finally:
        if os.path.lexists(partial):
            partial.unlink()


# The copy writes partial files only under .byor/ and, for sgconfig.yml, at the worktree root.
def _remove_partials(repo_root: Path) -> None:
    candidates = [*repo_root.glob(f".*{PARTIAL_MARK}*"), *(repo_root / ".byor").rglob(f".*{PARTIAL_MARK}*")]
    for partial in candidates:
        if partial.is_file() or partial.is_symlink():
            partial.unlink()
