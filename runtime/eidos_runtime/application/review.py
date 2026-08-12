from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Protocol, TypeVar

from pydantic import ValidationError

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.db.errors import (
    InvalidRunStateError,
    OperationConflictError,
    OperationInProgressError,
    ResourceNotFoundError,
)
from eidos_runtime.persistence.review_comments import ReviewCommentRepository
from eidos_runtime.protocol.methods import (
    MethodResultDto,
    SessionGitDiffRequestDto,
    SessionGitDiffResponseDto,
)
from eidos_runtime.protocol.review import (
    ReviewCommentCreateRequestDto,
    ReviewCommentCreateResponseDto,
    ReviewCommentDeleteRequestDto,
    ReviewCommentDeleteResponseDto,
    ReviewCommentListRequestDto,
    ReviewCommentListResponseDto,
)


class ReviewGitPort(Protocol):
    def git_diff(self, request: SessionGitDiffRequestDto) -> SessionGitDiffResponseDto: ...


ResultDtoT = TypeVar("ResultDtoT", bound=MethodResultDto)


class ReviewApplication:
    """Owns inline comment anchors while native Git owns Diff facts."""

    def __init__(
        self,
        repository: ReviewCommentRepository,
        *,
        git: ReviewGitPort,
        scan_text: Callable[[str], str],
    ) -> None:
        self._repository = repository
        self._git = git
        self._scan_text = scan_text

    def list_comments(
        self, request: ReviewCommentListRequestDto
    ) -> ReviewCommentListResponseDto:
        try:
            comments = self._repository.list_for_session(request.session_id)
            anchors = {
                (comment.path, comment.scope)
                for comment in comments
                if request.path is None
                or (comment.path == request.path and comment.scope == request.scope)
            }
            for path, scope in anchors:
                diff = self._observe_diff(request.session_id, path, scope)
                self._repository.refresh_anchor_status(
                    session_id=request.session_id,
                    path=path,
                    scope=scope,
                    base_head=diff.head,
                    diff_hash=_diff_hash(diff.unified_diff),
                )
            refreshed = self._repository.list_for_session(request.session_id)
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND") from error
        selected = [
            comment.model_dump(mode="json", by_alias=True)
            for comment in refreshed
            if request.path is None
            or (comment.path == request.path and comment.scope == request.scope)
        ]
        return _result(ReviewCommentListResponseDto, {"comments": selected})

    def create_comment(
        self, request: ReviewCommentCreateRequestDto
    ) -> ReviewCommentCreateResponseDto:
        operation_request = request.to_json_value()
        operation_request.pop("operationId", None)
        try:
            replay = self._repository.create_result(
                request.operation_id, operation_request
            )
            if replay is not None:
                return _result(
                    ReviewCommentCreateResponseDto,
                    {"comment": replay.model_dump(mode="json", by_alias=True)},
                )
            diff = self._observe_diff(request.session_id, request.path, request.scope)
            if (
                request.base_head != diff.head
                or request.diff_hash != _diff_hash(diff.unified_diff)
                or request.path not in diff.changed_files
            ):
                raise ApplicationError("REVIEW_DIFF_CHANGED")
            comment = self._repository.create(
                comment_id=request.comment_id,
                session_id=request.session_id,
                path=request.path,
                scope=request.scope,
                side=request.side,
                line=request.line,
                body=self._scan_text(request.body),
                base_head=request.base_head,
                diff_hash=request.diff_hash,
                operation_id=request.operation_id,
                operation_request=operation_request,
            )
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED") from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS") from error
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND") from error
        except InvalidRunStateError as error:
            raise ApplicationError("REVIEW_COMMENT_ID_REUSED") from error
        return _result(
            ReviewCommentCreateResponseDto,
            {"comment": comment.model_dump(mode="json", by_alias=True)},
        )

    def delete_comment(
        self, request: ReviewCommentDeleteRequestDto
    ) -> ReviewCommentDeleteResponseDto:
        operation_request = request.to_json_value()
        operation_request.pop("operationId", None)
        try:
            comment_id = self._repository.delete(
                session_id=request.session_id,
                comment_id=request.comment_id,
                operation_id=request.operation_id,
                operation_request=operation_request,
            )
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED") from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS") from error
        except ResourceNotFoundError as error:
            raise ApplicationError("REVIEW_COMMENT_NOT_FOUND") from error
        return _result(ReviewCommentDeleteResponseDto, {"commentId": comment_id})

    def _observe_diff(
        self, session_id: str, path: str, scope: str
    ) -> SessionGitDiffResponseDto:
        return self._git.git_diff(SessionGitDiffRequestDto(
            sessionId=session_id,
            path=path,
            scope=scope,
        ))


def _diff_hash(unified_diff: str) -> str:
    return hashlib.sha256(unified_diff.encode("utf-8")).hexdigest()


def _result(result_type: type[ResultDtoT], value: object) -> ResultDtoT:
    try:
        return result_type.model_validate(value)
    except ValidationError as error:
        raise ApplicationError("INTERNAL_ERROR") from error


__all__ = ["ReviewApplication"]
