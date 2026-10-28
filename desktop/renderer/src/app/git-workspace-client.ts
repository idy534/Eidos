import type { EidosRuntimeAPI } from "../../../shared/ipc-api.js";
import type { GitDiffScope, ReviewCommentCreateInput } from "../contracts.js";

/** Bind UI calls to one root; Runtime still resolves and verifies the checkout. */
export function gitWorkspaceClient(rootId: string): EidosRuntimeAPI {
  const client = window.eidosRuntime;
  return {
    ...client,
    openWorkspacePathInEditor: (sessionId: string, path: string) => client.openWorkspacePathInEditor(sessionId, path, rootId),
    listReviewComments: (sessionId: string, path?: string, scope?: GitDiffScope) => client.repositoryGit({ rootId, method: "review/listComments", parameters: { sessionId, ...(path && scope ? { path, scope } : {}) } }).then((result) => "comments" in result ? result.comments : []),
    createReviewComment: (sessionId: string, input: ReviewCommentCreateInput, operationId: string) => client.repositoryGit({ rootId, method: "review/createComment", parameters: { sessionId, operationId, ...input } }).then((result) => { if (!("comment" in result)) throw new Error("评论响应无效。"); return result.comment; }),
    deleteReviewComment: (sessionId: string, commentId: string, operationId: string) => client.repositoryGit({ rootId, method: "review/deleteComment", parameters: { sessionId, commentId, operationId } }).then((result) => { if (!("commentId" in result)) throw new Error("评论响应无效。"); return result.commentId; }),
    readSessionGitStatus: (sessionId: string) => client.repositoryGit({rootId, method: "session/gitStatus", parameters: { sessionId } }) as ReturnType<EidosRuntimeAPI["readSessionGitStatus"]>,
    readSessionGitDiff: (sessionId: string, scope: GitDiffScope, path?: string, compareRef?: string) => client.repositoryGit({rootId, method: "session/gitDiff", parameters: { sessionId, scope, ...(path ? {path} : {}), ...(compareRef ? {compareRef} : {}) } }) as ReturnType<EidosRuntimeAPI["readSessionGitDiff"]>,
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
