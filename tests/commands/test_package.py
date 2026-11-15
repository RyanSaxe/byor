"""The `byor package` command: list available packages and install one.

Packages are opt-in bundles that live in the global config dir but apply only where installed. `add`
must record the opt-in in local config, sync the package rules into the mirror, and make the package
checks effective — idempotently, with unknown names rejected alongside the list of available
packages.
"""

from pathlib import Path

import pytest
from support import (
    install_package,
    make_repo,
    package_mirror,
    write_package_check,
    write_package_rule,
)

from byor.cli import main
from byor.config import load_local_config
from byor.io.paths import global_config_dir
from byor.scan.checks import load_effective_checks


def test_list_shows_available_package_names(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_package_rule(home, "python-strict", relpath="no-cast.yml", rule_id="pkg-no-cast")
    write_package_rule(home, "web", relpath="no-x.yml", rule_id="web-no-x")
    make_repo(home)
    capsys.readouterr()

    assert main(["package", "list"]) == 0

    out = capsys.readouterr().out
    assert "python-strict" in out
    assert "web" in out


def test_list_reports_when_no_packages_exist(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_repo(home)
    capsys.readouterr()

    assert main(["package", "list"]) == 0

    assert "No packages available." in capsys.readouterr().out


def test_add_records_the_opt_in_and_syncs_the_rules(home: Path) -> None:
    write_package_rule(home, "python-strict", relpath="no-cast.yml", rule_id="pkg-no-cast")
    repo = make_repo(home)

    assert main(["package", "add", "python-strict", "--repo", str(repo)]) == 0

    assert load_local_config(repo).packages == ["python-strict"]
    assert (package_mirror(repo) / "python-strict" / "no-cast.yml").is_file()


def test_add_makes_the_package_checks_effective(home: Path) -> None:
    write_package_check(home, "python-strict", name="pkg-ruff", run="ruff-check")
    repo = make_repo(home)

    assert main(["package", "add", "python-strict", "--repo", str(repo)]) == 0

    checks = load_effective_checks(repo, global_config_dir())
    matched = [check for check in checks if check.name == "pkg-ruff"]
    assert len(matched) == 1
    assert matched[0].origin == "package:python-strict"


def test_add_is_idempotent(home: Path) -> None:
    write_package_rule(home, "python-strict", relpath="no-cast.yml", rule_id="pkg-no-cast")
    repo = make_repo(home)

    assert main(["package", "add", "python-strict", "--repo", str(repo)]) == 0
    assert main(["package", "add", "python-strict", "--repo", str(repo)]) == 0

    assert load_local_config(repo).packages == ["python-strict"]


def test_remove_drops_the_opt_in_and_its_rule_copies(home: Path) -> None:
    write_package_rule(home, "python-strict", relpath="no-cast.yml", rule_id="pkg-no-cast")
    repo = make_repo(home)
    assert main(["package", "add", "python-strict", "--repo", str(repo)]) == 0

    assert main(["package", "remove", "python-strict", "--repo", str(repo)]) == 0

    assert load_local_config(repo).packages == []
    assert not (package_mirror(repo) / "python-strict" / "no-cast.yml").exists()


def test_remove_drops_a_package_that_no_longer_exists(home: Path) -> None:
    repo = make_repo(home)
    install_package(repo, "deleted-package")

    assert main(["package", "remove", "deleted-package", "--repo", str(repo)]) == 0

    assert load_local_config(repo).packages == []


def test_remove_of_a_package_the_repo_never_installed_errors_and_lists_installed(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    install_package(repo, "style")
    capsys.readouterr()

    assert main(["package", "remove", "styel", "--repo", str(repo)]) != 0

    assert "package 'styel' is not installed in this repository (installed: style)" in capsys.readouterr().err
    assert load_local_config(repo).packages == ["style"]


def test_add_unknown_package_errors_and_lists_available(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_package_rule(home, "python-strict", relpath="no-cast.yml", rule_id="pkg-no-cast")
    repo = make_repo(home)
    capsys.readouterr()

    assert main(["package", "add", "ghost", "--repo", str(repo)]) != 0

    err = capsys.readouterr().err
    assert "unknown package 'ghost'" in err
    assert "python-strict" in err


def test_add_of_a_package_whose_rule_id_collides_leaves_local_config_as_it_was(home: Path) -> None:
    write_package_rule(home, "a", relpath="no-cast.yml", rule_id="no-cast")
    write_package_rule(home, "b", relpath="no-cast.yml", rule_id="no-cast")
    repo = make_repo(home)
    assert main(["package", "add", "a", "--repo", str(repo)]) == 0

    assert main(["package", "add", "b", "--repo", str(repo)]) != 0

    assert load_local_config(repo).packages == ["a"]
    assert main(["sync", "--repo", str(repo)]) == 0
