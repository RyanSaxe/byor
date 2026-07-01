"""Syncing opt-in packages: their rules mirror in only when installed."""

from pathlib import Path

import pytest
from support import (
    install_package,
    make_repo,
    package_mirror,
    write_package_rule,
    write_rule,
)

from byor.cli import main


def test_installed_package_rule_is_mirrored_under_its_package_name(home: Path) -> None:
    write_package_rule(home, "python-strict", "no-cast.yml", "pkg-no-cast")
    repo = make_repo(home)
    install_package(repo, "python-strict")

    assert main(["sync", "--repo", str(repo)]) == 0

    copy = package_mirror(repo) / "python-strict" / "no-cast.yml"
    assert copy.is_file()
    assert "pkg-no-cast" in copy.read_text()


def test_uninstalled_package_rule_is_not_mirrored(home: Path) -> None:
    write_package_rule(home, "python-strict", "no-cast.yml", "pkg-no-cast")
    repo = make_repo(home)

    assert main(["sync", "--repo", str(repo)]) == 0

    assert not (package_mirror(repo) / "python-strict").exists()


def test_removing_a_package_unmirrors_its_rules(home: Path) -> None:
    write_package_rule(home, "python-strict", "no-cast.yml", "pkg-no-cast")
    repo = make_repo(home)
    install_package(repo, "python-strict")
    assert main(["sync", "--repo", str(repo)]) == 0

    from byor.config import load_local_config, save_local_config

    local = load_local_config(repo)
    local.packages.clear()
    save_local_config(repo, local)
    assert main(["sync", "--repo", str(repo)]) == 0

    assert not (package_mirror(repo) / "python-strict" / "no-cast.yml").exists()


def test_project_rule_overrides_a_package_rule_of_the_same_id(home: Path) -> None:
    write_package_rule(home, "python-strict", "no-cast.yml", "shared-id")
    repo = make_repo(home)
    write_rule(repo / ".byor" / "rules" / "project" / "shared-id.yml", "shared-id")
    install_package(repo, "python-strict")

    assert main(["sync", "--repo", str(repo)]) == 0

    assert not (package_mirror(repo) / "python-strict" / "no-cast.yml").exists()


def test_two_packages_with_the_same_rule_id_is_a_hard_error(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_package_rule(home, "pkg-a", "dup.yml", "dup-id")
    write_package_rule(home, "pkg-b", "dup.yml", "dup-id")
    repo = make_repo(home)
    install_package(repo, "pkg-a")
    install_package(repo, "pkg-b")
    capsys.readouterr()

    assert main(["sync", "--repo", str(repo)]) != 0

    assert (
        "Duplicate rule IDs within installed package rules" in capsys.readouterr().err
    )
