"""The OpenCode adapter: a real post-edit plugin.

OpenCode discovers TypeScript plugins under .opencode/plugin/. Ours hooks
tool.execute.after for the file-mutating tools, runs agent-check on the
touched file, and appends any diagnostics to the tool output the model sees.
A `//` comment marker stands in for the HTML-comment marker, which is not
valid TypeScript.
"""

from __future__ import annotations

from byor.io.fsio import MANAGED_NOTICE

OPENCODE_PLUGIN_RELPATH = ".opencode/plugin/byor.ts"

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

// OpenCode's edit and write tools pass the touched file as `filePath`;
// a tool call without it (e.g. a multi-file apply_patch) is skipped.
const filePathArgument = (args: unknown): string | undefined => {
  if (typeof args !== "object" || args === null) return undefined
  const value = Reflect.get(args, "filePath")
  return typeof value === "string" ? value : undefined
}

export const ByorPlugin: Plugin = async ({ $ }) => ({
  "tool.execute.after": async (input, output) => {
    if (!FILE_MUTATING_TOOLS.has(input.tool)) return
    const filePath = filePathArgument(input.args)
    if (filePath === undefined) return
    // nothrow: a byor config error (exit 1) must never break the agent loop.
    const result = await $`byor agent-check --scope diff --files ${filePath}`
      .quiet()
      .nothrow()
    if (result.exitCode === DIAGNOSTICS_EXIT_CODE) {
      output.output += `\\n\\n${result.text()}`
    }
  },
})
"""
)
