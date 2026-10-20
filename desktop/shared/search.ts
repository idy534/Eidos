import { Ajv2020 } from "ajv/dist/2020.js";
import { searchSchema } from "./search-schema.generated.js";
import type { SearchRequest, SearchResponse, ReadAroundRequest, FileFindRequest, FileFindResponse } from "./search.generated.js";
export type { SearchRequest, SearchResponse, SearchHit, ReadAroundRequest, FileFindRequest } from "./search.generated.js";

const ajv = new Ajv2020({ strict: false, coerceTypes: false });
const validator = <T>(name: string) => ajv.compile<T>({ $defs: searchSchema.$defs, $ref: `#/$defs/${name}` });
export const isSearchRequest = validator<SearchRequest>("SearchRequest");
export const isSearchResponse = validator<SearchResponse>("SearchResponse");
export const isFileFindRequest = validator<FileFindRequest>("FileFindRequest");
export const isFileFindResponse = validator<FileFindResponse>("FileFindResponse");
export const isReadAroundRequest = validator<ReadAroundRequest>("ReadAroundRequest");
