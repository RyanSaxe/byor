"""Shared helpers for the BYOR test suite, importable via ``pythonpath = ["tests"]``.

These run the real thing: setup helpers assert `main(...)` exit codes, git helpers shell out with an
inline throwaway identity, and writers fabricate rules, checks, and packages inside the sandboxed
home. The editor factories return multi-word $EDITOR commands on purpose, so callers exercise the
shlex.split contract.
"""

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from byor.cli import main
from byor.config import (
    CheckDef,
    load_global_config,
    load_local_config,
    save_global_config,
    save_local_config,
)
from byor.io.paths import global_config_dir

RULE_TEMPLATE = "id: {rule_id}\nlanguage: Python\nmessage: {message}\nrule:\n  pattern: cast($TYPE, $VALUE)\n"
NOOP_EDITOR = shlex.join([sys.executable, "-c", "pass"])


# Every `command` string anywhere in a parsed harness-config JSON tree.
def commands_in(node: object) -> list[str]:
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


# Run git in `repo` with an inline throwaway identity; returns stdout.
def git(repo: Path, *argv: str) -> str:
    git_executable = shutil.which("git")
    if git_executable is None:
        msg = "git executable is required for this test"
        raise RuntimeError(msg)
    result = subprocess.run(
        [git_executable, "-c", "user.name=t", "-c", "user.email=t@t", *argv],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


def commit_file(repo: Path, name: str, *, content: str) -> Path:
    file = repo / name
    file.write_text(content)
    git(repo, "add", name)
    git(repo, "commit", "--quiet", "-m", f"add {name}")
    return file


def make_repo(home: Path, *, name: str = "repo", extra: tuple[str, ...] = ()) -> Path:
    repo = home / name
    repo.mkdir()
    assert main(["init", "--repo", str(repo), "--non-interactive", *extra]) == 0
    return repo


def install_agents(*agents: str) -> None:
    assert main(["install", "--agents", ",".join(agents), "--non-interactive"]) == 0


def repo_with_agents(home: Path, *agents: str) -> Path:
    repo = make_repo(home)
    install_agents(*agents)
    return repo


def global_agents() -> list[str]:
    return load_global_config(global_config_dir()).agents


def write_rule(
    path: Path,
    rule_id: str,
    *,
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


def write_global_rule(home: Path, relpath: str, *, rule_id: str, tags: list[str] | None = None) -> Path:
    path = home / "xdg" / "byor" / "rules" / relpath
    return write_rule(path, rule_id, tags=tags)


def write_package_rule(
    home: Path,
    package: str,
    *,
    relpath: str,
    rule_id: str,
    tags: list[str] | None = None,
) -> Path:
    path = home / "xdg" / "byor" / "packages" / package / relpath
    return write_rule(path, rule_id, tags=tags)


def install_package(repo: Path, name: str) -> None:
    local = load_local_config(repo)
    local.packages.append(name)
    save_local_config(repo, local)


def write_global_check(name: str, run: str, *, extensions: tuple[str, ...] = ("py",)) -> None:
    config_dir = global_config_dir()
    config = load_global_config(config_dir)
    config.checks.append(CheckDef(name=name, extensions=list(extensions), run=run))
    save_global_config(config_dir, config)


def write_package_check(
    home: Path,
    package: str,
    *,
    name: str,
    run: str,
    extensions: tuple[str, ...] = ("py",),
) -> Path:
    path = home / "xdg" / "byor" / "packages" / package / "checks.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"checks:\n  - name: {name}\n    extensions: [{', '.join(extensions)}]\n    run: {run}\n")
    return path


def uninstall_package(repo: Path, name: str) -> None:
    local = load_local_config(repo)
    if name in local.packages:
        local.packages.remove(name)
        save_local_config(repo, local)


# The generated copy of global rules that ast-grep reads in this repo.
def mirror(repo: Path) -> Path:
    return repo / ".byor" / "rules" / "personal" / "global"


# The generated copy of installed-package rules ast-grep reads in this repo.
def package_mirror(repo: Path) -> Path:
    return repo / ".byor" / "rules" / "personal" / "packages"


# An $EDITOR value whose command replaces the edited file with `content`.
# Deliberately multi-word so it exercises the shlex.split contract.
def make_editor(directory: Path, content: str) -> str:
    source = directory / "editor-replacement.yml"
    source.write_text(content)
    copy_into_edited_file = "import shutil, sys; shutil.copyfile(sys.argv[1], sys.argv[2])"
    return shlex.join([sys.executable, "-c", copy_into_edited_file, str(source)])


# An $EDITOR whose command replaces `old` with `new` in the edited file.
def substituting_editor(old: str, new: str) -> str:
    substitute = (
        "import pathlib, sys; path = pathlib.Path(sys.argv[1]); "
        f"path.write_text(path.read_text().replace({old!r}, {new!r}))"
    )
    return shlex.join([sys.executable, "-c", substitute])


# An $EDITOR that exits nonzero without touching the file.
def failing_editor(status: int) -> str:
    script = f"raise SystemExit({status})"
    return shlex.join([sys.executable, "-c", script])
