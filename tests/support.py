"""Shared helpers for the test suite (importable via ``pythonpath = ["tests"]``)."""

import shlex
import subprocess
import sys
from pathlib import Path

from byor.cli import main

RULE_TEMPLATE = (
    "id: {rule_id}\n"
    "language: Python\n"
    "message: {message}\n"
    "rule:\n"
    "  pattern: cast($TYPE, $VALUE)\n"
)
NOOP_EDITOR = shlex.join([sys.executable, "-c", "pass"])


def commands_in(node: object) -> list[str]:
    """Every `command` string anywhere in a parsed harness-config JSON tree."""
    if isinstance(node, dict):
        found: list[str] = []
        for key, value in node.items():
            if key == "command" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(commands_in(value))
        return found
    if isinstance(node, list):
        return [command for item in node for command in commands_in(item)]
    return []


def git(repo: Path, *argv: str) -> str:
    """Run git in `repo` with an inline throwaway identity; returns stdout."""
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *argv],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def commit_file(repo: Path, name: str, content: str) -> Path:
    file = repo / name
    file.write_text(content)
    git(repo, "add", name)
    git(repo, "commit", "--quiet", "-m", f"add {name}")
    return file


def make_repo(home: Path, name: str = "repo", *extra: str) -> Path:
    repo = home / name
    repo.mkdir()
    assert main(["init", "--repo", str(repo), "--non-interactive", *extra]) == 0
    return repo


def install_agents(home: Path, *agents: str) -> None:
    """Register AI agents globally (the `byor install` step) for test setup."""
    assert main(["install", "--agents", ",".join(agents), "--non-interactive"]) == 0


def repo_with_agents(home: Path, *agents: str) -> Path:
    """An init'd repo plus the named agents registered globally."""
    repo = make_repo(home)
    install_agents(home, *agents)
    return repo


def global_agents() -> list[str]:
    """The AI agents recorded in the global config (XDG-isolated in tests)."""
    from byor.config import load_global_config
    from byor.io.paths import global_config_dir

    return load_global_config(global_config_dir()).agents


def write_rule(
    path: Path,
    rule_id: str,
    message: str = "Avoid this.",
    tags: list[str] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = RULE_TEMPLATE.format(rule_id=rule_id, message=message)
    if tags:
        content += "metadata:\n  byor:\n    tags:\n"
        content += "".join(f"      - {tag}\n" for tag in tags)
    path.write_text(content)
    return path


def write_global_rule(
    home: Path, relpath: str, rule_id: str, tags: list[str] | None = None
) -> Path:
    path = home / "xdg" / "byor" / "rules" / relpath
    return write_rule(path, rule_id, tags=tags)


def write_package_rule(
    home: Path,
    package: str,
    relpath: str,
    rule_id: str,
    tags: list[str] | None = None,
) -> Path:
    path = home / "xdg" / "byor" / "packages" / package / relpath
    return write_rule(path, rule_id, tags=tags)


def install_package(repo: Path, name: str) -> None:
    """Opt `repo` into a global package by recording it in local config."""
    from byor.config import load_local_config, save_local_config

    local = load_local_config(repo)
    local.packages.append(name)
    save_local_config(repo, local)


def mirror(repo: Path) -> Path:
    """The generated copy of global rules that ast-grep reads in this repo."""
    return repo / ".byor" / "rules" / "personal" / "global"


def package_mirror(repo: Path) -> Path:
    """The generated copy of installed-package rules ast-grep reads in this repo."""
    return repo / ".byor" / "rules" / "personal" / "packages"


def make_editor(directory: Path, content: str) -> str:
    """An $EDITOR value whose command replaces the edited file with `content`.

    Deliberately multi-word so it exercises the shlex.split contract.
    """
    source = directory / "editor-replacement.yml"
    source.write_text(content)
    copy_into_edited_file = (
        "import shutil, sys; shutil.copyfile(sys.argv[1], sys.argv[2])"
    )
    return shlex.join([sys.executable, "-c", copy_into_edited_file, str(source)])


def substituting_editor(old: str, new: str) -> str:
    """An $EDITOR whose command replaces `old` with `new` in the edited file."""
    substitute = (
        "import pathlib, sys; path = pathlib.Path(sys.argv[1]); "
        f"path.write_text(path.read_text().replace({old!r}, {new!r}))"
    )
    return shlex.join([sys.executable, "-c", substitute])


def failing_editor(status: int) -> str:
    """An $EDITOR that exits nonzero without touching the file."""
    script = f"raise SystemExit({status})"
    return shlex.join([sys.executable, "-c", script])
