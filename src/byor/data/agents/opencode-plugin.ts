// {{MANAGED_NOTICE}}
//
// Runs `byor agent-check` after every file-mutating tool call and appends
// any diagnostics to the tool output so the model fixes them immediately.
//
// One default export serves both opencode majors: opencode 2 calls `setup`,
// and opencode 1 (1.3.4 or later) calls `server`. Each ignores the other.
import { execFile } from "node:child_process"
import type { PluginModule } from "@opencode-ai/plugin"
import type { Plugin } from "@opencode/plugin"

const FILE_TOOLS = new Set(["edit", "write"])

// opencode 1 names its patch tool `apply_patch`; opencode 2 names it `patch`.
const PATCH_TOOLS = new Set(["apply_patch", "patch"])

const DIAGNOSTICS_EXIT_CODE = 2

const stringArgument = (args: unknown, key: string): string | undefined => {
  if (typeof args !== "object" || args === null) return undefined
  const value = Reflect.get(args, key)
  return typeof value === "string" ? value : undefined
}

// A patch tool (the sole edit tool for some models) carries a `patchText`
// whose `*** Add File:` / `*** Update File:` markers name every path it
// changed.
const patchPaths = (args: unknown): string[] => {
  const patchText = stringArgument(args, "patchText")
  if (patchText === undefined) return []
  const paths: string[] = []
  for (const line of patchText.split("\n")) {
    for (const marker of ["*** Add File: ", "*** Update File: "]) {
      if (line.startsWith(marker)) paths.push(line.slice(marker.length).trim())
    }
  }
  return paths
}

// edit and write name the touched file in `path` (opencode 2) or `filePath`
// (opencode 1).
const editedPaths = (tool: string, args: unknown): string[] => {
  if (PATCH_TOOLS.has(tool)) return patchPaths(args)
  if (!FILE_TOOLS.has(tool)) return []
  const filePath =
    stringArgument(args, "path") ?? stringArgument(args, "filePath")
  return filePath === undefined ? [] : [filePath]
}

// Resolves to the diagnostics when byor exits 2 and to undefined otherwise,
// and never rejects: a byor config error (exit 1) or a missing byor must
// never break the agent loop. The check runs in the session's directory,
// which is where byor finds the repository and resolves relative paths.
const diagnosticsFor = (
  directory: string,
  tool: string,
  args: unknown,
): Promise<string | undefined> => {
  const paths = editedPaths(tool, args)
  if (paths.length === 0) return Promise.resolve(undefined)
  return new Promise((resolve) => {
    execFile(
      "byor",
      ["agent-check", "--scope", "diff", "--files", ...paths],
      { cwd: directory },
      (error, stdout) => {
        resolve(error?.code === DIAGNOSTICS_EXIT_CODE ? stdout : undefined)
      },
    )
  })
}

export default {
  id: "byor",
  async setup(ctx) {
    await ctx.tool.hook("execute.after", async (event) => {
      if (event.status !== "completed") return
      const diagnostics = await diagnosticsFor(
        ctx.location.directory,
        event.tool,
        event.input,
      )
      if (diagnostics === undefined) return
      const { content } = event.result
      const before =
        typeof content === "string"
          ? [{ type: "text" as const, text: content }]
          : (content ?? [])
      event.result = {
        ...event.result,
        content: [...before, { type: "text", text: diagnostics }],
      }
    })
  },
  server: async ({ directory }) => ({
    "tool.execute.after": async (input, output) => {
      const diagnostics = await diagnosticsFor(
        directory,
        input.tool,
        input.args,
      )
      if (diagnostics !== undefined) output.output += `\n\n${diagnostics}`
    },
  }),
} satisfies Plugin.Plugin & PluginModule
