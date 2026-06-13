"""The byor rule-capture skill: SKILL.md assembled from package data.

The whole skill — frontmatter and body — is authored as Markdown in
``data/skill.md`` and shipped as package data. This module fills in the shared
exceptions sentence and inserts the managed marker just after the frontmatter.

byor owns the rendered copies and keeps them current; the frontmatter is
deliberately limited to the cross-agent ``name`` + ``description`` fields, the
only pair every harness reads, so the one file works everywhere — never add a
harness-specific field (e.g. Claude's ``allowed-tools``), which would fork it.
"""

from __future__ import annotations

from importlib.resources import files

from byor.fsio import MANAGED_MARKER
from byor.rules import ALLOW_EXCEPTIONS_SENTENCE

# No single path is read by every harness (Claude Code reads .claude/, Codex and
# Pi read .agents/), so byor writes the same skill to both as owned copies.
SKILL_RELPATHS = (
    ".agents/skills/byor/SKILL.md",
    ".claude/skills/byor/SKILL.md",
)

_FRONTMATTER_FENCE = "---\n"
_ALLOW_EXCEPTIONS_PLACEHOLDER = "{{ALLOW_EXCEPTIONS_SENTENCE}}"


def _split_frontmatter(text: str) -> tuple[str, str]:
    """The `---`-fenced frontmatter block (trailing fence included) and the body."""
    if not text.startswith(_FRONTMATTER_FENCE):
        raise ValueError("data/skill.md must open with a --- frontmatter block")
    closing = text.index("\n" + _FRONTMATTER_FENCE, len(_FRONTMATTER_FENCE))
    split = closing + len("\n" + _FRONTMATTER_FENCE)
    return text[:split], text[split:]


def _render_skill() -> str:
    """The packaged skill with the exceptions sentence filled in and the managed
    marker on its own line just after the frontmatter."""
    raw = files("byor").joinpath("data", "skill.md").read_text(encoding="utf-8")
    raw = raw.replace(_ALLOW_EXCEPTIONS_PLACEHOLDER, ALLOW_EXCEPTIONS_SENTENCE)
    frontmatter, body = _split_frontmatter(raw)
    return f"{frontmatter}{MANAGED_MARKER}\n\n{body}"


SKILL_MARKDOWN = _render_skill()
