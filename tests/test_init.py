import io
import sys
from pathlib import Path

import pytest
from conftest import git

from byor.agents import MANAGED_MARKER
from byor.cli import main
from byor.config import (
    GlobalConfig,
    InitDefaults,
    load_repo_config,
    load_repo_registry,
    save_global_config,
)
from byor.ignore import IGNORED_PATTERNS

TRACKED_FILES = (
    "sgconfig.yml",
    ".byor/config.yml",
    ".byor/rules/project/.gitkeep",
    ".byor/rules/personal/local/.gitkeep",
    ".byor/rules/personal/local/.ignore",
    ".byor/rules/personal/global/.gitkeep",
    ".byor/rules/personal/global/.ignore",
    ".byor/agents/README.md",
    ".agents/skills/byor/SKILL.md",
    ".claude/skills/byor/SKILL.md",
)


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An empty repo dir, with the global config dir isolated under tmp_path."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


def config_dir(repo: Path) -> Path:
    return repo.parent / "xdg" / "byor"


def init(repo: Path, *extra: str) -> int:
    return main(["init", "--repo", str(repo), "--non-interactive", *extra])


def test_init_creates_repository_and_global_layout(repo: Path) -> None:
    assert init(repo) == 0

    for relpath in TRACKED_FILES:
        assert (repo / relpath).is_file(), relpath
    assert (repo / ".byor" / "local.yml").is_file()
    assert MANAGED_MARKER in (repo / ".byor" / "agents" / "README.md").read_text()

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


def test_init_is_idempotent(repo: Path) -> None:
    init(repo)
    snapshot = {path: path.read_text() for path in repo.rglob("*") if path.is_file()}

    assert init(repo) == 0

    for path, content in snapshot.items():
        assert path.read_text() == content, path
    assert load_repo_registry(config_dir(repo) / "repos.yml") == [repo.resolve()]


def test_init_preserves_existing_sgconfig_content(repo: Path) -> None:
    (repo / "sgconfig.yml").write_text(
        "# team config\nruleDirs:\n  - custom-rules\nutilDirs:\n  - utils\n"
    )

    assert init(repo) == 0

    content = (repo / "sgconfig.yml").read_text()
    assert "# team config" in content
    assert "custom-rules" in content
    assert "utils" in content
    assert ".byor/rules/personal/global" in content


def test_init_rejects_non_list_rule_dirs_without_traceback(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "sgconfig.yml").write_text("ruleDirs: not-a-list\n")

    assert init(repo) == 1

    captured = capsys.readouterr()
    assert "expected ruleDirs to be a list" in captured.err
    assert "--replace-sgconfig" in captured.err
    assert "Traceback" not in captured.err


def test_replace_sgconfig_backs_up_then_overwrites(repo: Path) -> None:
    (repo / "sgconfig.yml").write_text("ruleDirs: not-a-list\n")

    assert init(repo, "--replace-sgconfig") == 0

    backups = list(repo.glob("sgconfig.yml.byor-backup-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "ruleDirs: not-a-list\n"
    assert ".byor/rules/project" in (repo / "sgconfig.yml").read_text()


def test_local_ignore_mode_uses_git_info_exclude(repo: Path) -> None:
    (repo / ".git").mkdir()

    assert init(repo, "--ignore-mode", "local") == 0
    assert init(repo, "--ignore-mode", "local") == 0

    exclude = (repo / ".git" / "info" / "exclude").read_text()
    assert exclude.count(".byor/local.yml") == 1
    assert not (repo / ".gitignore").exists()


def test_agents_are_recorded_and_merged_without_duplicates(repo: Path) -> None:
    init(repo, "--agents", "claude-code,codex")
    init(repo, "--agents", "claude-code,generic")

    assert load_repo_config(repo).agents == ["claude-code", "codex", "skill", "generic"]


def test_unknown_agent_fails_cleanly(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert init(repo, "--agents", "frobnicate") == 1
    assert "Unknown agents: frobnicate" in capsys.readouterr().err


def test_no_register_creates_empty_registry(repo: Path) -> None:
    assert init(repo, "--no-register") == 0

    assert load_repo_registry(config_dir(repo) / "repos.yml") == []


def test_unmarked_agent_instructions_are_preserved(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    readme = repo / ".byor" / "agents" / "README.md"
    readme.parent.mkdir(parents=True)
    readme.write_text("my own notes\n")

    assert init(repo) == 0

    assert readme.read_text() == "my own notes\n"
    assert "without the BYOR marker" in capsys.readouterr().out


def test_git_hooks_without_a_git_dir_fail_cleanly(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert init(repo, "--git-hooks") == 1

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

    assert init(repo) == 0

    out = capsys.readouterr().out
    assert "doctor: ast_grep_found: ast-grep is required but was not found." in out
    assert f"Initialized BYOR in {repo}" in out


def test_interactive_prompts_drive_agents_ignore_mode_and_hooks(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    git(repo, "init", "--quiet")
    # Answers: agents -> claude-code, ignore -> local, git hooks -> yes,
    # hook scope -> project (claude-code is hook-capable, so it is asked).
    monkeypatch.setattr(sys, "stdin", io.StringIO("2\n2\n2\n1\n"))

    assert main(["init", "--repo", str(repo)]) == 0

    assert load_repo_config(repo).agents == ["claude-code", "skill"]
    assert (repo / ".git" / "info" / "exclude").is_file()
    assert (repo / ".claude" / "settings.json").is_file()
    out = capsys.readouterr().out
    assert "Installed .git/hooks/post-merge" in out
    assert (repo / ".git" / "hooks" / "post-checkout").is_file()


def test_init_hook_scope_global_writes_configs_under_home(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = repo.parent / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    assert init(repo, "--agents", "cursor", "--hook-scope", "global") == 0

    assert (fake_home / ".cursor" / "hooks.json").is_file()
    assert not (repo / ".cursor" / "hooks.json").exists()


def seed_init_defaults(repo: Path, defaults: InitDefaults) -> None:
    directory = config_dir(repo)
    save_global_config(directory, GlobalConfig(init=defaults))


def test_non_interactive_honors_global_init_defaults(repo: Path) -> None:
    git(repo, "init", "--quiet")
    seed_init_defaults(
        repo, InitDefaults(agents=["claude-code"], ignore_mode="local", git_hooks=True)
    )

    assert init(repo) == 0

    assert load_repo_config(repo).agents == ["claude-code", "skill"]
    assert (repo / ".git" / "info" / "exclude").is_file()
    assert (repo / ".git" / "hooks" / "post-merge").is_file()


def test_global_hook_scope_default_routes_hooks_under_home(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = repo.parent / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    seed_init_defaults(repo, InitDefaults(agents=["cursor"], hook_scope="global"))

    assert init(repo) == 0

    assert (fake_home / ".cursor" / "hooks.json").is_file()
    assert not (repo / ".cursor" / "hooks.json").exists()


def test_explicit_flag_overrides_global_init_default(repo: Path) -> None:
    seed_init_defaults(repo, InitDefaults(ignore_mode="local"))

    assert init(repo, "--ignore-mode", "project") == 0

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
