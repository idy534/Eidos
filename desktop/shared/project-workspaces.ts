import { Ajv2020 } from "ajv/dist/2020.js";
import { projectWorkspacesSchema } from "./project-workspaces-schema.generated.js";
import type { ProjectDto, ProjectReadRequest, ProjectRootDto, ProjectUpdateRequest, ProjectCreateFoldersRequest, GitWorkspaceRequest, GitWorkspaceContract } from "./project-workspaces.generated.js";

export type { ProjectReadRequest, ProjectRootDto, ProjectUpdateRequest, ProjectCreateFoldersRequest, GitWorkspaceRequest, GitWorkspaceContract } from "./project-workspaces.generated.js";
const ajv = new Ajv2020({ strict: false, coerceTypes: false });
const project = ajv.compile<ProjectDto>({ $defs: projectWorkspacesSchema.$defs, $ref: "#/$defs/ProjectDto" });
const read = ajv.compile<ProjectReadRequest>({ $defs: projectWorkspacesSchema.$defs, $ref: "#/$defs/ProjectReadRequest" });
const update = ajv.compile<ProjectUpdateRequest>({ $defs: projectWorkspacesSchema.$defs, $ref: "#/$defs/ProjectUpdateRequest" });
const root = ajv.compile<ProjectRootDto>({ $defs: projectWorkspacesSchema.$defs, $ref: "#/$defs/ProjectRootDto" });
export const isProjectRoot = (value: unknown): value is ProjectRootDto => root(value);
const git = ajv.compile<GitWorkspaceRequest>({ $defs: projectWorkspacesSchema.$defs, $ref: "#/$defs/GitWorkspaceRequest" });
export const isGitWorkspaceRequest = (value: unknown): value is GitWorkspaceRequest => git(value);
const create = ajv.compile<ProjectCreateFoldersRequest>({ $defs: projectWorkspacesSchema.$defs, $ref: "#/$defs/ProjectCreateFoldersRequest" });
export const isProjectCreateFoldersRequest = (value: unknown): value is ProjectCreateFoldersRequest => create(value);
export const isProjectWorkspace = (value: unknown): value is ProjectDto => project(value);
export const isProjectReadRequest = (value: unknown): value is ProjectReadRequest => read(value);
export const isProjectUpdateRequest = (value: unknown): value is ProjectUpdateRequest => update(value);
export type ProjectFolders = ProjectRootDto[];
