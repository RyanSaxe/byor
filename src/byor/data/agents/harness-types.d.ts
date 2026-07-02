// Dev-only ambient types for the harness APIs the packaged agent sources use.
// CI runs `tsc --noEmit` over the .ts sources against these stubs so a type
// break fails the build; the stubs are never installed anywhere.

declare module "@opencode-ai/plugin" {
  interface ShellOutput {
    exitCode: number
    text(): string
  }

  interface ShellPromise extends Promise<ShellOutput> {
    quiet(): ShellPromise
    nothrow(): ShellPromise
  }

  type Shell = (
    strings: TemplateStringsArray,
    ...values: unknown[]
  ) => ShellPromise

  interface Hooks {
    "tool.execute.after"?: (
      input: { tool: string; args: unknown },
      output: { output: string },
    ) => Promise<void>
  }

  export type Plugin = (input: { $: Shell }) => Promise<Hooks>
}

declare module "@earendil-works/pi-coding-agent" {
  interface ToolResultContent {
    type: string
    text?: string
  }

  interface ToolResultEvent {
    toolName: string
    input: unknown
    content?: ToolResultContent[]
  }

  type ToolResultHandler = (
    event: ToolResultEvent,
  ) => { content: ToolResultContent[] } | undefined

  export interface ExtensionAPI {
    on(event: "tool_result", handler: ToolResultHandler): void
  }
}

declare module "node:child_process" {
  export function spawnSync(
    command: string,
    args: readonly string[],
    options: { encoding: "utf8" },
  ): { status: number | null; stdout: string | null }
}
