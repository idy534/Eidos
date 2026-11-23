"""Team templates: built-in rosters plus user files in ``<data>/teams/*.toml``.

The catalog is read when a lead proposes a team; the chosen template is frozen
into the team row at confirmation, so later file edits never change a running
team. User files are read with the same owner, link and size checks as agent
definitions and cannot override a built-in name.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import ValidationError

from eidos_runtime.domain.teams import TeamRejected, TeamTemplate, TeamTemplateSummary, TemplateMember
from eidos_runtime.infrastructure import local_files
from eidos_runtime.infrastructure.agent_definition_files import read_definition_bytes

MAX_TEMPLATE_FILES = 64

BUILTIN_TEMPLATES: tuple[TeamTemplate, ...] = (
    TeamTemplate(
        name="feature",
        description="Build a feature across modules: map the code, implement in parallel, test and verify.",
        guidance=("Start with the architect mapping interfaces and risks. Split implementation by module so builders "
                  "touch different files. The tester writes tests against the acceptance criteria. Route every "
                  "implementation task through the verifier before you integrate it."),
        members=[
            TemplateMember(name="architect", role="explorer", workspace="shared",
                           brief="Map the affected code, interfaces and risks. Propose the split and the contracts between modules. Do not edit files."),
            TemplateMember(name="builder", role="worker",
                           brief="Implement the tasks you own within their paths. Run the targeted tests for what you change and cite them."),
            TemplateMember(name="tester", role="worker",
                           brief="Write and run tests for the acceptance criteria, including failure paths. Report gaps with evidence."),
            TemplateMember(name="verifier", role="verifier", workspace="shared",
                           brief="Review submitted tasks against their acceptance criteria and evidence. Accept or send back with reasons. Do not edit files."),
        ],
        gate_reviewers=["verifier"],
    ),
    TeamTemplate(
        name="research",
        description="Broad investigation: parallel scouts, one analyst to synthesize, one skeptic to challenge claims.",
        guidance=("Give each scout a distinct question or source area. The analyst merges findings into one answer with "
                  "sources. The skeptic checks every key claim against its evidence before you report."),
        members=[
            TemplateMember(name="scout", role="explorer", workspace="shared",
                           brief="Investigate the questions you own. Record sources and exact evidence for every finding."),
            TemplateMember(name="analyst", role="default", workspace="shared",
                           brief="Merge the scouts' findings into one structured answer. Mark what is confirmed and what is inferred."),
            TemplateMember(name="skeptic", role="verifier", workspace="shared",
                           brief="Challenge key claims: look for counter-evidence, missing cases and weak sources. Accept or send back with reasons."),
        ],
        workspace_policy="shared",
        gate_reviewers=["skeptic"],
    ),
    TeamTemplate(
        name="milestones",
        description="Long-running work in milestones, each closed by a critic review and a challenger's adversarial tests.",
        guidance=("Plan the work as milestones with a milestone name on every task. Each milestone closes with gates "
                  "owned by the critic and the challenger; tasks of the next milestone depend on those gate tasks. "
                  "When a gate reopens tasks, reassign or fix them before moving on."),
        members=[
            TemplateMember(name="builder-a", role="worker", brief="Implement the tasks you own in the current milestone and cite your test runs."),
            TemplateMember(name="builder-b", role="worker", brief="Implement the tasks you own in the current milestone and cite your test runs."),
            TemplateMember(name="critic", role="verifier", workspace="shared",
                           brief="At each milestone gate, review the milestone's changes for correctness, design and missing cases. Reopen tasks that fall short."),
            TemplateMember(name="challenger", role="verifier", workspace="shared",
                           brief="At each milestone gate, try to break the milestone's work with adversarial and edge-case tests. Reopen tasks you can break."),
        ],
        gate_reviewers=["critic", "challenger"],
    ),
)


class TeamTemplateCatalog:
    def __init__(self, data_directory: Path | None) -> None:
        self.root = data_directory / "teams" if data_directory is not None else None

    def templates(self) -> tuple[TeamTemplate, ...]:
        builtin = {template.name for template in BUILTIN_TEMPLATES}
        return BUILTIN_TEMPLATES + tuple(value for value in self._user_templates() if value.name not in builtin)

    def summaries(self) -> list[TeamTemplateSummary]:
        builtin = {template.name for template in BUILTIN_TEMPLATES}
        return [TeamTemplateSummary(name=value.name, description=value.description, members=[member.name for member in value.members],
                                    gate_reviewers=list(value.gate_reviewers), workspace_policy=value.workspace_policy, source="builtin" if value.name in builtin else "user")
                for value in self.templates()]

    def get(self, name: str) -> TeamTemplate:
        template = next((value for value in self.templates() if value.name == name), None)
        if template is None:
            raise TeamRejected("team_template_not_found")
        return template

    def _user_templates(self) -> list[TeamTemplate]:
        if self.root is None:
            return []
        try:
            directory = local_files.open_directory(self.root)
        except OSError:
            return []
        values: list[TeamTemplate] = []
        try:
            if not local_files.owned_by_user(directory, os.fstat(directory)):
                return []
            with local_files.scan_directory(directory) as entries:
                names = sorted(entry.name for entry in entries if entry.name.endswith(".toml"))[:MAX_TEMPLATE_FILES]
            for name in names:
                try:
                    values.append(TeamTemplate.model_validate(tomllib.loads(read_definition_bytes(directory, name).decode("utf-8"))))
                except (OSError, ValueError, UnicodeError, tomllib.TOMLDecodeError, ValidationError):
                    # An unreadable or invalid file is skipped; the rest of the catalog stays usable.
                    continue
        finally:
            os.close(directory)
        # A duplicated name keeps its first file in name order.
        unique: dict[str, TeamTemplate] = {}
        for value in values:
            unique.setdefault(value.name, value)
        return list(unique.values())
