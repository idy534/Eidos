import { describe, expect, it } from "vitest";
import { mcpCommandLabel } from "./mcpCommand.js";

describe("MCP command label", () => {
  it("names the managed runtime for the bundled browser instead of launch placeholders", () => {
    expect(mcpCommandLabel({
      executable: "${RUNTIME_NODE}", argv: ["${BROWSER_MCP_ENTRY}", "--cdp-endpoint", "${BROWSER_CDP_ENDPOINT}"],
      permissionProfile: "browser",
    })).toBe("Eidos 托管 Node · Playwright MCP");
  });

  it("keeps ordinary commands and remote URLs as they are", () => {
    expect(mcpCommandLabel({ executable: "npx", argv: ["@playwright/mcp@latest"], permissionProfile: "connector" }))
      .toBe("npx @playwright/mcp@latest");
    expect(mcpCommandLabel({ executable: "", argv: [], permissionProfile: "connector", remote: { url: "https://example.com/mcp" } as never }))
      .toBe("https://example.com/mcp");
  });
});
