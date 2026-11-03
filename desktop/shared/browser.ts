import { Ajv2020 } from "ajv/dist/2020.js";
import { browserSchema } from "./browser-schema.generated.js";
import type {
  BrowserClearResponse, BrowserCloseResponse, BrowserGrantRevokeRequest, BrowserGrantRevokeResponse,
  BrowserOpenResponse, BrowserStatus, BrowserStatusRequest, BrowserTargetRequest, BrowserTargetResponse,
} from "./browser.generated.js";
export type * from "./browser.generated.js";

const ajv = new Ajv2020({ strict: false, coerceTypes: false });
const compile = <T>(name: string) => ajv.compile<T>({ $defs: browserSchema.$defs, $ref: `#/$defs/${name}` });
export const isBrowserStatusRequest = compile<BrowserStatusRequest>("BrowserStatusRequest");
export const isBrowserStatus = compile<BrowserStatus>("BrowserStatus");
export const isBrowserGrantRevokeRequest = compile<BrowserGrantRevokeRequest>("BrowserGrantRevokeRequest");
export const isBrowserGrantRevokeResponse = compile<BrowserGrantRevokeResponse>("BrowserGrantRevokeResponse");
export const isBrowserCloseResponse = compile<BrowserCloseResponse>("BrowserCloseResponse");
export const isBrowserOpenResponse = compile<BrowserOpenResponse>("BrowserOpenResponse");
export const isBrowserClearResponse = compile<BrowserClearResponse>("BrowserClearResponse");
export const isBrowserTargetRequest = compile<BrowserTargetRequest>("BrowserTargetRequest");
export const isBrowserTargetResponse = compile<BrowserTargetResponse>("BrowserTargetResponse");
