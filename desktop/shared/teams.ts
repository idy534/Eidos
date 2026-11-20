import type { ValidateFunction } from "ajv";
import * as validators from "./teams-validators.generated.js";
import { teamMethodModels, type TeamMethods } from "./teams-methods.generated.js";
import type { TeamChangedNotice, TeamRunOrigin } from "./teams.generated.js";
export type * from "./teams.generated.js";
export type { TeamMethods } from "./teams-methods.generated.js";

const byModel = validators as unknown as Record<string, ValidateFunction | undefined>;

export function isTeamMethod(value: unknown): value is keyof TeamMethods {
  return typeof value === "string" && Object.hasOwn(teamMethodModels, value);
}

export function isTeamRequest<K extends keyof TeamMethods>(method: K, value: unknown): value is TeamMethods[K]["request"] {
  return byModel[`is${teamMethodModels[method][0]}`]?.(value) === true;
}

export function isTeamResponse<K extends keyof TeamMethods>(method: K, value: unknown): value is TeamMethods[K]["response"] {
  return byModel[`is${teamMethodModels[method][1]}`]?.(value) === true;
}

export const isTeamChangedNotice = validators.isTeamChangedNotice as ValidateFunction<TeamChangedNotice>;
export const isTeamRunOrigin = validators.isTeamRunOrigin as ValidateFunction<TeamRunOrigin>;
