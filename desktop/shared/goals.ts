import type { ValidateFunction } from "ajv";
import * as validators from "./goals-validators.generated.js";
import type { GoalRequest, GoalView, GoalChangedNotice, GoalRunOrigin } from "./goals.generated.js";
export type * from "./goals.generated.js";
export const isGoalRequest = validators.isGoalRequest as ValidateFunction<GoalRequest>;
export const isGoalView = validators.isGoalView as ValidateFunction<GoalView>;
export const isGoalChangedNotice = validators.isGoalChangedNotice as ValidateFunction<GoalChangedNotice>;
export const isGoalRunOrigin = validators.isGoalRunOrigin as ValidateFunction<GoalRunOrigin>;
