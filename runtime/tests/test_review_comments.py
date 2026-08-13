from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import uuid

import pytest
from pydantic import ValidationError

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.review import ReviewApplication
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.git import WorktreeManager
from eidos_runtime.persistence.review_comments import ReviewCommentRepository
from eidos_runtime.protocol.methods import (
    SessionCreateRequestDto,
    SessionGitDiffRequestDto,
)
from eidos_runtime.protocol.review import (
    ReviewCommentCreateRequestDto,
    ReviewCommentDeleteRequestDto,
    ReviewCommentListRequestDto,
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _fixture(tmp_path: Path) -> tuple[Path, SessionStore, SessionApplication, ReviewApplication, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.name", "Eidos Tests")
    _git(repository, "config", "user.email", "eidos-tests@example.com")
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "initial")
    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(store.database, managed_root=tmp_path / "worktrees")
    sessions = SessionApplication(store, scan_text=lambda value: value, worktree_manager=manager)
    session = sessions.create(SessionCreateRequestDto(
        workspaceRoot=str(repository), executionMode="local",
    )).root
    review = ReviewApplication(
        ReviewCommentRepository(store.database),
        git=sessions,
        scan_text=lambda value: value,
    )
    return repository, store, sessions, review, str(session["id"])


def _diff_facts(sessions: SessionApplication, session_id: str) -> tuple[str, str]:
    diff = sessions.git_diff(SessionGitDiffRequestDto(
        sessionId=session_id, scope="head", path="tracked.txt",
    )).root
    return str(diff["head"]), hashlib.sha256(
        str(diff["unifiedDiff"]).encode("utf-8")
    ).hexdigest()


def test_comment_create_replays_and_diff_change_marks_anchor_stale(tmp_path: Path) -> None:
    repository, store, sessions, review, session_id = _fixture(tmp_path)
    try:
        (repository / "tracked.txt").write_text("first\n", encoding="utf-8")
        head, diff_hash = _diff_facts(sessions, session_id)
        request = ReviewCommentCreateRequestDto(
            operationId=str(uuid.uuid4()),
            commentId=str(uuid.uuid4()),
            sessionId=session_id,
            path="tracked.txt",
            scope="head",
            side="new",
            line=1,
            body="Please keep the original wording.",
            baseHead=head,
            diffHash=diff_hash,
        )

        created = review.create_comment(request).root
        assert created["comment"]["status"] == "active"
        assert created["comment"]["path"] == "tracked.txt"
        assert created["comment"]["baseHead"] == head

        (repository / "tracked.txt").write_text("second\n", encoding="utf-8")
        replayed = review.create_comment(request).root
        assert replayed == created
        listed = review.list_comments(ReviewCommentListRequestDto(
            sessionId=session_id, path="tracked.txt", scope="head",
        )).root

        assert listed["comments"][0]["status"] == "stale"
    finally:
        store.close()


def test_comment_rejects_changed_diff_and_delete_is_idempotent(tmp_path: Path) -> None:
    repository, store, sessions, review, session_id = _fixture(tmp_path)
    try:
        (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")
        head, diff_hash = _diff_facts(sessions, session_id)
        invalid = ReviewCommentCreateRequestDto(
            operationId=str(uuid.uuid4()),
            commentId=str(uuid.uuid4()),
            sessionId=session_id,
            path="tracked.txt",
            scope="head",
            side="new",
            line=1,
            body="Review this line.",
            baseHead=head,
            diffHash="0" * 64,
        )
        with pytest.raises(ApplicationError) as changed:
            review.create_comment(invalid)
        assert changed.value.code == "REVIEW_DIFF_CHANGED"

        create = ReviewCommentCreateRequestDto(
            operationId=str(uuid.uuid4()),
            commentId=str(uuid.uuid4()),
            sessionId=session_id,
            path="tracked.txt",
            scope="head",
            side="old",
            line=1,
            body="Why remove this?",
            baseHead=head,
            diffHash=diff_hash,
        )
        comment_id = str(review.create_comment(create).root["comment"]["id"])
        delete = ReviewCommentDeleteRequestDto(
            operationId=str(uuid.uuid4()),
            sessionId=session_id,
            commentId=comment_id,
        )
        first = review.delete_comment(delete).root
        replay = review.delete_comment(delete).root

        assert replay == first == {"commentId": comment_id}
        assert review.list_comments(ReviewCommentListRequestDto(
            sessionId=session_id,
        )).root["comments"] == []
    finally:
        store.close()


def test_comment_rejects_invalid_diff_anchor_without_persisting(
    tmp_path: Path,
) -> None:
    repository, store, sessions, review, session_id = _fixture(tmp_path)
    try:
        (repository / "tracked.txt").write_text("base\nadded\n", encoding="utf-8")
        head, diff_hash = _diff_facts(sessions, session_id)
        invalid_line = ReviewCommentCreateRequestDto(
            operationId=str(uuid.uuid4()),
            commentId=str(uuid.uuid4()),
            sessionId=session_id,
            path="tracked.txt",
            scope="head",
            side="new",
            line=999999,
            body="This line does not exist.",
            baseHead=head,
            diffHash=diff_hash,
        )
        with pytest.raises(ApplicationError) as error:
            review.create_comment(invalid_line)
        assert error.value.code == "REVIEW_ANCHOR_INVALID"
        assert review.list_comments(
            ReviewCommentListRequestDto(sessionId=session_id)
        ).root["comments"] == []

        wrong_side = invalid_line.model_copy(update={
            "operationId": str(uuid.uuid4()),
            "commentId": str(uuid.uuid4()),
            "line": 2,
            "side": "old",
        })
        with pytest.raises(ApplicationError) as error:
            review.create_comment(wrong_side)
        assert error.value.code == "REVIEW_ANCHOR_INVALID"
    finally:
        store.close()


def test_comment_operation_id_reuse_with_different_request_is_rejected(
    tmp_path: Path,
) -> None:
    repository, store, sessions, review, session_id = _fixture(tmp_path)
    try:
        (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")
        head, diff_hash = _diff_facts(sessions, session_id)
        operation_id = str(uuid.uuid4())
        request = ReviewCommentCreateRequestDto(
            operationId=operation_id,
            commentId=str(uuid.uuid4()),
            sessionId=session_id,
            path="tracked.txt",
            scope="head",
            side="new",
            line=1,
            body="First request.",
            baseHead=head,
            diffHash=diff_hash,
        )
        review.create_comment(request)

        reused = request.model_copy(update={"body": "Different request."})
        with pytest.raises(ApplicationError) as conflict:
            review.create_comment(reused)

        assert conflict.value.code == "OPERATION_ID_REUSED"
    finally:
        store.close()


def test_review_comment_request_rejects_invalid_anchor_fields() -> None:
    common = {
        "operationId": str(uuid.uuid4()),
        "commentId": str(uuid.uuid4()),
        "sessionId": str(uuid.uuid4()),
        "scope": "head",
        "side": "new",
        "line": 1,
        "body": "Review this line.",
        "baseHead": "a" * 40,
        "diffHash": "b" * 64,
    }
    with pytest.raises(ValidationError):
        ReviewCommentCreateRequestDto(path="../outside.txt", **common)
    with pytest.raises(ValidationError):
        ReviewCommentCreateRequestDto(
            path="tracked.txt", **{**common, "diffHash": "not-a-hash"}
        )
    with pytest.raises(ValidationError):
        ReviewCommentListRequestDto(
            sessionId=str(uuid.uuid4()), path="tracked.txt"
        )
