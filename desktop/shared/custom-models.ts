import type { CustomModelSettings, CustomProviderResult } from "./custom-models.generated.js";
import { isCustomModelSettings as validateSettings, isCustomProviderResult as validateResult } from "./custom-models-validators.generated.js";
export type * from "./custom-models.generated.js";
export function isCustomModelSettings(value: unknown): value is CustomModelSettings { return validateSettings(value); }
export function isCustomProviderResult(value: unknown): value is CustomProviderResult { return validateResult(value); }
