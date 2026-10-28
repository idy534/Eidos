from __future__ import annotations

from typing import Literal

from pydantic import Field

from eidos_runtime.protocol.project_workspaces import WorkspaceDto
from eidos_runtime.protocol import methods as dto
from eidos_runtime.protocol import review as review_dto


class GitWorkspaceRequest(WorkspaceDto):
    root_id: str = Field(min_length=1, max_length=256)
    method: Literal[
        "review/listComments", "review/createComment", "review/deleteComment",
        "session/gitStatus", "session/gitDiff", "session/gitStage", "session/gitCommit",
        "session/gitSwitchBranch", "session/gitCreateBranch", "session/gitRemoteStatus",
        "session/gitFetch", "session/gitPull", "session/gitPush", "session/gitMerge",
        "session/gitMergeAbort", "session/gitRebase", "session/gitRebaseContinue", "session/gitRebaseAbort",
    ]
    parameters: (
        review_dto.ReviewCommentListRequestDto | review_dto.ReviewCommentCreateRequestDto | review_dto.ReviewCommentDeleteRequestDto
        | dto.SessionGitStatusRequestDto | dto.SessionGitDiffRequestDto | dto.SessionGitStageRequestDto
        | dto.SessionGitCommitRequestDto | dto.SessionGitSwitchBranchRequestDto | dto.SessionGitCreateBranchRequestDto
        | dto.SessionGitRemoteStatusRequestDto | dto.SessionGitFetchRequestDto | dto.SessionGitPullRequestDto
        | dto.SessionGitPushRequestDto | dto.SessionGitMergeRequestDto | dto.SessionGitMergeAbortRequestDto
        | dto.SessionGitRebaseRequestDto | dto.SessionGitRebaseContinueRequestDto | dto.SessionGitRebaseAbortRequestDto
    )


class GitWorkspaceContract(WorkspaceDto):
    request: GitWorkspaceRequest
    response: (
        review_dto.ReviewCommentListResponseDto | review_dto.ReviewCommentCreateResponseDto | review_dto.ReviewCommentDeleteResponseDto
        | dto.SessionGitStatusResponseDto | dto.SessionGitDiffResponseDto | dto.SessionGitStageResponseDto
        | dto.SessionGitCommitResponseDto | dto.SessionGitSwitchBranchResponseDto | dto.SessionGitCreateBranchResponseDto
        | dto.SessionGitRemoteStatusResponseDto | dto.SessionGitFetchResponseDto | dto.SessionGitPullResponseDto
        | dto.SessionGitPushResponseDto | dto.SessionGitMergeResponseDto | dto.SessionGitMergeAbortResponseDto
        | dto.SessionGitRebaseResponseDto | dto.SessionGitRebaseContinueResponseDto | dto.SessionGitRebaseAbortResponseDto
    )
