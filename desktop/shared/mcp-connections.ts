import { Ajv2020 } from "ajv/dist/2020.js";
import { mcpConnectionsSchema } from "./mcp-connections-schema.generated.js";
import type { McpConnectionRequest, McpConnectionState, BuiltinPluginRequest, BuiltinPluginsResponse, RemoteMcpInfo } from "./mcp-connections.generated.js";
export type * from "./mcp-connections.generated.js";

const ajv = new Ajv2020({ strict: false, coerceTypes: false });
ajv.addFormat("password", true);
const ref = (name: string) => ({ $defs: mcpConnectionsSchema.$defs, $ref: `#/$defs/${name}` });
export const isMcpConnectionRequest = ajv.compile<McpConnectionRequest>(ref("McpConnectionRequest"));
export const isMcpConnectionState = ajv.compile<McpConnectionState>(ref("McpConnectionState"));
export const isBuiltinPluginRequest = ajv.compile<BuiltinPluginRequest>(ref("BuiltinPluginRequest"));
export const isBuiltinPluginsResponse = ajv.compile<BuiltinPluginsResponse>(ref("BuiltinPluginsResponse"));
export const isRemoteMcpInfo = ajv.compile<RemoteMcpInfo>(ref("RemoteMcpInfo"));
