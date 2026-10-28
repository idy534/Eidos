import { Ajv2020 } from "ajv/dist/2020.js";
import { personalizationSchema } from "./personalization-schema.generated.js";
import type { PersonalizationReadRequest, PersonalizationSaveRequest, PersonalizationResponse } from "./personalization.generated.js";
export type * from "./personalization.generated.js";

const ajv = new Ajv2020({ strict: false, coerceTypes: false });
const validator = <T>(name: string) => ajv.compile<T>({ $defs: personalizationSchema.$defs, $ref: `#/$defs/${name}` });
export const isPersonalizationReadRequest = validator<PersonalizationReadRequest>("PersonalizationReadRequest");
export const isPersonalizationSaveRequest = validator<PersonalizationSaveRequest>("PersonalizationSaveRequest");
export const isPersonalizationResponse = validator<PersonalizationResponse>("PersonalizationResponse");
