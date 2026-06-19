"""The byor skill: a hub SKILL.md plus reference files, assembled from package data.

The whole skill — the hub and every reference — is authored as Markdown under
``data/skill/`` and shipped as package data. This module fills in the shared
exceptions sentence and inserts the managed marker so byor owns the rendered
copies and keeps them current.

The hub frontmatter is deliberately limited to the cross-agent ``name`` +
``description`` fields, the only pair every harness reads, so the one tree works
everywhere — never add a harness-specific field (e.g. Claude's ``allowed-tools``),
which would fork it.
"""

from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from byor.io.fsio import MANAGED_MARKER
from byor.rules.rules import ALLOW_EXCEPTIONS_SENTENCE

_SKILL_RELPATH = "SKILL.md"


def global_skill_dirs(home: Path | None = None) -> tuple[Path, Path]:
    """The two global skill directories byor writes the rendered tree into.

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
    """The managed marker on its own line: after the frontmatter when present
    (the hub), otherwise prepended (reference files have no frontmatter)."""
    if not text.startswith(_FRONTMATTER_FENCE):
        return f"{MANAGED_MARKER}\n\n{text}"
    closing = text.index("\n" + _FRONTMATTER_FENCE, len(_FRONTMATTER_FENCE))
    split = closing + len("\n" + _FRONTMATTER_FENCE)
    return f"{text[:split]}{MANAGED_MARKER}\n\n{text[split:]}"


def _render(text: str) -> str:
    """Fill the exceptions sentence (a no-op where absent) and add the marker."""
    filled = text.replace(_ALLOW_EXCEPTIONS_PLACEHOLDER, ALLOW_EXCEPTIONS_SENTENCE)
    return _insert_marker(filled)


def _walk_markdown(node: Traversable, prefix: str = "") -> list[tuple[str, str]]:
    """Every `.md` file under `node` as `(posix relpath, raw text)` pairs."""
    found: list[tuple[str, str]] = []
    for child in node.iterdir():
        relpath = f"{prefix}{child.name}"
        if child.is_dir():
            found.extend(_walk_markdown(child, f"{relpath}/"))
        elif child.name.endswith(".md"):
            found.append((relpath, child.read_text(encoding="utf-8")))
    return found


def skill_files() -> list[tuple[str, str]]:
    """The rendered skill tree as `(relpath, content)` pairs, hub first.

    The hub SKILL.md leads; references follow in sorted order so the rendered
    tree is deterministic across machines.
    """
    raw = _walk_markdown(files("byor").joinpath("data", "skill"))
    rendered = [(relpath, _render(text)) for relpath, text in raw]
    return sorted(rendered, key=lambda item: (item[0] != _SKILL_RELPATH, item[0]))
