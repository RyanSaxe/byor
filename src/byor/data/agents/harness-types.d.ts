// Dev-only ambient types for the harness APIs the packaged agent sources use.
// CI runs `oxlint --type-aware --type-check` over the .ts sources against these
// stubs so a type break fails the build; the stubs are never installed anywhere.

// opencode 1's plugin package.
declare module "@opencode-ai/plugin" {
  interface Hooks {
    "tool.execute.after"?: (
      input: { tool: string; args: unknown },
      output: { output: string },
    ) => Promise<void>
  }

  type Plugin = (input: { directory: string }) => Promise<Hooks>

  export interface PluginModule {
    id?: string
    server: Plugin
  }
}

// opencode 2's plugin package.
declare module "@opencode/plugin" {
  type Content =
    | { type: "text"; text: string }
    | { type: "file"; uri: string; mime: string; name?: string }

  interface ToolResult {
    readonly output?: unknown
    readonly content?: string | ReadonlyArray<Content>
    readonly metadata?: Readonly<Record<string, unknown>>
  }

  interface ToolHooks {
    "execute.after": { readonly tool: string; readonly input: unknown } & (
      | { readonly status: "completed"; result: ToolResult }
      | { readonly status: "error"; error: unknown }
    )
  }

  interface Context {
    readonly location: { readonly directory: string }
    readonly tool: {
      hook<Name extends keyof ToolHooks>(
        name: Name,
        callback: (event: ToolHooks[Name]) => Promise<void> | void,
      ): Promise<{ dispose(): Promise<void> }>
    }
  }

  export namespace Plugin {
    interface Plugin {
      readonly id: string
      readonly setup: (
        context: Context,
      ) => Promise<(() => Promise<void> | void) | void>
    }
  }
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
  export function execFile(
    file: string,
    args: readonly string[],
    options: { cwd: string },
    callback: (
      error: { code?: string | number | null } | null,
      stdout: string,
    ) => void,
  ): unknown

  export function spawnSync(
    command: string,
    args: readonly string[],
    options: { encoding: "utf8" },
  ): { status: number | null; stdout: string | null }
}
