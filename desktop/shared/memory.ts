import { Ajv2020 } from "ajv/dist/2020.js";
import { memorySchema, memoryMethodModels } from "./memory-schema.generated.js";
import type { MemoryMethods } from "./memory-methods.generated.js";
export type { MemoryMethods } from "./memory-methods.generated.js";

const ajv = new Ajv2020({strict: false, allErrors: false, coerceTypes: false});
const validators = new Map(Object.keys(memorySchema.$defs).map((name) => [name, ajv.compile({
  $defs: memorySchema.$defs, $ref: `#/$defs/${name}`,
})]));

export function isMemoryMethod(value: unknown): value is keyof MemoryMethods {
  return typeof value === "string" && Object.hasOwn(memoryMethodModels, value);
}

export function isMemoryRequest<K extends keyof MemoryMethods>(method: K, value: unknown): value is MemoryMethods[K]["request"] {
  return validators.get(memoryMethodModels[method][0])?.(value) === true;
}

export function isMemoryResponse<K extends keyof MemoryMethods>(method: K, value: unknown): value is MemoryMethods[K]["response"] {
  return validators.get(memoryMethodModels[method][1])?.(value) === true;
}
