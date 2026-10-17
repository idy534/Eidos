import { Ajv2020 } from "ajv/dist/2020.js";
import { agentDefinitionsSchema } from "./agent-definitions-schema.generated.js";
import type { AgentDefinitionDocument, AgentDefinitionSaveRequest, AgentDefinitionRemoveRequest } from "./collaboration.generated.js";

const ajv = new Ajv2020({ strict: false, coerceTypes: false });
const validator = <T>(name: string) => ajv.compile<T>({ $defs: agentDefinitionsSchema.$defs, $ref: `#/$defs/${name}` });
export const isAgentDefinitionDocument = validator<AgentDefinitionDocument>("AgentDefinitionDocument");
export const isAgentDefinitionSaveRequest = validator<AgentDefinitionSaveRequest>("AgentDefinitionSaveRequest");
export const isAgentDefinitionRemoveRequest = validator<AgentDefinitionRemoveRequest>("AgentDefinitionRemoveRequest");
