import { Ajv2020 } from "ajv/dist/2020.js";
import { webSearchSchema } from "./web-search-schema.generated.js";
import type { WebSearchReadRequest, WebSearchSaveRequest, WebSearchResponse, WebSearchOfficialPageRequest } from "./web-search.generated.js";
export type * from "./web-search.generated.js";

const ajv = new Ajv2020({ strict: false, coerceTypes: false });
const validator = <T>(name: string) => ajv.compile<T>({ $defs: webSearchSchema.$defs, $ref: `#/$defs/${name}` });
export const isWebSearchReadRequest = validator<WebSearchReadRequest>("WebSearchReadRequest");
export const isWebSearchSaveRequest = validator<WebSearchSaveRequest>("WebSearchSaveRequest");
export const isWebSearchResponse = validator<WebSearchResponse>("WebSearchResponse");
export const isWebSearchOfficialPageRequest = validator<WebSearchOfficialPageRequest>("WebSearchOfficialPageRequest");
