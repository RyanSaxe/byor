import json
from pathlib import Path

import pytest
from conftest import make_repo, write_global_rule, write_rule

from byolsp.cli import main


def list_rules(repo: Path, *extra: str) -> int:
    return main(["list", "--repo", str(repo), *extra])


def populate(home: Path, repo: Path) -> None:
    write_rule(repo / ".byolsp" / "rules" / "project" / "no-foo.yml", "no-foo")
    write_rule(
        repo / ".byolsp" / "rules" / "personal" / "local" / "no-bar-local.yml",
        "no-bar-local",
    )
    write_global_rule(home, "python/no-baz.yml", "no-baz")


def test_effective_listing_shows_scope_id_and_path(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    populate(home, repo)
    capsys.readouterr()

    assert list_rules(repo) == 0

    assert capsys.readouterr().out == (
        "project  no-foo        .byolsp/rules/project/no-foo.yml\n"
        "local    no-bar-local  .byolsp/rules/personal/local/no-bar-local.yml\n"
        "global   no-baz        .byolsp/rules/personal/global/python/no-baz.yml\n"
    )


def test_scope_all_appends_skipped_global_rules_with_reasons(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-cast.yml", "no-cast")
    write_global_rule(home, "no-wrap.yml", "no-wrap")
    write_rule(repo / ".byolsp" / "rules" / "project" / "no-cast.yml", "no-cast")
    (repo / ".byolsp" / "local.yml").write_text(
        "version: 1\nglobal:\n  excluded_rule_ids:\n    - no-wrap\n"
    )
    capsys.readouterr()

    assert list_rules(repo, "--scope", "all") == 0

    out = capsys.readouterr().out
    assert "project  no-cast  .byolsp/rules/project/no-cast.yml\n" in out
    assert "skipped  no-cast  overridden by project rule\n" in out
    assert "skipped  no-wrap  excluded in .byolsp/local.yml\n" in out


def test_scope_filters_to_one_origin(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    populate(home, repo)
    capsys.readouterr()

    assert list_rules(repo, "--scope", "project") == 0

    out = capsys.readouterr().out
    assert "no-foo" in out
    assert "no-bar-local" not in out
    assert "no-baz" not in out


def test_json_lists_rules_and_skips(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = make_repo(home)
    write_global_rule(home, "no-baz.yml", "no-baz")
    write_global_rule(home, "no-cast.yml", "no-cast")
    write_rule(repo / ".byolsp" / "rules" / "project" / "no-cast.yml", "no-cast")
    capsys.readouterr()

    assert list_rules(repo, "--scope", "all", "--json") == 0

    payload = json.loads(capsys.readouterr().out)
    assert {
        "scope": "global",
        "id": "no-baz",
        "path": ".byolsp/rules/personal/global/no-baz.yml",
    } in payload["rules"]
    assert payload["skipped"] == [
        {"id": "no-cast", "reason": "overridden by project rule"}
    ]


def test_list_fails_cleanly_outside_an_initialized_repo(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = home / "untouched"
    repo.mkdir()

    assert list_rules(repo) == 1

    captured = capsys.readouterr()
    assert "byolsp init" in captured.err
    assert "Traceback" not in captured.err
