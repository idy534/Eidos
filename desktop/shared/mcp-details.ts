import { Ajv2020 } from "ajv/dist/2020.js";
import { mcpDetailsSchema } from "./mcp-details-schema.generated.js";
import type { McpToolsRequest, McpToolsResponse } from "./mcp-details.generated.js";
export type * from "./mcp-details.generated.js";

const ajv = new Ajv2020({ strict: false, coerceTypes: false });
export const isMcpToolsRequest = ajv.compile<McpToolsRequest>({ $defs: mcpDetailsSchema.$defs, $ref: "#/$defs/McpToolsRequest" });
export const isMcpToolsResponse = ajv.compile<McpToolsResponse>({ $defs: mcpDetailsSchema.$defs, $ref: "#/$defs/McpToolsResponse" });
