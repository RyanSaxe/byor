"""The OpenCode adapter: a real post-edit plugin.

OpenCode loads global plugins from ~/.config/opencode/plugin/, so byor installs
the plugin once there and it fires in every project. It hooks
tool.execute.after for the file-mutating tools, runs agent-check on the touched
file, and appends any diagnostics to the tool output the model sees. A `//`
comment marker stands in for the HTML-comment marker, which is not valid
TypeScript.
"""

from __future__ import annotations

from byor.io.fsio import MANAGED_NOTICE

# Relative to the user's home directory (the global plugin location).
OPENCODE_PLUGIN_RELPATH = ".config/opencode/plugin/byor.ts"

OPENCODE_MARKER = f"// {MANAGED_NOTICE}"

OPENCODE_PLUGIN = (
    OPENCODE_MARKER
    + """
//
// Runs `byor agent-check` after every file-mutating tool call and appends
// any diagnostics to the tool output so the model fixes them immediately.
import type { Plugin } from "@opencode-ai/plugin"

const FILE_MUTATING_TOOLS = new Set(["edit", "write", "apply_patch"])

const DIAGNOSTICS_EXIT_CODE = 2

const stringArgument = (args: unknown, key: string): string | undefined => {
  if (typeof args !== "object" || args === null) return undefined
  const value = Reflect.get(args, key)
  return typeof value === "string" ? value : undefined
}

// edit/write name the touched file in `filePath`; apply_patch (the sole edit
// tool for some models) instead carries a `patchText` whose `*** Add File:` /
// `*** Update File:` markers name every path it changed.
const patchPaths = (args: unknown): string[] => {
  const patchText = stringArgument(args, "patchText")
  if (patchText === undefined) return []
  const paths: string[] = []
  for (const line of patchText.split("\\n")) {
    for (const marker of ["*** Add File: ", "*** Update File: "]) {
      if (line.startsWith(marker)) paths.push(line.slice(marker.length).trim())
    }
  }
  return paths
}

const editedPaths = (tool: string, args: unknown): string[] => {
  if (tool === "apply_patch") return patchPaths(args)
  const filePath = stringArgument(args, "filePath")
  return filePath === undefined ? [] : [filePath]
}

export const ByorPlugin: Plugin = async ({ $ }) => ({
  "tool.execute.after": async (input, output) => {
    if (!FILE_MUTATING_TOOLS.has(input.tool)) return
    const paths = editedPaths(input.tool, input.args)
    if (paths.length === 0) return
    // nothrow: a byor config error (exit 1) must never break the agent loop.
    const result = await $`byor agent-check --scope diff --files ${paths}`
      .quiet()
      .nothrow()
    if (result.exitCode === DIAGNOSTICS_EXIT_CODE) {
      output.output += `\\n\\n${result.text()}`
    }
  },
})
"""
)
