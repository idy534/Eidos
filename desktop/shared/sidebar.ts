import { Ajv2020 } from "ajv/dist/2020.js";
import { sidebarSchema } from "./sidebar-schema.generated.js";
import type { SidebarPinsResponse, SidebarSetPinnedRequest } from "./sidebar.generated.js";

export type { SidebarPinsResponse, SidebarSetPinnedRequest } from "./sidebar.generated.js";
export type SidebarTarget = SidebarSetPinnedRequest["target"];

const ajv = new Ajv2020({ strict: false, coerceTypes: false });
const response = ajv.compile<SidebarPinsResponse>({
  $defs: sidebarSchema.$defs, $ref: "#/$defs/SidebarPinsResponse",
});
const request = ajv.compile<SidebarSetPinnedRequest>({
  $defs: sidebarSchema.$defs, $ref: "#/$defs/SidebarSetPinnedRequest",
});

export function isSidebarPinsResponse(value: unknown): value is SidebarPinsResponse {
  return response(value);
}

export function isSidebarSetPinnedRequest(value: unknown): value is SidebarSetPinnedRequest {
  return request(value);
}
