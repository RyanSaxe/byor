"""Render the BYOR skill from package references.

The managed skill is assembled from the packaged markdown instructions and reference files so agents
receive current BYOR guidance. Rendering here keeps install and self-heal paths from duplicating
markdown packaging details.
"""

from __future__ import annotations

from pathlib import Path

from byor.io.fsio import MANAGED_MARKER
from byor.rules.rules import ALLOW_EXCEPTIONS_SENTENCE

__all__ = (
    "global_skill_dirs",
    "skill_files",
)

_SKILL_RELPATH = "SKILL.md"
_SKILL_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "skill"


def global_skill_dirs(home: Path | None = None) -> tuple[Path, Path]:
    """Return the two global skill directories for the rendered BYOR skill.

    `~/.agents/skills/byor/` is read by Codex, Copilot, opencode, and pi;
    `~/.claude/skills/byor/` by Claude Code, which reads only its own directory.
    The skill describes byor itself, not any repo, so a single render per machine
    — kept current by self-heal — serves every repo.
    """
    base = home or Path.home()
    return (
        base / ".agents" / "skills" / "byor",
        base / ".claude" / "skills" / "byor",
    )


_FRONTMATTER_FENCE = "---\n"
_ALLOW_EXCEPTIONS_PLACEHOLDER = "{{ALLOW_EXCEPTIONS_SENTENCE}}"


def _insert_marker(text: str) -> str:
    if not text.startswith(_FRONTMATTER_FENCE):
        return f"{MANAGED_MARKER}\n\n{text}"
    closing = text.index("\n" + _FRONTMATTER_FENCE, len(_FRONTMATTER_FENCE))
    split = closing + len("\n" + _FRONTMATTER_FENCE)
    return f"{text[:split]}{MANAGED_MARKER}\n\n{text[split:]}"


def _render(text: str) -> str:
    filled = text.replace(_ALLOW_EXCEPTIONS_PLACEHOLDER, ALLOW_EXCEPTIONS_SENTENCE)
    return _insert_marker(filled)


def _walk_markdown(node: Path, prefix: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for child in node.iterdir():
        relpath = f"{prefix}{child.name}"
        if child.is_dir():
            found.extend(_walk_markdown(child, f"{relpath}/"))
        elif child.name.endswith(".md"):
            found.append((relpath, child.read_text(encoding="utf-8")))
    return found


def skill_files() -> list[tuple[str, str]]:
    raw = _walk_markdown(_SKILL_DATA_DIR)
    rendered = [(relpath, _render(text)) for relpath, text in raw]
    return sorted(rendered, key=lambda item: (item[0] != _SKILL_RELPATH, item[0]))
