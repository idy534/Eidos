from __future__ import annotations

from collections import Counter
import os
from pathlib import Path
import tomllib

from pydantic import ValidationError

from eidos_runtime.domain.agents import (
    AgentCatalogItem,
    AgentCatalogResponse,
    AgentCatalogSnapshot,
    AgentDefinition,
    MAX_AGENT_FILES,
    definition_version,
)
from eidos_runtime.model.client import ModelProfileSnapshot
from eidos_runtime.model.config import (
    MODEL_CATALOG,
    ModelConfigError,
    ModelConfigStore,
    ModelReasoningSelectionError,
)
from eidos_runtime.domain.input_reference import InputSnapshot
from eidos_runtime.infrastructure.agent_definition_files import read_definition_bytes as _definition_bytes




class AgentCatalog:
    """One bounded discovery/validation path for the picker and Run admission."""

    def __init__(self, data_directory: Path, models: ModelConfigStore | None = None):
        self.root = data_directory / "agents"
        self.models = models or ModelConfigStore(data_directory)
        if models is None:
            # Database initialization already established this private directory.
            self.models.path = data_directory / "models.json"

    def resolve_model(
        self, definition: AgentDefinition, parent: ModelProfileSnapshot
    ) -> ModelProfileSnapshot:
        selection = definition.model
        if selection is None:
            return parent
        config_id = selection.config_id or parent.model_id
        config = self.models.get(config_id)
        if config is None:
            raise ModelConfigError("模型尚未配置。")
        profile = MODEL_CATALOG.profile(config.id, config=config)
        if (
            selection.provider_id is not None
            and selection.provider_id != profile.provider_id
        ):
            raise ModelConfigError("提供商与模型配置不匹配。")
        if not config.supports_tool_call:
            raise ModelConfigError("模型不支持工具调用。")
        effort = MODEL_CATALOG.reasoning_selection(
            config.id, selection.reasoning_selection, config=config
        )
        if selection.config_id is None:
            return parent.model_copy(update={"reasoning_selection": effort})
        return profile.snapshot(config, reasoning_selection=effort)

    def _model_reason(self, definition: AgentDefinition) -> str | None:
        if not definition.enabled:
            return "已停用"
        if definition.model is None or definition.model.config_id is None:
            return None
        try:
            config = self.models.get(definition.model.config_id)
            if config is None:
                return "模型尚未配置。"
            self.resolve_model(
                definition,
                MODEL_CATALOG.profile(config.id, config=config).snapshot(config),
            )
        except ModelReasoningSelectionError:
            return "目标模型不支持所选思考档位。"
        except ModelConfigError as error:
            reason = str(error)
            if reason in {
                "模型尚未配置。",
                "提供商与模型配置不匹配。",
                "模型不支持工具调用。",
            }:
                return reason
            return "模型配置不可读取，请检查已有模型配置。"
        except ValueError:
            return "模型配置无效，请检查已有模型配置。"
        return None

    def scan(self) -> tuple[AgentCatalogSnapshot, AgentCatalogResponse]:
        definitions: list[AgentDefinition] = []
        rows: list[AgentCatalogItem] = []
        try:
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return AgentCatalogSnapshot(), AgentCatalogResponse()
        except OSError:
            return AgentCatalogSnapshot(), AgentCatalogResponse(
                error="智能体定义目录不可读取。"
            )
        try:
            if os.fstat(directory).st_uid != os.getuid():
                return AgentCatalogSnapshot(), AgentCatalogResponse(
                    error="智能体定义目录的所有者不匹配。"
                )
            names: list[str] = []
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.name.endswith(".toml"):
                        names.append(entry.name)
                        if len(names) > MAX_AGENT_FILES:
                            return AgentCatalogSnapshot(), AgentCatalogResponse(
                                error="智能体定义超过 128 个，请减少文件数量。"
                            )
            for name in sorted(names):
                source = str(self.root / name)
                try:
                    raw = _definition_bytes(directory, name)
                    definition = AgentDefinition.model_validate(
                        tomllib.loads(raw.decode("utf-8"))
                    )
                    definitions.append(definition)
                    reason = self._model_reason(definition)
                    rows.append(
                        AgentCatalogItem(
                            name=definition.name,
                            description=definition.description,
                            source=source,
                            base_role=definition.base_role,
                            provider_id=definition.model.provider_id
                            if definition.model
                            else None,
                            model_id=definition.model.config_id
                            if definition.model
                            else None,
                            enabled=definition.enabled,
                            available=reason is None,
                            reason=reason,
                            definition_version=definition_version(definition),
                        )
                    )
                except tomllib.TOMLDecodeError:
                    rows.append(
                        AgentCatalogItem(
                            name=name[:256],
                            source=source,
                            reason="TOML 格式错误，请检查定义文件。",
                        )
                    )
                except ValidationError:
                    rows.append(
                        AgentCatalogItem(
                            name=name[:256],
                            source=source,
                            reason="定义字段无效，请检查名称、版本、模型和指令。",
                        )
                    )
                except (OSError, ValueError, UnicodeError):
                    rows.append(
                        AgentCatalogItem(
                            name=name[:256],
                            source=source,
                            reason="定义不可读取、文件已变化或超过大小限制。",
                        )
                    )
        finally:
            os.close(directory)
        counts = Counter(value.name for value in definitions)
        rows = [
            row.model_copy(update={"available": False, "reason": "定义名称重复。"})
            if counts[row.name] > 1
            else row
            for row in rows
        ]
        available = {row.name for row in rows if row.available}
        return AgentCatalogSnapshot(
            definitions=[value for value in definitions if value.name in available]
        ), AgentCatalogResponse(agents=rows)

    def list(self) -> AgentCatalogResponse:
        return self.scan()[1]

    def backup_files(self) -> list[tuple[str, bytes]]:
        try:
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return []
        try:
            if os.fstat(directory).st_uid != os.getuid():
                raise ValueError("agent_directory_owner")
            files: list[tuple[str, bytes]] = []
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.name.endswith(".toml"):
                        if len(files) >= MAX_AGENT_FILES:
                            raise ValueError("agent_file_limit")
                        files.append(
                            (entry.name, _definition_bytes(directory, entry.name))
                        )
            return sorted(files)
        finally:
            os.close(directory)

    def freeze(
        self,
        references: list[InputSnapshot],
        *,
        previous: AgentCatalogSnapshot | None = None,
        retained_ids: set[str] | None = None,
    ) -> AgentCatalogSnapshot:
        current, _diagnostics = self.scan()
        available = {definition.name: definition for definition in current.definitions}
        frozen = {
            definition.name: definition
            for definition in (previous or current).definitions
        }
        for snapshot in references:
            if snapshot.reference.kind != "agent":
                continue
            definition = snapshot.agent_definition
            if (
                definition is None
                or definition.name != snapshot.selection_id
                or definition_version(definition) != snapshot.reference.sha256
            ):
                raise ValueError("Agent 引用无效，请重新选择。")
            if retained_ids and snapshot.reference.id in retained_ids:
                original = frozen.get(definition.name)
                if original != definition:
                    raise ValueError("历史 智能体定义不匹配。")
            elif available.get(definition.name) != definition:
                raise ValueError("Agent 已变化或不可用，请移除引用后重新选择。")
            frozen[definition.name] = definition
        return AgentCatalogSnapshot(definitions=list(frozen.values()))
