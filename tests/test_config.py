"""Exercise BYOR configuration loading and persistence.

Every config layer — repo, local, global — must round-trip through save and load while preserving
user comments and unknown keys, and default cleanly when its file is absent. The rejection cases are
the contract's teeth: unsupported versions, wrongly typed fields, and malformed checks or profiles
raise instead of degrading, and repo registration is idempotent.
"""

from pathlib import Path

import pytest

from byor.config import (
    CheckDef,
    CommandCheckDef,
    GlobalConfig,
    InitDefaults,
    LocalConfig,
    ProfileConfig,
    RepoConfig,
    RepoPaths,
    command_rule_dir_relpaths,
    command_rules_relpath,
    global_commands_dir,
    global_rules_dir,
    load_global_config,
    load_local_config,
    load_package_command_checks,
    load_repo_config,
    load_repo_registry,
    register_repo,
    repo_registry_path,
    save_global_config,
    save_local_config,
    save_repo_config,
)
from byor.errors import ConfigError, RepoNotInitializedError


def test_repo_config_round_trips_and_writes_version(tmp_path: Path) -> None:
    config = RepoConfig(project_name="demo")

    save_repo_config(tmp_path, config)

    assert load_repo_config(tmp_path) == config
    assert "version: 1" in (tmp_path / ".byor" / "config.yml").read_text()


def test_repo_config_missing_means_repo_not_initialized(tmp_path: Path) -> None:
    with pytest.raises(RepoNotInitializedError, match="byor init"):
        load_repo_config(tmp_path)


def test_repo_config_rejects_unsupported_version(tmp_path: Path) -> None:
    (tmp_path / ".byor").mkdir()
    (tmp_path / ".byor" / "config.yml").write_text("version: 2\n")

    with pytest.raises(ConfigError, match="version"):
        load_repo_config(tmp_path)


def test_global_config_rejects_wrongly_typed_agents(tmp_path: Path) -> None:
    (tmp_path / "config.yml").write_text("version: 1\nai:\n  agents: not-a-list\n")

    with pytest.raises(ConfigError, match="list of strings"):
        load_global_config(tmp_path)


def test_repo_config_round_trips_checks(tmp_path: Path) -> None:
    config = RepoConfig(checks=[CheckDef("ruff", ["py"], "uv run ruff check --output-format concise")])

    save_repo_config(tmp_path, config)

    assert load_repo_config(tmp_path).checks == config.checks


def test_repo_config_round_trips_an_agent_only_check_and_omits_the_default(tmp_path: Path) -> None:
    save_repo_config(tmp_path, RepoConfig(checks=[CheckDef("deps", ["toml"], "scripts/deps.sh", gate=False)]))
    assert load_repo_config(tmp_path).checks[0].gate is False

    save_repo_config(tmp_path, RepoConfig(checks=[CheckDef("ruff", ["py"], "uv run ruff check")]))
    assert load_repo_config(tmp_path).checks[0].gate is True
    assert "gate" not in (tmp_path / ".byor" / "config.yml").read_text()


def test_repo_config_rejects_a_check_missing_name_or_run(tmp_path: Path) -> None:
    (tmp_path / ".byor").mkdir()
    (tmp_path / ".byor" / "config.yml").write_text("version: 1\nchecks:\n  - extensions: [py]\n")

    with pytest.raises(ConfigError, match="name"):
        load_repo_config(tmp_path)


def test_repo_config_rejects_a_check_with_an_unparseable_run(tmp_path: Path) -> None:
    (tmp_path / ".byor").mkdir()
    # run value is the single token `cmd "` — an unterminated shell quote.
    (tmp_path / ".byor" / "config.yml").write_text("version: 1\nchecks:\n  - name: bad\n    run: 'cmd \"'\n")

    with pytest.raises(ConfigError, match="invalid 'run'"):
        load_repo_config(tmp_path)


def test_repo_config_round_trips_command_checks_and_omits_the_empty_list(tmp_path: Path) -> None:
    config = RepoConfig(command_checks=[CommandCheckDef("no-net", ".byor/scripts/no-net.py", tags=["deps"])])
    save_repo_config(tmp_path, config)
    assert load_repo_config(tmp_path).command_checks == config.command_checks

    save_repo_config(tmp_path, RepoConfig())
    assert "command_checks" not in (tmp_path / ".byor" / "config.yml").read_text()


def test_repo_config_rejects_a_command_check_missing_name_or_run(tmp_path: Path) -> None:
    (tmp_path / ".byor").mkdir()
    (tmp_path / ".byor" / "config.yml").write_text("version: 1\ncommand_checks:\n  - name: no-net\n")

    with pytest.raises(ConfigError, match="name"):
        load_repo_config(tmp_path)


def test_repo_config_rejects_a_command_check_with_an_unparseable_run(tmp_path: Path) -> None:
    (tmp_path / ".byor").mkdir()
    # run value is the single token `cmd "` — an unterminated shell quote.
    (tmp_path / ".byor" / "config.yml").write_text("version: 1\ncommand_checks:\n  - name: bad\n    run: 'cmd \"'\n")

    with pytest.raises(ConfigError, match="invalid 'run'"):
        load_repo_config(tmp_path)


def test_command_rule_dirs_derive_from_the_commands_root() -> None:
    paths = RepoPaths()
    assert command_rules_relpath(paths, "project") == ".byor/commands/project"
    assert command_rule_dir_relpaths(paths) == [
        ".byor/commands/project",
        ".byor/commands/personal/local",
        ".byor/commands/personal/global",
        ".byor/commands/personal/packages",
    ]

    moved = RepoPaths(command_rules="tools/commands")
    assert command_rules_relpath(moved, "local") == "tools/commands/personal/local"


def test_repo_config_round_trips_a_custom_command_rules_root(tmp_path: Path) -> None:
    config = RepoConfig(paths=RepoPaths(command_rules="tools/commands"))
    save_repo_config(tmp_path, config)
    assert load_repo_config(tmp_path).paths.command_rules == "tools/commands"


def test_repo_config_round_trips_fail_on_and_omits_the_default(tmp_path: Path) -> None:
    save_repo_config(tmp_path, RepoConfig(fail_on="error"))
    assert load_repo_config(tmp_path).fail_on == "error"

    save_repo_config(tmp_path, RepoConfig())
    assert "fail_on" not in (tmp_path / ".byor" / "config.yml").read_text()


def test_repo_config_rejects_an_unknown_fail_on(tmp_path: Path) -> None:
    (tmp_path / ".byor").mkdir()
    (tmp_path / ".byor" / "config.yml").write_text("version: 1\nfail_on: sometimes\n")

    with pytest.raises(ConfigError, match="'all' or 'error'"):
        load_repo_config(tmp_path)


def test_local_config_defaults_when_file_absent(tmp_path: Path) -> None:
    assert load_local_config(tmp_path) == LocalConfig()


def test_local_config_round_trips_excluded_checks(tmp_path: Path) -> None:
    config = LocalConfig(
        excluded_checks=["ruff"],
        excluded_check_tags=["strict"],
    )

    save_local_config(tmp_path, config)

    assert load_local_config(tmp_path).excluded_checks == ["ruff"]
    assert load_local_config(tmp_path).excluded_check_tags == ["strict"]


# A `..`-style package name would traverse out of the packages mirror when the
# name is joined onto packages_root, so load_local_config rejects it outright.
@pytest.mark.parametrize("name", ["..", ".", "", "../evil", "a/b", "a\\b", "evil/.."])
def test_local_config_rejects_path_like_package_names(tmp_path: Path, name: str) -> None:
    path = tmp_path / ".byor" / "local.yml"
    path.parent.mkdir()
    quoted = name.replace("\\", "\\\\")
    path.write_text(f'version: 1\npackages:\n  - "{quoted}"\n')

    with pytest.raises(ConfigError, match="bare directory name"):
        load_local_config(tmp_path)


def test_local_config_accepts_bare_package_names(tmp_path: Path) -> None:
    path = tmp_path / ".byor" / "local.yml"
    path.parent.mkdir()
    path.write_text("version: 1\npackages:\n  - python-strict_v1.0\n")

    assert load_local_config(tmp_path).packages == ["python-strict_v1.0"]


def test_local_config_round_trips(tmp_path: Path) -> None:
    config = LocalConfig(
        excluded_rule_ids=["no-python-cast"],
        excluded_rule_tags=["legacy-risk"],
    )

    save_local_config(tmp_path, config)

    assert load_local_config(tmp_path) == config


def test_save_preserves_user_comments_and_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / ".byor" / "local.yml"
    path.parent.mkdir()
    path.write_text("# personal overrides\nversion: 1\nglobal:\n  excluded_rule_ids: []\nexperimental: true\n")

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
        agents=["claude-code", "skill"],
        checks=[CheckDef("mypy", ["py"], "mypy", tags=["strict"])],
        init=InitDefaults(
            private=True,
            git_hooks=True,
            profile="existing",
        ),
        profiles={
            "existing": ProfileConfig(
                description="Low-friction defaults.",
                excluded_rule_ids=["python.no-staticmethod"],
                excluded_rule_tags=["legacy-risk"],
                excluded_checks=["ty"],
                excluded_check_tags=["strict"],
            )
        },
    )

    save_global_config(tmp_path, config)

    assert load_global_config(tmp_path) == config


def test_global_config_round_trips_disabled_repos_and_omits_the_empty_list(tmp_path: Path) -> None:
    config = GlobalConfig(disabled_repos=[tmp_path / "legacy", tmp_path / "clients"])

    save_global_config(tmp_path, config)

    assert load_global_config(tmp_path) == config

    save_global_config(tmp_path, GlobalConfig())

    assert load_global_config(tmp_path).disabled_repos == []
    assert "disabled_repos" not in (tmp_path / "config.yml").read_text()


def test_global_config_rejects_malformed_profiles(tmp_path: Path) -> None:
    (tmp_path / "config.yml").write_text("version: 1\nprofiles:\n  existing: bad\n")

    with pytest.raises(ConfigError, match="profile 'existing'"):
        load_global_config(tmp_path)


def test_global_config_rejects_malformed_profile_tags(tmp_path: Path) -> None:
    (tmp_path / "config.yml").write_text(
        "version: 1\nprofiles:\n  existing:\n    rules:\n      excluded_tags: legacy-risk\n"
    )

    with pytest.raises(ConfigError, match="excluded_tags"):
        load_global_config(tmp_path)


def test_global_config_round_trips_output_concise(tmp_path: Path) -> None:
    config = GlobalConfig(output_concise=True)

    save_global_config(tmp_path, config)

    assert load_global_config(tmp_path) == config


def test_global_config_rejects_non_boolean_concise(tmp_path: Path) -> None:
    (tmp_path / "config.yml").write_text("version: 1\noutput:\n  concise: maybe\n")

    with pytest.raises(ConfigError, match="concise"):
        load_global_config(tmp_path)


def test_global_config_round_trips_output_max_diagnostics(tmp_path: Path) -> None:
    config = GlobalConfig(output_max_diagnostics=5)

    save_global_config(tmp_path, config)

    assert load_global_config(tmp_path).output_max_diagnostics == 5


def test_global_config_omits_max_diagnostics_when_unlimited(tmp_path: Path) -> None:
    save_global_config(tmp_path, GlobalConfig())

    assert "max_diagnostics" not in (tmp_path / "config.yml").read_text()


def test_global_config_rejects_non_positive_max_diagnostics(tmp_path: Path) -> None:
    (tmp_path / "config.yml").write_text("version: 1\noutput:\n  max_diagnostics: 0\n")

    with pytest.raises(ConfigError, match="max_diagnostics"):
        load_global_config(tmp_path)


def test_global_init_defaults_absent_when_unset(tmp_path: Path) -> None:
    (tmp_path / "config.yml").write_text("version: 1\n")

    assert load_global_config(tmp_path).init == InitDefaults()


def test_global_config_partial_file_fills_defaults(tmp_path: Path) -> None:
    (tmp_path / "config.yml").write_text("version: 1\nast_grep:\n  command: sg\n")

    loaded = load_global_config(tmp_path)

    assert loaded.ast_grep_command == "sg"
    assert loaded.rules_path == "rules"
    assert loaded.repos_path == "repos.yml"
    assert loaded.output_concise is False


def test_global_paths_resolve_relative_to_config_dir_unless_absolute(
    tmp_path: Path,
) -> None:
    relative = GlobalConfig()
    elsewhere = tmp_path / "elsewhere" / "rules"
    absolute = GlobalConfig(rules_path=str(elsewhere))

    assert global_rules_dir(tmp_path, relative) == tmp_path / "rules"
    assert global_commands_dir(tmp_path, relative) == tmp_path / "commands"
    assert repo_registry_path(tmp_path, relative) == tmp_path / "repos.yml"
    assert global_rules_dir(tmp_path, absolute) == elsewhere


def test_package_command_checks_load_from_the_package_manifest(tmp_path: Path) -> None:
    package = tmp_path / "packages" / "style"
    package.mkdir(parents=True)
    (package / "checks.yml").write_text(
        "checks:\n"
        "  - name: ruff\n"
        "    extensions: [py]\n"
        "    run: uv run ruff check\n"
        "command_checks:\n"
        "  - name: no-net\n"
        "    run: scripts/no-net.py\n"
    )

    config = GlobalConfig()
    checks = load_package_command_checks(tmp_path, config, name="style")

    assert checks == [CommandCheckDef("no-net", "scripts/no-net.py")]
    assert load_package_command_checks(tmp_path, config, name="missing") == []


def test_register_repo_creates_registry_and_is_idempotent(tmp_path: Path) -> None:
    registry_path = tmp_path / "config" / "byor" / "repos.yml"
    repo = tmp_path / "repo"
    repo.mkdir()

    assert register_repo(repo, registry_path) is True
    assert register_repo(repo, registry_path) is False

    assert load_repo_registry(registry_path) == [repo.resolve()]
