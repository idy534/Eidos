import type { ValidateFunction } from "ajv";
import * as validators from "./automation-validators.generated.js";
import type { AutomationExecuteRequest, AutomationResponse, AutomationHistoryRequest, AutomationRunNowRequest, AutomationOrigin } from "./automation.generated.js";
export type * from "./automation.generated.js";

export const isAutomationRequest = validators.isAutomationRequest as ValidateFunction<AutomationExecuteRequest>;
export const isAutomationResponse = validators.isAutomationResponse as ValidateFunction<AutomationResponse>;
export const isAutomationHistoryRequest = validators.isAutomationHistoryRequest as ValidateFunction<AutomationHistoryRequest>;
export const isAutomationRunNowRequest = validators.isAutomationRunNowRequest as ValidateFunction<AutomationRunNowRequest>;
export const isAutomationOrigin = validators.isAutomationOrigin as ValidateFunction<AutomationOrigin>;
