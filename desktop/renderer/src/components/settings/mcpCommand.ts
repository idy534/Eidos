import type { McpServerRecord } from "../../contracts.js";

/** The bundled browser server starts from Eidos-managed resources, not a user command. */
export function isBundledBrowserServer(server: Pick<McpServerRecord, "permissionProfile">): boolean {
  return server.permissionProfile === "browser";
}

export function mcpCommandLabel(server: Pick<McpServerRecord, "executable" | "argv" | "permissionProfile" | "remote">): string {
  if (server.remote?.url) return server.remote.url;
  if (isBundledBrowserServer(server)) return "Eidos 托管 Node · Playwright MCP";
  return [server.executable, ...server.argv].filter(Boolean).join(" ");
}
