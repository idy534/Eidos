from contextlib import contextmanager
from collections.abc import Iterator
import hashlib
import os
import tomllib

from pydantic import ValidationError

from eidos_runtime.application.agent_catalog import AgentCatalog
from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.domain.agents import (
    AgentCatalogResponse,
    AgentDefinition,
    MAX_AGENT_FILES,
)
from eidos_runtime.infrastructure.agent_definition_files import (
    AgentDefinitionFiles,
    AgentDefinitionFileError,
    encode_definition,
)
from eidos_runtime.protocol.collaboration import (
    AgentDefinitionDocument,
    AgentDefinitionReadRequest,
    AgentDefinitionRemoveRequest,
    AgentDefinitionSaveRequest,
)


class AgentDefinitionsApplication:
    def __init__(self, catalog: AgentCatalog):
        self.catalog = catalog
        self.files = AgentDefinitionFiles(catalog.root)

    @contextmanager
    def _directory(self, *, create: bool = False) -> Iterator[int]:
        try:
            with self.files.directory(create=create) as directory:
                yield directory
        except AgentDefinitionFileError as error:
            raise ApplicationError(error.code, str(error)) from error

    def _document(self, filename: str, raw: bytes) -> AgentDefinitionDocument:
        try:
            definition = AgentDefinition.model_validate(
                tomllib.loads(raw.decode("utf-8"))
            )
        except (ValueError, UnicodeError, ValidationError) as error:
            raise ApplicationError(
                "AGENT_DEFINITION_INVALID", "智能体配置格式无效，请先修复 TOML 文件。"
            ) from error
        return AgentDefinitionDocument(
            source=str(self.catalog.root / filename),
            version=hashlib.sha256(raw).hexdigest(),
            definition=definition,
            text=raw.decode("utf-8"),
        )

    def read(self, request: AgentDefinitionReadRequest) -> AgentDefinitionDocument:
        with self._directory() as directory:
            filename = self.files.filename(request.source)
            return self._document(filename, self.files.read(directory, filename))

    def save(self, request: AgentDefinitionSaveRequest) -> AgentDefinitionDocument:
        if bool(request.source) != bool(request.expected_version):
            raise ApplicationError(
                "AGENT_DEFINITION_INVALID", "编辑智能体时需要原文件和版本。"
            )
        definition = request.definition
        reason = self.catalog._model_reason(definition) if definition.enabled else None
        if reason:
            raise ApplicationError(
                {
                    "提供商与模型配置不匹配。": "AGENT_MODEL_PROVIDER_MISMATCH",
                    "目标模型不支持所选思考档位。": "AGENT_MODEL_REASONING_INVALID",
                }.get(reason, "AGENT_MODEL_NOT_AVAILABLE"),
                reason,
            )
        with self._directory(create=True) as directory:
            filename = (
                self.files.filename(request.source)
                if request.source
                else f"{definition.name}.toml"
            )
            raw = encode_definition(definition)
            if request.source:
                old = self.files.read(directory, filename)
                self.files.check_version(old, request.expected_version)
                if self._document(filename, old).definition.name != definition.name:
                    raise ApplicationError(
                        "AGENT_DEFINITION_INVALID",
                        "智能体名称不能修改，请复制后使用新名称。",
                    )
            names: list[str] = []
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.name.endswith(".toml"):
                        names.append(entry.name)
                        if len(names) > MAX_AGENT_FILES:
                            break
            names.sort()
            if len(names) > MAX_AGENT_FILES or (
                not request.source and len(names) >= MAX_AGENT_FILES
            ):
                raise ApplicationError(
                    "AGENT_DEFINITION_INVALID", "智能体配置最多支持 128 个。"
                )
            for name in names:
                if request.source and name == filename:
                    continue
                try:
                    other = self._document(name, self.files.read(directory, name))
                except ApplicationError as error:
                    if error.code == "AGENT_DEFINITION_INVALID":
                        continue
                    raise
                if other.definition.name == definition.name:
                    raise ApplicationError(
                        "AGENT_DEFINITION_EXISTS",
                        "智能体名称已经存在，请使用其他名称。",
                    )
            self.files.write(directory, filename, raw, request.expected_version)
        return self._document(filename, raw)

    def remove(self, request: AgentDefinitionRemoveRequest) -> AgentCatalogResponse:
        with self._directory() as directory:
            filename = self.files.filename(request.source)
            self.files.remove(directory, filename, request.expected_version)
        return self.catalog.list()
