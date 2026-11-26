import { describe, expect, it } from "vitest";
import { hookCommandText } from "./hook-command.js";

describe("hookCommandText", () => {
  it("shows the Windows command next to the POSIX command it replaces on Windows", () => {
    expect(hookCommandText({ handlerType: "command", command: "./check.sh", commandWindows: "pwsh -File check.ps1" }))
      .toBe("macOS：./check.sh\nWindows：pwsh -File check.ps1");
  });

  it("shows a Windows-only command instead of an empty handler label", () => {
    expect(hookCommandText({ handlerType: "command", command: "", commandWindows: "check.cmd" })).toBe("Windows：check.cmd");
  });

  it("keeps single-platform and MCP hooks unchanged", () => {
    expect(hookCommandText({ handlerType: "command", command: "./check.sh", commandWindows: null })).toBe("./check.sh");
    expect(hookCommandText({ handlerType: "command", command: "same", commandWindows: "same" })).toBe("same");
    expect(hookCommandText({ handlerType: "prompt", command: "" }, "（不执行）")).toBe("处理器：prompt（不执行）");
    expect(hookCommandText({ handlerType: "mcp_tool", mcpServer: "s", mcpTool: "t", mcpInput: {} }))
      .toBe(JSON.stringify({ server: "s", tool: "t", input: {} }, null, 2));
  });
});
