"""Exercise rule and check listing behavior.

list is the one view over every origin at once: project, local, global, and installed packages, plus
configured checks. The tests pin the scope and tag filters, the skip reasons shown for excluded
global rules, JSON output, the guidance printed when no rules exist, and a clean failure outside an
initialized repo.
"""

import json
from pathlib import Path

import pytest
from support import (
    install_package,
    make_repo,
    write_global_rule,
    write_package_rule,
    write_rule,
)

from byor.cli import main
from byor.config import (
    CheckDef,
    GlobalConfig,
    LocalConfig,
    load_repo_config,
    save_global_config,
    save_local_config,
    save_repo_config,
)


def populate(home: Path, repo: Path) -> None:
    write_rule(repo / ".byor" / "rules" / "project" / "no-foo.yml", "no-foo")
    write_rule(
        repo / ".byor" / "rules" / "personal" / "local" / "no-bar-local.yml",
        "no-bar-local",
    )
    write_global_rule(home, "python/no-baz.yml", rule_id="no-baz")


def test_effective_listing_shows_scope_id_and_path(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    populate(home, repo)
    capsys.readouterr()

    assert main(["list", "--repo", str(repo)]) == 0

    assert capsys.readouterr().out == (
        "project  no-foo        .byor/rules/project/no-foo.yml\n"
        "local    no-bar-local  .byor/rules/personal/local/no-bar-local.yml\n"
        "global   no-baz        .byor/rules/personal/global/python/no-baz.yml\n"
    )


def test_scope_all_appends_skipped_global_rules_with_reasons(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", rule_id="no-cast")
    write_global_rule(home, "no-wrap.yml", rule_id="no-wrap")
    write_rule(repo / ".byor" / "rules" / "project" / "no-cast.yml", "no-cast")
    (repo / ".byor" / "local.yml").write_text("version: 1\nglobal:\n  excluded_rule_ids:\n    - no-wrap\n")
    capsys.readouterr()

    assert main(["list", "--repo", str(repo), "--scope", "all"]) == 0

    out = capsys.readouterr().out
    assert "project  no-cast  .byor/rules/project/no-cast.yml\n" in out
    assert "skipped  no-cast  overridden by project rule\n" in out
    assert "skipped  no-wrap  excluded in .byor/local.yml\n" in out


# The docs point at `byor list --scope all` as the way to see exclusions, but
# skipped package rules only surfaced in `byor sync` output.
def test_scope_all_appends_skipped_package_rules(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_package_rule(home, "pkg1", relpath="no-foo.yml", rule_id="pkg-no-foo")
    repo = make_repo(home)
    install_package(repo, "pkg1")
    assert main(["exclude", "pkg-no-foo", "--repo", str(repo)]) == 0
    capsys.readouterr()

    assert main(["list", "--repo", str(repo), "--scope", "all"]) == 0

    out = capsys.readouterr().out
    assert "skipped" in out
    assert "pkg-no-foo" in out
    assert "excluded in .byor/local.yml" in out


def test_scope_filters_to_one_origin(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    populate(home, repo)
    capsys.readouterr()

    assert main(["list", "--repo", str(repo), "--scope", "project"]) == 0

    out = capsys.readouterr().out
    assert "no-foo" in out
    assert "no-bar-local" not in out
    assert "no-baz" not in out


def test_json_lists_rules_and_skips(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-baz.yml", rule_id="no-baz", tags=["typing"])
    write_global_rule(home, "no-cast.yml", rule_id="no-cast", tags=["typing"])
    write_rule(repo / ".byor" / "rules" / "project" / "no-cast.yml", "no-cast")
    capsys.readouterr()

    assert main(["list", "--repo", str(repo), "--scope", "all", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert {
        "scope": "global",
        "id": "no-baz",
        "path": ".byor/rules/personal/global/no-baz.yml",
        "tags": ["typing"],
    } in payload["rules"]
    assert payload["skipped"] == [
        {
            "id": "no-cast",
            "reason": "overridden by project rule",
            "tags": ["typing"],
        }
    ]


def test_list_surfaces_effective_checks_with_origin_and_exclusions(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    config_dir = home / "xdg" / "byor"
    save_global_config(
        config_dir,
        GlobalConfig(
            checks=[
                CheckDef("ruff", ["py"], "global-ruff"),
                CheckDef("mypy", ["py"], "mypy"),
            ]
        ),
    )
    repo_config = load_repo_config(repo)
    repo_config.checks.append(CheckDef("ruff", ["py"], "repo-ruff"))
    save_repo_config(repo, repo_config)
    save_local_config(repo, LocalConfig(excluded_checks=["mypy"]))
    capsys.readouterr()

    assert main(["list", "--repo", str(repo)]) == 0

    out = capsys.readouterr().out
    assert "check/repo  ruff  repo-ruff" in out
    assert "mypy" not in out  # excluded in local.yml


def test_list_tag_filters_rules_and_checks_together(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    write_global_rule(home, "strict.yml", rule_id="strict", tags=["strict"])
    write_global_rule(home, "style.yml", rule_id="style", tags=["style"])
    save_global_config(
        home / "xdg" / "byor",
        GlobalConfig(
            checks=[
                CheckDef("ty", ["py"], "ty", tags=["strict"]),
                CheckDef("ruff", ["py"], "ruff", tags=["format"]),
            ]
        ),
    )
    main(["sync", "--repo", str(repo)])
    capsys.readouterr()

    assert main(["list", "--repo", str(repo), "--tag", "strict"]) == 0

    out = capsys.readouterr().out
    assert "strict" in out  # rule tagged strict
    assert "ty" in out  # check tagged strict
    assert "style" not in out  # rule with another tag
    assert "ruff" not in out  # check with another tag


def test_list_tags_summarizes_rule_and_check_tags(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    write_global_rule(home, "strict.yml", rule_id="strict", tags=["strict"])
    config_dir = home / "xdg" / "byor"
    save_global_config(
        config_dir,
        GlobalConfig(checks=[CheckDef("ty", ["py"], "ty", tags=["strict"])]),
    )
    main(["sync", "--repo", str(repo)])
    capsys.readouterr()

    assert main(["list", "--repo", str(repo), "--tags"]) == 0

    out = capsys.readouterr().out
    assert "rule   strict  1  global:1" in out
    assert "check  strict  1  global:1" in out


def test_list_guides_the_user_when_there_are_no_rules(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(home)
    capsys.readouterr()

    assert main(["list", "--repo", str(repo)]) == 0

    assert capsys.readouterr().out == "No rules or checks yet. Add a rule with `byor add`.\n"


def test_list_fails_cleanly_outside_an_initialized_repo(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = home / "untouched"
    repo.mkdir()

    assert main(["list", "--repo", str(repo)]) == 1

    captured = capsys.readouterr()
    assert "byor init" in captured.err
    assert "Traceback" not in captured.err


def test_effective_listing_includes_installed_package_rules(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_package_rule(home, "python-strict", relpath="no-cast.yml", rule_id="pkg-no-cast")
    repo = make_repo(home)
    install_package(repo, "python-strict")
    assert main(["sync", "--repo", str(repo)]) == 0
    capsys.readouterr()

    assert main(["list", "--repo", str(repo)]) == 0

    out = capsys.readouterr().out
    assert "package  pkg-no-cast" in out
    assert "personal/packages/python-strict/no-cast.yml" in out


def test_scope_package_shows_only_package_rules(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_package_rule(home, "python-strict", relpath="no-cast.yml", rule_id="pkg-no-cast")
    repo = make_repo(home)
    write_rule(repo / ".byor" / "rules" / "project" / "no-foo.yml", "no-foo")
    install_package(repo, "python-strict")
    assert main(["sync", "--repo", str(repo)]) == 0
    capsys.readouterr()

    assert main(["list", "--repo", str(repo), "--scope", "package"]) == 0

    out = capsys.readouterr().out
    assert "pkg-no-cast" in out
    assert "no-foo" not in out
