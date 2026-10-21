import { Ajv2020 } from "ajv/dist/2020.js";
import { shellEnvironmentSchema } from "./shell-environment-schema.generated.js";
import type { ShellEnvironmentReadRequest, ShellEnvironmentRefreshRequest, ShellEnvironmentSaveRequest, ShellEnvironmentResponse } from "./shell-environment.generated.js";
export type * from "./shell-environment.generated.js";

const ajv = new Ajv2020({ strict: false, coerceTypes: false });
const validator = <T>(name: string) => ajv.compile<T>({ $defs: shellEnvironmentSchema.$defs, $ref: `#/$defs/${name}` });
export const isShellEnvironmentReadRequest = validator<ShellEnvironmentReadRequest>("ShellEnvironmentReadRequest");
export const isShellEnvironmentSaveRequest = validator<ShellEnvironmentSaveRequest>("ShellEnvironmentSaveRequest");
export const isShellEnvironmentResponse = validator<ShellEnvironmentResponse>("ShellEnvironmentResponse");

export const isShellEnvironmentRefreshRequest = validator<ShellEnvironmentRefreshRequest>("ShellEnvironmentRefreshRequest");
