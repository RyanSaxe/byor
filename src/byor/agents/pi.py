"""Render the Pi extension integration.

Pi consumes a managed TypeScript extension file that launches BYOR after edits and displays returned
feedback. The extension is rendered from a packaged `.ts` source (checked by prettier and tsc in
CI), substituting the managed-notice placeholder at import.
"""

from __future__ import annotations

from importlib.resources import files

from byor.io.fsio import MANAGED_NOTICE

__all__ = ()

# Relative to the user's home directory (the global extension location).
PI_EXTENSION_RELPATH = ".pi/agent/extensions/byor.ts"

PI_MARKER = f"// {MANAGED_NOTICE}"

_MANAGED_NOTICE_PLACEHOLDER = "{{MANAGED_NOTICE}}"


def _render_extension() -> str:
    source = files("byor").joinpath("data", "agents", "pi-extension.ts").read_text(encoding="utf-8")
    return source.replace(_MANAGED_NOTICE_PLACEHOLDER, MANAGED_NOTICE)


PI_EXTENSION = _render_extension()
