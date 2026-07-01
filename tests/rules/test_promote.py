from pathlib import Path

import pytest
from support import (
    install_package,
    make_repo,
    mirror,
    write_global_check,
    write_global_rule,
    write_package_check,
    write_package_rule,
    write_rule,
)

from byor.cli import main
from byor.config import load_repo_config


def test_promote_local_moves_the_rule_preserving_its_relative_path(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    local = write_rule(
        repo / ".byor" / "rules" / "personal" / "local" / "python" / "exp.yml", "exp"
    )
    content = local.read_text()
    capsys.readouterr()

    assert main(["promote", "--repo", str(repo), "exp", "--from", "local"]) == 0

    destination = repo / ".byor" / "rules" / "project" / "python" / "exp.yml"
    assert destination.read_text() == content
    assert not local.exists()
    out = capsys.readouterr().out
    assert "Promoted 'exp' to .byor/rules/project/python/exp.yml" in out


def test_promote_local_with_keep_local_fails_before_writing(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    local = write_rule(
        repo / ".byor" / "rules" / "personal" / "local" / "exp.yml", "exp"
    )

    assert (
        main(
            [
                "promote",
                "--repo",
                str(repo),
                "exp",
                "--from",
                "local",
                "--keep-local",
            ]
        )
        == 1
    )

    assert local.is_file()
    assert not (repo / ".byor" / "rules" / "project" / "exp.yml").exists()
    err = capsys.readouterr().err
    assert "A local variation of a project rule requires a different ID." in err


def test_promote_global_copies_without_touching_canonical_or_exclusions(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    canonical = write_global_rule(home, "no-cast.yml", "no-cast")
    main(["sync", "--repo", str(repo)])
    capsys.readouterr()

    assert main(["promote", "--repo", str(repo), "no-cast", "--from", "global"]) == 0

    destination = repo / ".byor" / "rules" / "project" / "no-cast.yml"
    assert destination.read_text() == canonical.read_text()
    assert canonical.is_file()
    assert not (mirror(repo) / "no-cast.yml").exists()
    assert "excluded_rule_ids: []" in (repo / ".byor" / "local.yml").read_text()
    assert f"Synced 1 removed global rule into {repo}" in capsys.readouterr().out


def test_promote_requires_replace_to_overwrite_the_destination(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    # The destination file exists but holds a different rule ID, so the repo
    # is healthy before promote and only the file path collides.
    existing = write_rule(
        repo / ".byor" / "rules" / "project" / "exp.yml", "old-exp", message="Old."
    )
    local = write_rule(
        repo / ".byor" / "rules" / "personal" / "local" / "exp.yml",
        "exp",
        message="New.",
    )
    promoted_content = local.read_text()

    assert main(["promote", "--repo", str(repo), "exp", "--from", "local"]) == 1
    assert "rerun with --replace" in capsys.readouterr().err
    assert "Old." in existing.read_text()
    assert local.is_file()

    assert (
        main(
            [
                "promote",
                "--repo",
                str(repo),
                "exp",
                "--from",
                "local",
                "--replace",
            ]
        )
        == 0
    )
    assert existing.read_text() == promoted_content
    assert not local.exists()


def test_promote_unknown_rule_id_fails_cleanly(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)

    assert main(["promote", "--repo", str(repo), "missing", "--from", "global"]) == 1

    captured = capsys.readouterr()
    assert "No rule with ID 'missing' found in global rules." in captured.err
    assert "Traceback" not in captured.err


def test_promote_package_rule_copies_it_into_project_and_keeps_the_source(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = write_package_rule(home, "python-strict", "python/no-cast.yml", "pkg-cast")
    repo = make_repo(home)
    install_package(repo, "python-strict")
    capsys.readouterr()

    assert main(["promote", "--repo", str(repo), "pkg-cast", "--from", "package"]) == 0

    destination = repo / ".byor" / "rules" / "project" / "python" / "no-cast.yml"
    assert destination.is_file()
    assert source.is_file()
    # The package copy is dropped from the mirror: project now owns the ID.
    assert not (
        repo / ".byor" / "rules" / "personal" / "packages" / "python-strict"
    ).exists()
    assert "Promoted 'pkg-cast'" in capsys.readouterr().out


def test_promote_check_vendors_a_package_check_into_repo_config(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_package_check(home, "python-strict", "pkg-ruff", "ruff-check")
    repo = make_repo(home)
    install_package(repo, "python-strict")
    capsys.readouterr()

    assert main(["promote", "--repo", str(repo), "--check", "pkg-ruff"]) == 0

    repo_checks = load_repo_config(repo).checks
    assert [check.name for check in repo_checks] == ["pkg-ruff"]
    assert "Promoted check 'pkg-ruff'" in capsys.readouterr().out


def test_promote_check_vendors_a_global_check_into_repo_config(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_global_check("global-ruff", "ruff-check")
    repo = make_repo(home)
    capsys.readouterr()

    assert main(["promote", "--repo", str(repo), "--check", "global-ruff"]) == 0

    assert [check.name for check in load_repo_config(repo).checks] == ["global-ruff"]


def test_promote_check_that_is_already_a_repo_check_fails(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_package_check(home, "python-strict", "pkg-ruff", "ruff-check")
    repo = make_repo(home)
    install_package(repo, "python-strict")
    assert main(["promote", "--repo", str(repo), "--check", "pkg-ruff"]) == 0
    capsys.readouterr()

    assert main(["promote", "--repo", str(repo), "--check", "pkg-ruff"]) != 0

    assert "already a repo check" in capsys.readouterr().err
