import type { EidosRuntimeAPI } from "../../../shared/ipc-api.js";
import type { ProjectRootDto } from "../../../shared/project-workspaces.js";
import type { GitDiffScope, ReviewCommentCreateInput } from "../contracts.js";

/** Bind UI calls to one root; Runtime still resolves and verifies the checkout. */
export function gitWorkspaceClient(rootId: string, checkout?: { roots: readonly ProjectRootDto[]; repositoryPath: string }, projectId?: string): EidosRuntimeAPI {
  const client = window.eidosRuntime;
  const draftBinding = (sessionId: string) => sessionId.startsWith("draft-") && projectId ? { projectId } : {};
  return {
    ...client,
    openWorkspacePathInEditor: async (sessionId: string, path: string) => {
      const target = checkout ? gitFileTarget(path, checkout.repositoryPath, checkout.roots) : { path, rootId };
      if (!target) throw new Error("这个文件不在项目添加的文件夹中。");
      await client.openWorkspacePathInEditor(sessionId, target.path, target.rootId);
    },
    listReviewComments: (sessionId: string, path?: string, scope?: GitDiffScope) => sessionId.startsWith("draft-") ? Promise.resolve([]) : client.repositoryGit({ rootId, method: "review/listComments", parameters: { sessionId, ...(path && scope ? { path, scope } : {}) } }).then((result) => "comments" in result ? result.comments : []),
    createReviewComment: (sessionId: string, input: ReviewCommentCreateInput, operationId: string) => client.repositoryGit({ rootId, method: "review/createComment", parameters: { sessionId, operationId, ...input } }).then((result) => { if (!("comment" in result)) throw new Error("评论响应无效。"); return result.comment; }),
    deleteReviewComment: (sessionId: string, commentId: string, operationId: string) => client.repositoryGit({ rootId, method: "review/deleteComment", parameters: { sessionId, commentId, operationId } }).then((result) => { if (!("commentId" in result)) throw new Error("评论响应无效。"); return result.commentId; }),
    readSessionGitStatus: (sessionId: string) => client.repositoryGit({rootId, method: "session/gitStatus", parameters: { sessionId, ...draftBinding(sessionId) } }) as ReturnType<EidosRuntimeAPI["readSessionGitStatus"]>,
    readSessionGitDiff: (sessionId: string, scope: GitDiffScope, path?: string, compareRef?: string) => client.repositoryGit({rootId, method: "session/gitDiff", parameters: { sessionId, ...draftBinding(sessionId), scope, ...(path ? {path} : {}), ...(compareRef ? {compareRef} : {}) } }) as ReturnType<EidosRuntimeAPI["readSessionGitDiff"]>,
    stageSessionGit: (sessionId: string, paths: string[], operationId: string) => client.repositoryGit({rootId, method: "session/gitStage", parameters: { sessionId, paths: paths as [string, ...string[]], operationId } }) as ReturnType<EidosRuntimeAPI["stageSessionGit"]>,
    commitSessionGit: (sessionId: string, message: string, operationId: string) => client.repositoryGit({rootId, method: "session/gitCommit", parameters: { sessionId, message, operationId } }) as ReturnType<EidosRuntimeAPI["commitSessionGit"]>,
    switchSessionGitBranch: (sessionId: string, branch: string, operationId: string) => client.repositoryGit({rootId, method: "session/gitSwitchBranch", parameters: { sessionId, branch, operationId } }) as ReturnType<EidosRuntimeAPI["switchSessionGitBranch"]>,
    createSessionGitBranch: (sessionId: string, branch: string, operationId: string) => client.repositoryGit({rootId, method: "session/gitCreateBranch", parameters: { sessionId, branch, operationId } }) as ReturnType<EidosRuntimeAPI["createSessionGitBranch"]>,
    readSessionGitRemoteStatus: (sessionId: string) => client.repositoryGit({rootId, method: "session/gitRemoteStatus", parameters: { sessionId } }) as ReturnType<EidosRuntimeAPI["readSessionGitRemoteStatus"]>,
    fetchSessionGit: (sessionId: string, operationId: string, remote?: string) => client.repositoryGit({rootId, method: "session/gitFetch", parameters: { sessionId, operationId, ...(remote ? {remote} : {}) } }) as ReturnType<EidosRuntimeAPI["fetchSessionGit"]>,
    pullSessionGit: (sessionId: string, operationId: string) => client.repositoryGit({rootId, method: "session/gitPull", parameters: { sessionId, operationId } }) as ReturnType<EidosRuntimeAPI["pullSessionGit"]>,
    pushSessionGit: (sessionId: string, operationId: string, remote?: string) => client.repositoryGit({rootId, method: "session/gitPush", parameters: { sessionId, operationId, ...(remote ? {remote} : {}) } }) as ReturnType<EidosRuntimeAPI["pushSessionGit"]>,
    mergeSessionGit: (sessionId: string, target: string, operationId: string) => client.repositoryGit({rootId, method: "session/gitMerge", parameters: { sessionId, target, operationId } }) as ReturnType<EidosRuntimeAPI["mergeSessionGit"]>,
    abortSessionGitMerge: (sessionId: string, operationId: string) => client.repositoryGit({rootId, method: "session/gitMergeAbort", parameters: { sessionId, operationId } }) as ReturnType<EidosRuntimeAPI["abortSessionGitMerge"]>,
    rebaseSessionGit: (sessionId: string, target: string, operationId: string) => client.repositoryGit({rootId, method: "session/gitRebase", parameters: { sessionId, target, operationId } }) as ReturnType<EidosRuntimeAPI["rebaseSessionGit"]>,
    continueSessionGitRebase: (sessionId: string, operationId: string) => client.repositoryGit({rootId, method: "session/gitRebaseContinue", parameters: { sessionId, operationId } }) as ReturnType<EidosRuntimeAPI["continueSessionGitRebase"]>,
    abortSessionGitRebase: (sessionId: string, operationId: string) => client.repositoryGit({rootId, method: "session/gitRebaseAbort", parameters: { sessionId, operationId } }) as ReturnType<EidosRuntimeAPI["abortSessionGitRebase"]>,
  };
}

export function gitFileTarget(path: string, repositoryPath: string, roots: readonly Pick<ProjectRootDto, "id" | "path">[]): { path: string; rootId: string } | undefined {
  if (!path || path.startsWith("/") || path.includes("\0")
    || path.split("/").some(part => !part || part === "." || part === "..")) return undefined;
  const absolute = `${repositoryPath.replace(/\/$/, "")}/${path}`;
  for (const root of [...roots].sort((a, b) => b.path.length - a.path.length)) {
    const prefix = root.path.endsWith("/") ? root.path : `${root.path}/`;
    if (absolute.startsWith(prefix)) return { path: absolute.slice(prefix.length), rootId: root.id };
  }
  return undefined;
}
