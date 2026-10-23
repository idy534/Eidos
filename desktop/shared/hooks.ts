import { Ajv2020 } from "ajv/dist/2020.js";
import { hookSchema } from "./hooks-schema.generated.js";
import type { PluginHookReview, PluginCompatibility } from "./hooks.generated.js";
export type * from "./hooks.generated.js";

const ajv = new Ajv2020({ strict: false, coerceTypes: false });
export const isPluginHookReview = ajv.compile<PluginHookReview>({ $defs: hookSchema.$defs, $ref: "#/$defs/PluginHookReview" });
export const isPluginCompatibility = ajv.compile<PluginCompatibility>({ $defs: hookSchema.$defs, $ref: "#/$defs/PluginCompatibility" });
