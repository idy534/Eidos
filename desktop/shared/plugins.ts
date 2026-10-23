import { Ajv2020 } from "ajv/dist/2020.js";
import { pluginPresentationSchema } from "./plugins-schema.generated.js";
import type { PluginPresentation } from "./plugins.generated.js";
export type * from "./plugins.generated.js";

const ajv = new Ajv2020({ strict: false, coerceTypes: false });
export const isPluginPresentation = ajv.compile<PluginPresentation>(pluginPresentationSchema);
