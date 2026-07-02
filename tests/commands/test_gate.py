"""`byor init --gate`: distribute a byor-free blocking gate to the team.

The gate must run for contributors who never installed byor, so generation vendors everything the
pre-commit config needs into the repo — including home-directory check scripts, whose references get
rewritten and whose name collisions are refused. It also self-heals when checks are added later,
keeps out of an existing pre-commit config, and supports a private variant that installs a local
shim and commits nothing.
"""

from pathlib import Path

import pytest
from support import git, install_package, write_global_check, write_global_rule, write_package_rule

from byor.cli import main
from byor.config import load_repo_config


def gate_repo(home: Path, *, extra: tuple[str, ...] = (), branch: str = "main") -> Path:
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", f"--initial-branch={branch}")
    args = ["init", "--repo", str(repo), "--non-interactive", "--gate", *extra]
    assert main(args) == 0
    return repo


def test_gate_promotes_rules_and_checks_and_writes_portable_artifacts(
    home: Path,
) -> None:
    write_global_rule(home, "python/no-cast.yml", rule_id="no-cast")
    write_global_check("ruff", "ruff-check")
    repo = gate_repo(home)

    config = load_repo_config(repo)
    assert (repo / ".byor" / "rules" / "project" / "python" / "no-cast.yml").is_file()
    assert [check.name for check in config.checks] == ["ruff"]
    assert config.gate is True

    precommit = (repo / ".pre-commit-config.yaml").read_text()
    assert "uvx --from ast-grep-cli ast-grep scan --error" in precommit
    assert "ruff-check" in precommit
    # A check's extensions become a pre-commit files filter (byor-faithful scoping).
    assert r"files: \.(py)$" in precommit

    workflow = (repo / ".github" / "workflows" / "byor-gate.yml").read_text()
    # Push runs are limited to the default branch so PR branches are not gated twice.
    assert "on:\n  pull_request:\n  push:\n    branches: [main]\n" in workflow
    assert "astral-sh/setup-uv@v6" in workflow
    assert "uvx --from ast-grep-cli ast-grep scan --error" in workflow
    assert "npm install" not in workflow
    assert "ruff-check" in workflow


def test_gate_workflow_gates_pushes_to_a_non_main_default_branch(home: Path) -> None:
    repo = gate_repo(home, branch="trunk")

    workflow = (repo / ".github" / "workflows" / "byor-gate.yml").read_text()
    assert "on:\n  pull_request:\n  push:\n    branches: [trunk]\n" in workflow


def test_gate_self_heals_when_a_check_is_added_later(home: Path) -> None:
    write_global_rule(home, "python/no-cast.yml", rule_id="no-cast")
    repo = gate_repo(home)
    assert "later-check" not in (repo / ".pre-commit-config.yaml").read_text()

    write_global_check("later", "later-check")
    assert main(["promote", "--repo", str(repo), "--check", "later"]) == 0
    # Any subsequent repo command regenerates the byor-owned artifacts.
    assert main(["list", "--repo", str(repo)]) == 0

    assert "later-check" in (repo / ".pre-commit-config.yaml").read_text()
    assert "later-check" in (repo / ".github" / "workflows" / "byor-gate.yml").read_text()


def test_gate_vendors_a_home_script_check_into_the_repo(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Redirect `~` into the sandbox on both POSIX (HOME) and Windows (USERPROFILE),
    # since os.path.expanduser reads different vars per platform.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    (home / "fix.sh").write_text("#!/bin/sh\necho hi\n")
    write_global_check("fixer", "~/fix.sh")
    repo = gate_repo(home)

    assert (repo / ".byor" / "scripts" / "fix.sh").is_file()
    run = next(c.run for c in load_repo_config(repo).checks if c.name == "fixer")
    assert run == ".byor/scripts/fix.sh"


def test_gate_rewrites_vendored_home_script_dependencies(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    scripts = home / ".config" / "byor" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "runner.sh").write_text('#!/usr/bin/env zsh\nexec "${HOME}/.config/byor/scripts/helper.py" "$@"\n')
    (scripts / "helper.py").write_text("#!/usr/bin/env python3\nprint('ok')\n")
    write_global_check("runner", "~/.config/byor/scripts/runner.sh")

    repo = gate_repo(home)

    runner = repo / ".byor" / "scripts" / "runner.sh"
    helper = repo / ".byor" / "scripts" / "helper.py"
    assert runner.is_file()
    assert helper.is_file()
    assert runner.read_text() == ('#!/usr/bin/env zsh\nexec ".byor/scripts/helper.py" "$@"\n')


def test_gate_vendors_subdirectory_script_references(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    scripts = home / ".config" / "byor" / "scripts"
    (scripts / "sub").mkdir(parents=True)
    (scripts / "runner.sh").write_text('#!/usr/bin/env zsh\nexec "${HOME}/.config/byor/scripts/sub/tool.py" "$@"\n')
    (scripts / "sub" / "tool.py").write_text("#!/usr/bin/env python3\nprint('ok')\n")
    write_global_check("runner", "~/.config/byor/scripts/runner.sh")

    repo = gate_repo(home)

    runner = repo / ".byor" / "scripts" / "runner.sh"
    assert (repo / ".byor" / "scripts" / "sub" / "tool.py").is_file()
    assert runner.read_text() == ('#!/usr/bin/env zsh\nexec ".byor/scripts/sub/tool.py" "$@"\n')


def test_gate_refuses_two_scripts_vendoring_to_the_same_name(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    (home / "fix.sh").write_text("#!/bin/sh\necho direct\n")
    scripts = home / ".config" / "byor" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "runner.sh").write_text('#!/bin/sh\nexec "${HOME}/.config/byor/scripts/fix.sh"\n')
    (scripts / "fix.sh").write_text("#!/bin/sh\necho nested\n")
    write_global_check("fixer", "~/fix.sh")
    write_global_check("runner", "~/.config/byor/scripts/runner.sh")
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    capsys.readouterr()

    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 1

    assert "rename one of the scripts" in capsys.readouterr().err


def test_gate_promote_keeps_both_package_rules_on_a_filename_collision(home: Path) -> None:
    """Two packages shipping the same filename (distinct IDs) must both be vendored.

    The loser used to be dropped silently, so the committed gate never
    enforced it on CI or fresh clones; the collision now lands under a
    package-prefixed path, and regenerating stays idempotent.
    """
    write_package_rule(home, "pkg-a", relpath="no-cast.yml", rule_id="a-no-cast")
    write_package_rule(home, "pkg-b", relpath="no-cast.yml", rule_id="b-no-cast")
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    install_package(repo, "pkg-a")
    install_package(repo, "pkg-b")

    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 0
    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 0

    project = repo / ".byor" / "rules" / "project"
    vendored = sorted(project.rglob("*.yml"))
    ids = sorted(line for path in vendored for line in path.read_text().splitlines() if line.startswith("id: "))
    assert ids == ["id: a-no-cast", "id: b-no-cast"]
    # Both copies live in the committed project dir, visible to git.
    visible = git(repo, "ls-files", "--others", "--exclude-standard", "--", str(project))
    assert visible.count("no-cast.yml") == 2


def test_private_gate_installs_a_local_shim_and_commits_nothing(home: Path) -> None:
    write_global_rule(home, "python/no-cast.yml", rule_id="no-cast")
    repo = gate_repo(home, extra=("--private",))

    assert (repo / ".git" / "hooks" / "pre-commit").is_file()
    assert not (repo / ".pre-commit-config.yaml").exists()
    assert not (repo / ".github").exists()
    status = git(repo, "status", "--porcelain")
    assert ".byor" not in status
    assert "sgconfig" not in status


def test_gate_does_not_clobber_an_existing_precommit_config(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_global_rule(home, "python/no-cast.yml", rule_id="no-cast")
    repo = home / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / ".pre-commit-config.yaml").write_text("repos: []\n")
    capsys.readouterr()

    assert main(["init", "--repo", str(repo), "--non-interactive", "--gate"]) == 0

    assert (repo / ".pre-commit-config.yaml").read_text() == "repos: []\n"
    assert "already exists" in capsys.readouterr().out
