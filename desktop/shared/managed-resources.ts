import { Ajv2020 } from "ajv/dist/2020.js";
import { managedResourcesSchema } from "./managed-resources-schema.generated.js";
import type { ResourceStatus, ResourceRetryRequest, ResourceRetryResponse } from "./managed-resources.generated.js";
export type * from "./managed-resources.generated.js";

const ajv = new Ajv2020({ strict: false, coerceTypes: false });
const validator = <T>(name: string) => ajv.compile<T>({ $defs: managedResourcesSchema.$defs, $ref: `#/$defs/${name}` });
export const isResourceStatus = validator<ResourceStatus>("ResourceStatus");
export const isResourceRetryRequest = validator<ResourceRetryRequest>("ResourceRetryRequest");
export const isResourceRetryResponse = validator<ResourceRetryResponse>("ResourceRetryResponse");
