"""Give a linked git worktree the byor files that its main checkout keeps out of git.

`git worktree add` checks out only tracked files, and byor keeps a repo's personal files untracked:
all of `.byor/` and `sgconfig.yml` under a private init, `.byor/local.yml` and the rule mirrors under
a committed one. Without them a new worktree runs only the global rules, or no rules at all, until
someone runs `byor init` there again.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from byor.config import load_repo_config, local_config_path, repo_config_path
from byor.rules.sync import load_canonical_rules, sync_repo

if TYPE_CHECKING:
    from pathlib import Path

__all__ = (
    "adopt_main_checkout",
    "main_checkout_root",
)

GITDIR_PREFIX = "gitdir: "


def main_checkout_root(worktree_root: Path) -> Path | None:
    """Return the main checkout of a linked worktree, or None for any other directory.

    A linked worktree's `.git` is a file naming the worktree's own git dir, and that dir's
    `commondir` file leads to the repository all of its worktrees share. The main checkout is the
    directory holding that shared `.git`. Reading the two files keeps a git subprocess off every
    hook call. A bare repository has no main checkout to copy from, so it returns None.
    """
    dot_git = worktree_root / ".git"
    if not dot_git.is_file():
        return None
    pointer = dot_git.read_text(encoding="utf-8").strip()
    if not pointer.startswith(GITDIR_PREFIX):
        return None
    git_dir = (worktree_root / pointer.removeprefix(GITDIR_PREFIX)).resolve()
    commondir_file = git_dir / "commondir"
    if not commondir_file.is_file():
        return None
    common_dir = (git_dir / commondir_file.read_text(encoding="utf-8").strip()).resolve()
    if common_dir.name != ".git":
        return None
    return common_dir.parent


def adopt_main_checkout(repo_root: Path, config_dir: Path) -> str | None:
    """Copy the byor files a linked worktree lacks from its main checkout, then sync the mirrors.

    `.byor/local.yml` marks a worktree that already has its byor files, because `byor init` always
    writes it and git never tracks it, so the check costs one stat in every initialized checkout.
    The copy never overwrites a file the worktree has. Returns a summary line when it copied files,
    and raises ByorError or OSError for the caller to report.
    """
    if local_config_path(repo_root).is_file():
        return None
    main_root = main_checkout_root(repo_root)
    if main_root is None or not repo_config_path(main_root).is_file():
        return None
    sources = [path for path in (main_root / ".byor").rglob("*") if path.is_file()]
    sources.append(main_root / load_repo_config(main_root).paths.sgconfig)
    copied = 0
    for source in sources:
        target = repo_root / source.relative_to(main_root)
        if target.exists() or not source.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    if copied == 0:
        return None
    sync_repo(repo_root, load_canonical_rules(config_dir))
    noun = "file" if copied == 1 else "files"
    return f"byor: copied {copied} byor {noun} from the main checkout {main_root}"
