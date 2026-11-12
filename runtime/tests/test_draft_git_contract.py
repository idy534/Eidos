import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from eidos_runtime.protocol.methods import (
    GitContextRequestDto,
    GitContextResponseDto,
    SessionGitSwitchBranchRequestDto,
)


def test_draft_git_branch_fixture() -> None:
    fixture = json.loads(
        (Path(__file__).resolve().parents[2] / "protocol/fixtures/v1.json").read_text()
    )["draftGitBranch"]
    context = fixture["contextRequest"]["params"]
    assert GitContextRequestDto.model_validate(context).to_json_value() == context
    with pytest.raises(ValidationError):
        GitContextRequestDto.model_validate({**context, "workspaceRoot": "/other"})

    result = fixture["contextResponse"]["result"]
    assert GitContextResponseDto.model_validate(result).to_json_value() == result

    request = fixture["switchRequest"]["params"]
    switch = SessionGitSwitchBranchRequestDto.model_validate(request)
    assert switch.expected_branch is None
    assert switch.expected_head == result["head"]
    for key in ("projectId", "rootId", "expectedBranch", "expectedHead"):
        with pytest.raises(ValidationError):
            SessionGitSwitchBranchRequestDto.model_validate(
                {name: value for name, value in request.items() if name != key}
            )
