"""Syncing opt-in packages: their rules mirror in only when installed.

The packages mirror holds a subdirectory per installed package, and sync keeps it truthful: rules of
uninstalled or removed packages disappear, and a project rule with the same id overrides the package
copy. Two installed packages claiming one rule id has no right answer, so it is a hard error.
"""

from pathlib import Path

import pytest
from support import (
    install_package,
    make_repo,
    package_mirror,
    uninstall_package,
    write_package_rule,
    write_rule,
)

from byor.cli import main


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


def test_two_packages_with_the_same_rule_id_is_a_hard_error(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_package_rule(home, "pkg-a", relpath="dup.yml", rule_id="dup-id")
    write_package_rule(home, "pkg-b", relpath="dup.yml", rule_id="dup-id")
    repo = make_repo(home)
    install_package(repo, "pkg-a")
    install_package(repo, "pkg-b")
    capsys.readouterr()

    assert main(["sync", "--repo", str(repo)]) != 0

    assert "Duplicate rule IDs within installed package rules" in capsys.readouterr().err
