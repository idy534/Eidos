import { Ajv2020 } from "ajv/dist/2020.js";
import { guardrailsSchema } from "./guardrails-schema.generated.js";
import type { GuardrailsReadRequest, GuardrailsSaveRequest, GuardrailsResponse } from "./guardrails.generated.js";
export type * from "./guardrails.generated.js";

const ajv = new Ajv2020({ strict: false, coerceTypes: false });
const validator = <T>(name: string) => ajv.compile<T>({ $defs: guardrailsSchema.$defs, $ref: `#/$defs/${name}` });
export const isGuardrailsReadRequest = validator<GuardrailsReadRequest>("GuardrailsReadRequest");
export const isGuardrailsSaveRequest = validator<GuardrailsSaveRequest>("GuardrailsSaveRequest");
export const isGuardrailsResponse = validator<GuardrailsResponse>("GuardrailsResponse");
