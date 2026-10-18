import type { ContextDetails, ContextCompactionRecord, ContextCompactionActivity } from "./context.generated.js";
import { isContextDetails as validateDetails, isContextCompactionRecord as validateRecord, isContextCompactionActivity as validateActivity } from "./context-validators.generated.js";
export type * from "./context.generated.js";
export function isContextDetails(value: unknown): value is ContextDetails { return validateDetails(value); }
export function isContextCompactionRecord(value: unknown): value is ContextCompactionRecord { return validateRecord(value); }
export function isContextCompactionActivity(value: unknown): value is ContextCompactionActivity { return validateActivity(value); }
