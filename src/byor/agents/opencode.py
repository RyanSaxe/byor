"""Render the OpenCode plugin integration.

OpenCode consumes a TypeScript plugin file rather than a JSON hook entry, so BYOR renders the
managed plugin from a packaged `.ts` source (checked by prettier and tsc in CI), substituting the
managed-notice placeholder at import. That keeps installation deterministic and easy for doctor to
compare.
"""

from __future__ import annotations

from importlib.resources import files

from byor.io.fsio import MANAGED_NOTICE

__all__ = ()

# Relative to the user's home directory (the global plugin location).
OPENCODE_PLUGIN_RELPATH = ".config/opencode/plugin/byor.ts"

OPENCODE_MARKER = f"// {MANAGED_NOTICE}"

_MANAGED_NOTICE_PLACEHOLDER = "{{MANAGED_NOTICE}}"


def _render_plugin() -> str:
    source = files("byor").joinpath("data", "agents", "opencode-plugin.ts").read_text(encoding="utf-8")
    return source.replace(_MANAGED_NOTICE_PLACEHOLDER, MANAGED_NOTICE)


OPENCODE_PLUGIN = _render_plugin()
