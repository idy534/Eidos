import { qoderSchema } from "./qoder-schema.generated.js";
import type { QoderModelSettings, QoderConnectionRequest, QoderConnectionState } from "./qoder.generated.js";
import { isQoderModelSettings as settings, isQoderConnectionRequest as request, isQoderConnectionState as state } from "./qoder-validators.generated.js";
export type * from "./qoder.generated.js";
export function isQoderModelSettings(value: unknown): value is QoderModelSettings { return settings(value); }
export function isQoderConnectionRequest(value: unknown): value is QoderConnectionRequest { return request(value); }
export function isQoderConnectionState(value: unknown): value is QoderConnectionState { return state(value); }

// Read numeric bounds from the generated Python schema so the form and RPC
// validator keep the same limits.
const limits = qoderSchema.$defs.QoderModelSettings.properties;
export const QODER_TOKEN_LIMITS = {
  contextMin: limits.contextWindowTokens.exclusiveMinimum + 1,
  contextMax: limits.contextWindowTokens.maximum,
  outputMin: limits.maxOutputTokens.exclusiveMinimum + 1,
  outputMax: limits.maxOutputTokens.maximum,
};
