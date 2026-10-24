import { Ajv2020 } from "ajv/dist/2020.js";
import { hookSchema } from "./hooks-schema.generated.js";
import type { PluginHookReview, PluginCompatibility, HookActivity, HookActivityPage, HookSourceList, HookConfigurationRequest, HookHistoryRequest } from "./hooks.generated.js";
export type * from "./hooks.generated.js";

const ajv = new Ajv2020({ strict: false, coerceTypes: false });
export const isPluginHookReview = ajv.compile<PluginHookReview>({ $defs: hookSchema.$defs, $ref: "#/$defs/PluginHookReview" });
export const isPluginCompatibility = ajv.compile<PluginCompatibility>({ $defs: hookSchema.$defs, $ref: "#/$defs/PluginCompatibility" });
export const isHookActivity = ajv.compile<HookActivity>({ $defs: hookSchema.$defs, $ref: "#/$defs/HookActivity" });
export const isHookActivityPage = ajv.compile<HookActivityPage>({ $defs: hookSchema.$defs, $ref: "#/$defs/HookActivityPage" });
export const isHookSourceList = ajv.compile<HookSourceList>({ $defs: hookSchema.$defs, $ref: "#/$defs/HookSourceList" });
export const isHookConfigurationRequest = ajv.compile<HookConfigurationRequest>({ $defs: hookSchema.$defs, $ref: "#/$defs/HookConfigurationRequest" });
export const isHookHistoryRequest = ajv.compile<HookHistoryRequest>({ $defs: hookSchema.$defs, $ref: "#/$defs/HookHistoryRequest" });
