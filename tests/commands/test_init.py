import io
import sys
from pathlib import Path

import pytest
from support import git

from byor.cli import main
from byor.config import (
    GlobalConfig,
    InitDefaults,
    load_repo_config,
    load_repo_registry,
    save_global_config,
)
from byor.scaffold.ignore import IGNORED_PATTERNS

TRACKED_FILES = (
    "sgconfig.yml",
    ".byor/config.yml",
    ".byor/rules/project/.gitkeep",
    ".byor/rules/personal/local/.gitkeep",
    ".byor/rules/personal/local/.ignore",
    ".byor/rules/personal/global/.gitkeep",
    ".byor/rules/personal/global/.ignore",
)


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An empty repo dir, with the global config dir and home isolated under tmp_path."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


def config_dir(repo: Path) -> Path:
    return repo.parent / "xdg" / "byor"


def test_init_creates_repository_and_global_layout(repo: Path) -> None:
    assert main(["init", "--repo", str(repo), "--non-interactive"]) == 0

    for relpath in TRACKED_FILES:
        assert (repo / relpath).is_file(), relpath
    assert (repo / ".byor" / "local.yml").is_file()
    # init is content-only: it installs no AI agents or skill into the repo.
    assert not (repo / ".claude").exists()
    assert not (repo / ".agents").exists()

    sgconfig = (repo / "sgconfig.yml").read_text()
    for rule_dir in (
        ".byor/rules/project",
        ".byor/rules/personal/local",
        ".byor/rules/personal/global",
    ):
        assert rule_dir in sgconfig

    gitignore = (repo / ".gitignore").read_text()
    assert all(pattern in gitignore for pattern in IGNORED_PATTERNS)

    assert (config_dir(repo) / "config.yml").is_file()
    assert (config_dir(repo) / "rules").is_dir()
    assert load_repo_registry(config_dir(repo) / "repos.yml") == [repo.resolve()]


def test_init_defaults_project_name_to_repo_dir(repo: Path) -> None:
    assert main(["init", "--repo", str(repo), "--non-interactive"]) == 0

    assert load_repo_config(repo).project_name == repo.name


def test_init_is_idempotent(repo: Path) -> None:
    main(["init", "--repo", str(repo), "--non-interactive"])
    snapshot = {path: path.read_text() for path in repo.rglob("*") if path.is_file()}

    assert main(["init", "--repo", str(repo), "--non-interactive"]) == 0

    for path, content in snapshot.items():
        assert path.read_text() == content, path
    assert load_repo_registry(config_dir(repo) / "repos.yml") == [repo.resolve()]


def test_init_preserves_existing_sgconfig_content(repo: Path) -> None:
    (repo / "sgconfig.yml").write_text(
        "# team config\nruleDirs:\n  - custom-rules\nutilDirs:\n  - utils\n"
    )

    assert main(["init", "--repo", str(repo), "--non-interactive"]) == 0

    content = (repo / "sgconfig.yml").read_text()
    assert "# team config" in content
    assert "custom-rules" in content
    assert "utils" in content
    assert ".byor/rules/personal/global" in content


def test_init_rejects_non_list_rule_dirs_without_traceback(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "sgconfig.yml").write_text("ruleDirs: not-a-list\n")

    assert main(["init", "--repo", str(repo), "--non-interactive"]) == 1

    captured = capsys.readouterr()
    assert "expected ruleDirs to be a list" in captured.err
    assert "--replace-sgconfig" in captured.err
    assert "Traceback" not in captured.err


def test_replace_sgconfig_backs_up_then_overwrites(repo: Path) -> None:
    (repo / "sgconfig.yml").write_text("ruleDirs: not-a-list\n")

    assert (
        main(
            [
                "init",
                "--repo",
                str(repo),
                "--non-interactive",
                "--replace-sgconfig",
            ]
        )
        == 0
    )

    backups = list(repo.glob("sgconfig.yml.byor-backup-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "ruleDirs: not-a-list\n"
    assert ".byor/rules/project" in (repo / "sgconfig.yml").read_text()


def test_local_ignore_mode_uses_git_info_exclude(repo: Path) -> None:
    git(repo, "init", "--quiet")

    args = [
        "init",
        "--repo",
        str(repo),
        "--non-interactive",
        "--ignore-mode",
        "local",
    ]
    assert main(args) == 0
    assert main(args) == 0

    exclude = (repo / ".git" / "info" / "exclude").read_text()
    assert exclude.count(".byor/local.yml") == 1
    assert not (repo / ".gitignore").exists()


def test_no_register_creates_empty_registry(repo: Path) -> None:
    assert (
        main(["init", "--repo", str(repo), "--non-interactive", "--no-register"]) == 0
    )

    assert load_repo_registry(config_dir(repo) / "repos.yml") == []


def test_git_hooks_without_a_git_dir_fail_cleanly(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["init", "--repo", str(repo), "--non-interactive", "--git-hooks"]) == 1

    captured = capsys.readouterr()
    assert "has no .git directory" in captured.err
    assert "Traceback" not in captured.err


def test_init_ends_with_quick_doctor_surfacing_problems(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Init succeeds without ast-grep but its closing doctor --quick says so."""
    empty_bin = repo.parent / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    monkeypatch.delenv("BYOR_AST_GREP", raising=False)

    assert main(["init", "--repo", str(repo), "--non-interactive"]) == 0

    out = capsys.readouterr().out
    assert "doctor: ast_grep_found: A working ast-grep could not be found." in out
    assert f"Initialized BYOR in {repo}" in out


def test_interactive_prompts_drive_ignore_mode_and_git_hooks(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    git(repo, "init", "--quiet")
    # init no longer installs agents; answers: ignore -> local, git hooks -> yes.
    monkeypatch.setattr(sys, "stdin", io.StringIO("2\n2\n"))

    assert main(["init", "--repo", str(repo)]) == 0

    assert (repo / ".git" / "info" / "exclude").is_file()
    out = capsys.readouterr().out
    assert "Installed .git/hooks/post-merge" in out
    assert (repo / ".git" / "hooks" / "post-checkout").is_file()


def seed_init_defaults(repo: Path, defaults: InitDefaults) -> None:
    directory = config_dir(repo)
    save_global_config(directory, GlobalConfig(init=defaults))


def test_non_interactive_honors_global_init_defaults(repo: Path) -> None:
    git(repo, "init", "--quiet")
    seed_init_defaults(repo, InitDefaults(ignore_mode="local", git_hooks=True))

    assert main(["init", "--repo", str(repo), "--non-interactive"]) == 0

    assert (repo / ".git" / "info" / "exclude").is_file()
    assert (repo / ".git" / "hooks" / "post-merge").is_file()


def test_explicit_flag_overrides_global_init_default(repo: Path) -> None:
    seed_init_defaults(repo, InitDefaults(ignore_mode="local"))

    assert (
        main(
            [
                "init",
                "--repo",
                str(repo),
                "--non-interactive",
                "--ignore-mode",
                "project",
            ]
        )
        == 0
    )

    assert (repo / ".gitignore").is_file()
    assert not (repo / ".git" / "info" / "exclude").exists()


def test_global_default_seeds_interactive_prompt(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(repo, "init", "--quiet")
    seed_init_defaults(repo, InitDefaults(ignore_mode="local"))
    # Empty answers accept each prompt's default; the global default makes the
    # ignore-mode prompt default to local rather than project.
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n\n\n"))

    assert main(["init", "--repo", str(repo)]) == 0

    assert (repo / ".git" / "info" / "exclude").is_file()
    assert not (repo / ".gitignore").exists()
