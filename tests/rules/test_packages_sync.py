"""Syncing opt-in packages: their rules mirror in only when installed.

The packages mirror holds a subdirectory per installed package, and sync keeps it truthful: rules of
uninstalled or removed packages disappear, a project rule with the same id overrides the package
copy, and a package rule overrides the global copy. Two installed packages claiming one rule id has
no right answer, so it is a hard error.
"""

from pathlib import Path

import pytest
from support import (
    install_package,
    make_repo,
    mirror,
    package_command_mirror,
    package_mirror,
    uninstall_package,
    write_global_rule,
    write_package_command_rule,
    write_package_rule,
    write_rule,
)

from byor.cli import main


def test_package_command_rules_mirror_into_the_commands_tree_only(home: Path) -> None:
    write_package_rule(home, "style", relpath="no-cast.yml", rule_id="pkg-no-cast")
    write_package_command_rule(home, "style", relpath="no-pip.yml", rule_id="pkg-no-pip")
    repo = make_repo(home)
    install_package(repo, "style")

    assert main(["sync", "--repo", str(repo)]) == 0

    assert (package_command_mirror(repo) / "style" / "no-pip.yml").is_file()
    # The commands/ subtree is the package's command universe: it must not
    # leak into the file-rule mirror that sgconfig points ast-grep at.
    assert (package_mirror(repo) / "style" / "no-cast.yml").is_file()
    assert not (package_mirror(repo) / "style" / "commands").exists()


def test_installed_package_rule_is_mirrored_under_its_package_name(home: Path) -> None:
    write_package_rule(home, "python-strict", relpath="no-cast.yml", rule_id="pkg-no-cast")
    repo = make_repo(home)
    install_package(repo, "python-strict")

    assert main(["sync", "--repo", str(repo)]) == 0

    copy = package_mirror(repo) / "python-strict" / "no-cast.yml"
    assert copy.is_file()
    assert "pkg-no-cast" in copy.read_text()


def test_uninstalled_package_rule_is_not_mirrored(home: Path) -> None:
    write_package_rule(home, "python-strict", relpath="no-cast.yml", rule_id="pkg-no-cast")
    repo = make_repo(home)

    assert main(["sync", "--repo", str(repo)]) == 0

    assert not (package_mirror(repo) / "python-strict").exists()


def test_removing_a_package_unmirrors_its_rules(home: Path) -> None:
    write_package_rule(home, "python-strict", relpath="no-cast.yml", rule_id="pkg-no-cast")
    repo = make_repo(home)
    install_package(repo, "python-strict")
    assert main(["sync", "--repo", str(repo)]) == 0

    uninstall_package(repo, "python-strict")
    assert main(["sync", "--repo", str(repo)]) == 0

    assert not (package_mirror(repo) / "python-strict" / "no-cast.yml").exists()


def test_project_rule_overrides_a_package_rule_of_the_same_id(home: Path) -> None:
    write_package_rule(home, "python-strict", relpath="no-cast.yml", rule_id="shared-id")
    repo = make_repo(home)
    write_rule(repo / ".byor" / "rules" / "project" / "shared-id.yml", "shared-id")
    install_package(repo, "python-strict")

    assert main(["sync", "--repo", str(repo)]) == 0

    assert not (package_mirror(repo) / "python-strict" / "no-cast.yml").exists()


# Opting into a package is an easy avenue to override your global setup.
def test_package_rule_overrides_a_global_rule_of_the_same_id(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_global_rule(home, "shared-id.yml", rule_id="shared-id")
    write_package_rule(home, "python-strict", relpath="strict.yml", rule_id="shared-id")
    repo = make_repo(home)
    install_package(repo, "python-strict")
    capsys.readouterr()

    assert main(["sync", "--repo", str(repo)]) == 0

    assert (package_mirror(repo) / "python-strict" / "strict.yml").is_file()
    assert not (mirror(repo) / "shared-id.yml").exists()
    assert "  shared-id: overridden by package rule" in capsys.readouterr().out


def test_excluding_the_package_rule_leaves_neither_copy(home: Path) -> None:
    write_global_rule(home, "shared-id.yml", rule_id="shared-id")
    write_package_rule(home, "python-strict", relpath="strict.yml", rule_id="shared-id")
    repo = make_repo(home)
    install_package(repo, "python-strict")

    assert main(["exclude", "shared-id", "--repo", str(repo)]) == 0

    assert not (package_mirror(repo) / "python-strict" / "strict.yml").exists()
    assert not (mirror(repo) / "shared-id.yml").exists()


# `packages: ["../EVILSRC"]` used to write rules above the packages mirror.
def test_traversal_package_name_fails_sync_without_touching_the_repo(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_package_rule(home, "real-pkg", relpath="ok.yml", rule_id="ok-rule")
    write_rule(home / "xdg" / "byor" / "EVILSRC" / "evil.yml", "evil-rule")
    repo = make_repo(home)
    (repo / ".byor" / "local.yml").write_text('version: 1\npackages:\n  - "../EVILSRC"\n')
    capsys.readouterr()

    assert main(["sync", "--repo", str(repo)]) == 1

    assert "bare directory name" in capsys.readouterr().err
    assert not (repo / ".byor" / "rules" / "personal" / "EVILSRC").exists()
    assert not list((repo / ".byor" / "rules" / "personal" / "packages").rglob("*.yml"))


def test_two_packages_with_the_same_rule_id_is_a_hard_error(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_package_rule(home, "pkg-a", relpath="dup.yml", rule_id="dup-id")
    write_package_rule(home, "pkg-b", relpath="dup.yml", rule_id="dup-id")
    repo = make_repo(home)
    install_package(repo, "pkg-a")
    install_package(repo, "pkg-b")
    capsys.readouterr()

    assert main(["sync", "--repo", str(repo)]) != 0

    assert "Duplicate rule IDs within installed package rules" in capsys.readouterr().err
