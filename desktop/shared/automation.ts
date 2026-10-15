import { Ajv2020 } from "ajv/dist/2020.js";
import { automationSchema } from "./automation-schema.generated.js";
import type { AutomationExecuteRequest, AutomationResponse, AutomationHistoryRequest, AutomationRunNowRequest } from "./automation.generated.js";
export type * from "./automation.generated.js";

const ajv = new Ajv2020({ strict: false, coerceTypes: false });
export const isAutomationRequest = ajv.compile<AutomationExecuteRequest>({ $defs: automationSchema.$defs, $ref: "#/$defs/AutomationExecuteRequest" });
export const isAutomationResponse = ajv.compile<AutomationResponse>({ $defs: automationSchema.$defs, $ref: "#/$defs/AutomationResponse" });
export const isAutomationHistoryRequest = ajv.compile<AutomationHistoryRequest>({ $defs: automationSchema.$defs, $ref: "#/$defs/AutomationHistoryRequest" });
export const isAutomationRunNowRequest = ajv.compile<AutomationRunNowRequest>({ $defs: automationSchema.$defs, $ref: "#/$defs/AutomationRunNowRequest" });
export const isAutomationOrigin = ajv.compile({ $defs: automationSchema.$defs, $ref: "#/$defs/AutomationOrigin" });
