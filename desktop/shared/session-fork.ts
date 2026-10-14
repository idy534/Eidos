import { Ajv2020 } from "ajv/dist/2020.js";
import { sessionForkSchema } from "./session-fork-schema.generated.js";
import type { InheritedHistoryEntry, SessionForkRequest, SessionForkResponse, SessionForkSource } from "./session-fork.generated.js";

export type { InheritedHistoryEntry, SessionForkRequest, SessionForkResponse, SessionForkSource } from "./session-fork.generated.js";

const ajv = new Ajv2020({ strict: false, coerceTypes: false });
const validator = <T>(name: string) => ajv.compile<T>({ $defs: sessionForkSchema.$defs, $ref: `#/$defs/${name}` });
export const isSessionForkRequest = validator<SessionForkRequest>("SessionForkRequest");
export const isSessionForkResponse = validator<SessionForkResponse>("SessionForkResponse");
export const isSessionForkSource = validator<SessionForkSource>("SessionForkSource");
export const isInheritedHistoryEntry = validator<InheritedHistoryEntry>("InheritedHistoryEntry");
