"""Render the Pi extension integration.

Pi consumes a managed TypeScript extension file that launches BYOR after edits and displays returned
feedback. This module keeps that generated artifact close to the other agent adapters and markers.
"""

from __future__ import annotations

from byor.io.fsio import MANAGED_NOTICE

__all__ = ()

# Relative to the user's home directory (the global extension location).
PI_EXTENSION_RELPATH = ".pi/agent/extensions/byor.ts"

PI_MARKER = f"// {MANAGED_NOTICE}"

PI_EXTENSION = (
    PI_MARKER
    + """
//
// Runs `byor agent-check` after every file-editing tool call and appends any
// diagnostics to the tool result so the model fixes them immediately.
import { spawnSync } from "node:child_process"
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent"

const FILE_EDITING_TOOLS = new Set(["edit", "write"])

const DIAGNOSTICS_EXIT_CODE = 2

// Pi's edit and write tools name the touched file in the tool input; a call
// without a recognizable path is skipped.
const editedPath = (input: unknown): string | undefined => {
  if (typeof input !== "object" || input === null) return undefined
  for (const key of ["path", "filePath", "file_path"]) {
    const value = Reflect.get(input, key)
    if (typeof value === "string") return value
  }
  return undefined
}

export default function (pi: ExtensionAPI) {
  pi.on("tool_result", (event) => {
    if (!FILE_EDITING_TOOLS.has(event.toolName)) return
    const filePath = editedPath(event.input)
    if (filePath === undefined) return
    const result = spawnSync(
      "byor",
      ["agent-check", "--scope", "diff", "--files", filePath],
      { encoding: "utf8" },
    )
    // Any exit code other than 2 (e.g. a byor config error) is ignored so it
    // never breaks the agent loop.
    if (result.status !== DIAGNOSTICS_EXIT_CODE) return
    const feedback = { type: "text", text: `\\n\\n${result.stdout ?? ""}` }
    return { content: [...(event.content ?? []), feedback] }
  })
}
"""
)
