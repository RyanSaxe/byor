from pathlib import Path

import pytest

from byor.config import (
    CheckDef,
    GlobalConfig,
    InitDefaults,
    LocalConfig,
    RepoConfig,
    global_rules_dir,
    load_global_config,
    load_local_config,
    load_repo_config,
    load_repo_registry,
    register_repo,
    repo_registry_path,
    save_global_config,
    save_local_config,
    save_repo_config,
)
from byor.errors import ConfigError, RepoNotInitialized


def test_repo_config_round_trips_and_writes_version(tmp_path: Path) -> None:
    config = RepoConfig(project_name="demo", agents=["claude-code"])

    save_repo_config(tmp_path, config)

    assert load_repo_config(tmp_path) == config
    assert "version: 1" in (tmp_path / ".byor" / "config.yml").read_text()


def test_repo_config_missing_means_repo_not_initialized(tmp_path: Path) -> None:
    with pytest.raises(RepoNotInitialized, match="byor init"):
        load_repo_config(tmp_path)


def test_repo_config_rejects_unsupported_version(tmp_path: Path) -> None:
    (tmp_path / ".byor").mkdir()
    (tmp_path / ".byor" / "config.yml").write_text("version: 2\n")

    with pytest.raises(ConfigError, match="version"):
        load_repo_config(tmp_path)


def test_repo_config_rejects_wrongly_typed_values(tmp_path: Path) -> None:
    (tmp_path / ".byor").mkdir()
    (tmp_path / ".byor" / "config.yml").write_text(
        "version: 1\nai:\n  agents: not-a-list\n"
    )

    with pytest.raises(ConfigError, match="list of strings"):
        load_repo_config(tmp_path)


def test_repo_config_round_trips_checks(tmp_path: Path) -> None:
    config = RepoConfig(
        checks=[CheckDef("ruff", ["py"], "uv run ruff check --output-format concise")]
    )

    save_repo_config(tmp_path, config)

    assert load_repo_config(tmp_path).checks == config.checks


def test_repo_config_rejects_a_check_missing_name_or_run(tmp_path: Path) -> None:
    (tmp_path / ".byor").mkdir()
    (tmp_path / ".byor" / "config.yml").write_text(
        "version: 1\nchecks:\n  - extensions: [py]\n"
    )

    with pytest.raises(ConfigError, match="name"):
        load_repo_config(tmp_path)


def test_local_config_defaults_when_file_absent(tmp_path: Path) -> None:
    assert load_local_config(tmp_path) == LocalConfig()


def test_local_config_round_trips_excluded_checks(tmp_path: Path) -> None:
    config = LocalConfig(excluded_checks=["ruff"])

    save_local_config(tmp_path, config)

    assert load_local_config(tmp_path).excluded_checks == ["ruff"]


def test_local_config_round_trips(tmp_path: Path) -> None:
    config = LocalConfig(excluded_rule_ids=["no-python-cast"])

    save_local_config(tmp_path, config)

    assert load_local_config(tmp_path) == config


def test_save_preserves_user_comments_and_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / ".byor" / "local.yml"
    path.parent.mkdir()
    path.write_text(
        "# personal overrides\n"
        "version: 1\n"
        "global:\n"
        "  excluded_rule_ids: []\n"
        "experimental: true\n"
    )

    save_local_config(tmp_path, LocalConfig(excluded_rule_ids=["no-python-cast"]))

    content = path.read_text()
    assert "# personal overrides" in content
    assert "experimental: true" in content
    assert "no-python-cast" in content


def test_global_config_defaults_when_file_absent(tmp_path: Path) -> None:
    assert load_global_config(tmp_path) == GlobalConfig()


def test_global_config_round_trips(tmp_path: Path) -> None:
    config = GlobalConfig(ast_grep_command="sg")

    save_global_config(tmp_path, config)

    assert load_global_config(tmp_path) == config


def test_global_config_round_trips_checks_and_init_defaults(tmp_path: Path) -> None:
    config = GlobalConfig(
        checks=[CheckDef("mypy", ["py"], "mypy")],
        init=InitDefaults(
            agents=["codex"],
            ignore_mode="local",
            git_hooks=True,
        ),
    )

    save_global_config(tmp_path, config)

    assert load_global_config(tmp_path) == config


def test_global_init_defaults_absent_when_unset(tmp_path: Path) -> None:
    (tmp_path / "config.yml").write_text("version: 1\n")

    assert load_global_config(tmp_path).init == InitDefaults()


def test_global_config_partial_file_fills_defaults(tmp_path: Path) -> None:
    (tmp_path / "config.yml").write_text("version: 1\nast_grep:\n  command: sg\n")

    loaded = load_global_config(tmp_path)

    assert loaded.ast_grep_command == "sg"
    assert loaded.rules_path == "rules"
    assert loaded.repos_path == "repos.yml"


def test_global_paths_resolve_relative_to_config_dir_unless_absolute(
    tmp_path: Path,
) -> None:
    relative = GlobalConfig()
    elsewhere = tmp_path / "elsewhere" / "rules"
    absolute = GlobalConfig(rules_path=str(elsewhere))

    assert global_rules_dir(tmp_path, relative) == tmp_path / "rules"
    assert repo_registry_path(tmp_path, relative) == tmp_path / "repos.yml"
    assert global_rules_dir(tmp_path, absolute) == elsewhere


def test_register_repo_creates_registry_and_is_idempotent(tmp_path: Path) -> None:
    registry_path = tmp_path / "config" / "byor" / "repos.yml"
    repo = tmp_path / "repo"
    repo.mkdir()

    assert register_repo(repo, registry_path) is True
    assert register_repo(repo, registry_path) is False

    assert load_repo_registry(registry_path) == [repo.resolve()]
