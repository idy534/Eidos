import { Ajv2020 } from "ajv/dist/2020.js";
import { queuedRunSchema } from "./queued-runs-schema.generated.js";
import type { QueuedRunChangeResponse, QueuedRunDeleteRequest, QueuedRunSteerRequest, QueuedRunUpdateRequest, QueuedRunListRequest, QueuedRunListResponse } from "./queued-runs.generated.js";

export type { QueuedRunChangeResponse, QueuedRunDeleteRequest, QueuedRunSteerRequest, QueuedRunUpdateRequest, QueuedRunListRequest, QueuedRunListResponse } from "./queued-runs.generated.js";
export type QueuedMessage = QueuedRunListResponse["items"][number];
export type QueuedRunChange = QueuedRunChangeResponse["change"];

const ajv = new Ajv2020({ strict: false, coerceTypes: false });
export const isQueuedRunChangeResponse = ajv.compile<QueuedRunChangeResponse>({
  $defs: queuedRunSchema.$defs, $ref: "#/$defs/QueuedRunChangeResponse",
});
export const isQueuedRunDeleteRequest = ajv.compile<QueuedRunDeleteRequest>({
  $defs: queuedRunSchema.$defs, $ref: "#/$defs/QueuedRunDeleteRequest",
});
export const isQueuedRunUpdateRequest = ajv.compile<QueuedRunUpdateRequest>({
  $defs: queuedRunSchema.$defs, $ref: "#/$defs/QueuedRunUpdateRequest",
});
export const isQueuedRunListRequest = ajv.compile<QueuedRunListRequest>({
  $defs: queuedRunSchema.$defs, $ref: "#/$defs/QueuedRunListRequest",
});
export const isQueuedRunListResponse = ajv.compile<QueuedRunListResponse>({
  $defs: queuedRunSchema.$defs, $ref: "#/$defs/QueuedRunListResponse",
});

export const isQueuedRunSteerRequest = ajv.compile<QueuedRunSteerRequest>({
  $defs: queuedRunSchema.$defs, $ref: "#/$defs/QueuedRunSteerRequest",
});
