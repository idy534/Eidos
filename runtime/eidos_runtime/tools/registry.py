from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import threading
from typing import Literal, Protocol

from pydantic import (
    BaseModel,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from eidos_runtime.protocol.schemas import ClosedModel, StepToolSnapshotDto
from eidos_runtime.model.client import (
    CustomToolDefinition,
    CustomToolFormat,
    FunctionToolDefinition,
    MAX_CUSTOM_TOOL_INPUT_BYTES,
    MAX_FUNCTION_ARGUMENT_BYTES,
    ModelToolDefinitionLike,
)
from eidos_runtime.tools.contracts import (
    GENERIC_PROJECTOR,
    PROJECTORS,
    ToolResultProjector,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ALLOWED_SCHEMA_KEYS = {
    "type", "properties", "required", "additionalProperties", "items",
    "enum", "const", "default", "minimum", "maximum", "minLength",
    "maxLength", "minItems", "maxItems", "description",
}
MAX_ACTIVATED_TOOLS = 16
MAX_ACTIVATED_SCHEMA_BYTES = 128 * 1024
MAX_SINGLE_DEFINITION_BYTES = 32 * 1024
MAX_SINGLE_SCHEMA_BYTES = MAX_SINGLE_DEFINITION_BYTES


class ToolSpec(ClosedModel):
    name: StrictStr
    description: StrictStr
    side_effect: Literal["none", "workspace", "eidos_state", "shell", "external"] = Field(
        alias="sideEffect"
    )
    approval_required: bool = Field(alias="approvalRequired")
    timeout_seconds: StrictInt = Field(alias="timeoutSeconds", ge=1, le=600)
    batch_policy: Literal["parallel", "single"] = Field(
        default="single", alias="batchPolicy"
    )
    visibility: Literal["direct", "deferred"] = "direct"
    input_kind: Literal["function", "custom"] = Field(
        default="function", alias="inputKind"
    )
    input_schema: dict[str, object] | None = Field(
        default=None, alias="inputSchema"
    )
    input_format: CustomToolFormat | None = Field(
        default=None, alias="inputFormat"
    )
    result_schema: dict[str, object] = Field(alias="resultSchema")
    model_projection_policy: StrictStr = Field(
        default="generic", alias="modelProjectionPolicy"
    )
    contract_version: Literal[1] = Field(default=1, alias="contractVersion")

    @model_validator(mode="after")
    def validate_input_contract(self) -> ToolSpec:
        if self.input_kind == "function":
            if self.input_schema is None or self.input_format is not None:
                raise ValueError("invalid_function_input_contract")
        elif self.input_schema is not None:
            raise ValueError("invalid_custom_input_contract")
        return self

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _TOOL_NAME.fullmatch(value):
            raise ValueError("invalid_tool_name")
        return value


class ToolProvenance(ClosedModel):
    kind: Literal["builtin", "skill", "mcp"]
    source_id: StrictStr = Field(alias="sourceId", min_length=1, max_length=128)
    source_version: StrictStr = Field(alias="sourceVersion", min_length=1, max_length=64)
    content_hash: StrictStr = Field(alias="contentHash")
    plugin_id: StrictStr | None = Field(default=None, alias="pluginId")
    server_id: StrictStr | None = Field(default=None, alias="serverId")
    skill_id: StrictStr | None = Field(default=None, alias="skillId")

    @field_validator("content_hash")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("invalid_content_hash")
        return value


class ToolImplementation(Protocol):
    def execute(
        self, arguments: dict[str, object], cancel: threading.Event
    ) -> dict[str, object]: ...


class ToolCancellationPolicy(ClosedModel):
    mode: Literal["cancel_safe", "await_cleanup", "uncertain_after_intent"]


class ToolConcurrencyPolicy(ClosedModel):
    mode: Literal["parallel_safe", "exclusive"]
    max_concurrency: StrictInt = Field(default=1, ge=1, le=64)
    resource_keys: tuple[StrictStr, ...] = ()
    exclusive_keys: tuple[StrictStr, ...] = ()


class ToolExecutionPolicy(ClosedModel):
    side_effect: Literal[
        "none", "workspace", "eidos_state", "shell", "external"
    ]
    approval_required: bool
    timeout_seconds: StrictInt = Field(ge=1, le=600)
    cancellation: ToolCancellationPolicy
    concurrency: ToolConcurrencyPolicy


@dataclass(frozen=True)
class PreparedToolInvocation:
    arguments: object


@dataclass(frozen=True)
class VerifiedToolOutput:
    result: dict[str, object]
    activated_tool_names: tuple[str, ...] = ()
    workspace_change: object | None = None


class ToolRuntime(Protocol):
    def prepare(
        self,
        context: object,
        arguments: object,
        cancel: threading.Event,
    ) -> PreparedToolInvocation: ...

    def execute(
        self,
        context: object,
        prepared: PreparedToolInvocation,
        cancel: threading.Event,
    ) -> dict[str, object]: ...

    def verify(
        self,
        context: object,
        prepared: PreparedToolInvocation,
        raw: dict[str, object],
        cancel: threading.Event,
    ) -> VerifiedToolOutput: ...

    def cleanup(self, context: object, reason: str) -> None: ...

    def invoke(
        self,
        context: object,
        run_id: str,
        item: dict[str, object],
        call: object,
        cancel: threading.Event,
    ) -> object: ...


@dataclass(frozen=True)
class AdapterToolRuntime:
    """Default immutable per-tool runtime for simple prepare/execute/verify tools."""

    implementation: ToolImplementation
    spec: ToolSpec
    provenance: ToolProvenance

    def prepare(
        self,
        _context: object,
        arguments: object,
        _cancel: threading.Event,
    ) -> PreparedToolInvocation:
        return PreparedToolInvocation(arguments)

    def execute(
        self,
        _context: object,
        prepared: PreparedToolInvocation,
        cancel: threading.Event,
    ) -> dict[str, object]:
        return self.implementation.execute(prepared.arguments, cancel)

    def verify(
        self,
        _context: object,
        _prepared: PreparedToolInvocation,
        raw: dict[str, object],
        _cancel: threading.Event,
    ) -> VerifiedToolOutput:
        return VerifiedToolOutput(raw)

    def cleanup(self, _context: object, _reason: str) -> None:
        return None

    def invoke(
        self,
        context: object,
        run_id: str,
        item: dict[str, object],
        call: object,
        cancel: threading.Event,
    ) -> object:
        return context.invoke_read(  # type: ignore[attr-defined]
            self, run_id, item, call, cancel
        )


@dataclass(frozen=True)
class WorkspaceMutationRuntime(AdapterToolRuntime):
    def invoke(
        self, context: object, run_id: str, item: dict[str, object],
        call: object, cancel: threading.Event,
    ) -> object:
        return context.invoke_workspace_mutation(  # type: ignore[attr-defined]
            self, run_id, item, call, cancel
        )


@dataclass(frozen=True)
class ShellToolRuntime(AdapterToolRuntime):
    def invoke(
        self, context: object, run_id: str, item: dict[str, object],
        call: object, cancel: threading.Event,
    ) -> object:
        return context.invoke_shell(  # type: ignore[attr-defined]
            self, run_id, item, call, cancel
        )


@dataclass(frozen=True)
class ExternalToolRuntime(AdapterToolRuntime):
    def invoke(
        self, context: object, run_id: str, item: dict[str, object],
        call: object, cancel: threading.Event,
    ) -> object:
        return context.invoke_external(  # type: ignore[attr-defined]
            self, run_id, item, call, cancel
        )


@dataclass(frozen=True)
class EidosStateToolRuntime(AdapterToolRuntime):
    network_prepare: bool = False

    def invoke(
        self, context: object, run_id: str, item: dict[str, object],
        call: object, cancel: threading.Event,
    ) -> object:
        return context.invoke_eidos_state(  # type: ignore[attr-defined]
            self, run_id, item, call, cancel
        )


class ToolArgumentValidationResult(ClosedModel):
    valid: bool
    normalized_arguments: dict[str, object] | None = None
    normalized_input: str | None = None
    code: StrictStr | None = None
    path: StrictStr | None = None
    reason_code: StrictStr | None = None


@dataclass(frozen=True)
class ToolRegistryEntry:
    spec: ToolSpec
    provenance: ToolProvenance
    adapter: ToolImplementation
    input_model: type[BaseModel] | None = None
    result_data_model: type[BaseModel] | None = None
    input_schema_validator: object | None = None
    output_schema_validator: object | None = None
    runtime: ToolRuntime | None = None
    projector: ToolResultProjector | None = None
    execution_policy: ToolExecutionPolicy | None = None

    def __post_init__(self) -> None:
        if (
            self.spec.input_kind == "function"
            and self.input_model is None
            and self.input_schema_validator is None
        ):
            from eidos_runtime.tools.json_schema import BoundedJsonSchema

            try:
                validator = BoundedJsonSchema(self.spec.input_schema)
            except ValueError:
                validator = None
            object.__setattr__(self, "input_schema_validator", validator)
        if self.runtime is None:
            runtime_type: type[AdapterToolRuntime]
            if self.spec.side_effect == "workspace":
                runtime_type = WorkspaceMutationRuntime
            elif self.spec.side_effect == "shell":
                runtime_type = ShellToolRuntime
            elif self.spec.side_effect == "external":
                runtime_type = ExternalToolRuntime
            elif self.spec.side_effect == "eidos_state":
                object.__setattr__(
                    self,
                    "runtime",
                    EidosStateToolRuntime(
                        self.adapter,
                        self.spec,
                        self.provenance,
                        network_prepare=self.spec.name == "skill_install",
                    ),
                )
                runtime_type = AdapterToolRuntime
            else:
                runtime_type = AdapterToolRuntime
            if self.runtime is None:
                object.__setattr__(
                    self,
                    "runtime",
                    runtime_type(self.adapter, self.spec, self.provenance),
                )
        if self.projector is None:
            object.__setattr__(
                self,
                "projector",
                PROJECTORS.get(
                    self.spec.model_projection_policy, GENERIC_PROJECTOR
                ),
            )
        if self.execution_policy is None:
            object.__setattr__(
                self,
                "execution_policy",
                _execution_policy(self.spec),
            )

    def result_model_json_schema(self) -> dict[str, object]:
        if self.result_data_model is None:
            return self.spec.result_schema
        from eidos_runtime.tools.contracts import result_model

        return result_model(self.result_data_model).model_json_schema(by_alias=True)

    def validate_arguments(
        self, value: object, *, enforce_size: bool = True
    ) -> ToolArgumentValidationResult:
        if self.spec.input_kind != "function":
            return ToolArgumentValidationResult(
                valid=False,
                code="TOOL_ARGUMENT_CONTRACT_VIOLATION",
                reason_code="custom_input_requires_raw_payload",
            )
        try:
            encoded_value = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
            if enforce_size and len(encoded_value) > MAX_FUNCTION_ARGUMENT_BYTES:
                return ToolArgumentValidationResult(
                    valid=False,
                    code="TOOL_ARGUMENT_CONTRACT_VIOLATION",
                    reason_code="function_arguments_too_large",
                )
            if self.input_model is not None:
                normalized = self.input_model.model_validate_json(
                    encoded_value.decode("utf-8")
                ).model_dump(mode="json", by_alias=True)
            elif self.input_schema_validator is not None:
                validated = self.input_schema_validator.validate(
                    value, apply_defaults=True
                )
                if not isinstance(validated, dict):
                    raise ValueError("arguments_not_object")
                normalized = validated
            else:
                raise ValueError("missing_argument_contract")
        except ValidationError as error:
            details = error.errors(include_url=False)
            detail = details[0] if details else {}
            return ToolArgumentValidationResult(
                valid=False,
                code="TOOL_ARGUMENT_CONTRACT_VIOLATION",
                path=_validation_path(detail.get("loc")),
                reason_code=_validation_error_reason(detail),
            )
        except (TypeError, ValueError) as error:
            return ToolArgumentValidationResult(
                valid=False,
                code="TOOL_ARGUMENT_CONTRACT_VIOLATION",
                path=_validation_path(getattr(error, "path", None)),
                reason_code=_validation_reason(
                    getattr(error, "code", None) or str(error)
                ),
            )
        return ToolArgumentValidationResult(
            valid=True, normalized_arguments=normalized
        )

    def validate_custom_input(
        self, value: object
    ) -> ToolArgumentValidationResult:
        if self.spec.input_kind != "custom":
            return ToolArgumentValidationResult(
                valid=False,
                code="TOOL_ARGUMENT_CONTRACT_VIOLATION",
                reason_code="function_input_requires_arguments",
            )
        if not isinstance(value, str):
            return ToolArgumentValidationResult(
                valid=False,
                code="TOOL_ARGUMENT_CONTRACT_VIOLATION",
                reason_code="custom_input_not_string",
            )
        try:
            input_bytes = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            return ToolArgumentValidationResult(
                valid=False,
                code="TOOL_ARGUMENT_CONTRACT_VIOLATION",
                reason_code="custom_input_invalid_utf8",
            )
        if input_bytes > MAX_CUSTOM_TOOL_INPUT_BYTES:
            return ToolArgumentValidationResult(
                valid=False,
                code="TOOL_ARGUMENT_CONTRACT_VIOLATION",
                reason_code="custom_input_too_large",
            )
        return ToolArgumentValidationResult(valid=True, normalized_input=value)

    def model_definition(self) -> ModelToolDefinitionLike:
        if self.spec.input_kind == "custom":
            return CustomToolDefinition(
                name=self.spec.name,
                description=self.spec.description,
                format=self.spec.input_format,
            )
        assert self.spec.input_schema is not None
        return FunctionToolDefinition(
            name=self.spec.name,
            description=self.spec.description,
            parameters_json_schema=self.spec.input_schema,
        )

    @property
    def contract_fingerprint(self) -> str:
        assert self.projector is not None
        assert self.execution_policy is not None
        output_schema = getattr(self.output_schema_validator, "schema", None)
        return _hash_json({
            "contractVersion": self.spec.contract_version,
            "model": {
                "name": self.spec.name,
                "description": self.spec.description,
                "visibility": self.spec.visibility,
                "inputKind": self.spec.input_kind,
                "inputSchema": self.spec.input_schema,
                "inputFormat": (
                    self.spec.input_format.model_dump(mode="json")
                    if self.spec.input_format is not None else None
                ),
                "inputFormatSha256": (
                    hashlib.sha256(
                        self.spec.input_format.definition.encode("utf-8")
                    ).hexdigest()
                    if self.spec.input_format is not None else None
                ),
            },
            "resultSchema": self.spec.result_schema,
            "dynamicOutputSchema": output_schema,
            "executionPolicy": self.execution_policy.model_dump(
                mode="json", by_alias=True
            ),
            "projectionPolicy": {
                "id": self.projector.policy_id,
                "version": self.projector.policy_version,
            },
            "provenanceContentHash": self.provenance.content_hash,
        })
    def validate_result(self, value: object) -> dict[str, object]:
        if self.result_data_model is None:
            raise ValueError("missing_result_model")
        from eidos_runtime.tools.contracts import result_model

        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        return result_model(self.result_data_model).model_validate_json(encoded).model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )


ToolDescriptor = ToolRegistryEntry


@dataclass(frozen=True)
class QuarantinedTool:
    name: str
    code: str


@dataclass(frozen=True)
class StepToolSnapshot:
    available_names: tuple[str, ...]
    direct_names: tuple[str, ...]
    deferred_names: tuple[str, ...]
    activated_names: tuple[str, ...]
    spec_hashes: tuple[tuple[str, str], ...]
    definitions_hash: str
    tool_set_hash: str
    # Executable bindings are Run-local and deliberately absent from as_dict().
    bindings: tuple[object, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return StepToolSnapshotDto.model_validate({
            "schemaVersion": 1,
            "availableNames": list(self.available_names),
            "directNames": list(self.direct_names),
            "deferredNames": list(self.deferred_names),
            "activatedNames": list(self.activated_names),
            "specHashes": {name: value for name, value in self.spec_hashes},
            "definitionsHash": self.definitions_hash,
            "toolSetHash": self.tool_set_hash,
        }).to_json_value()

    def binding(self, name: str) -> StepToolBinding | None:
        return next(
            (
                binding
                for binding in self.bindings
                if isinstance(binding, StepToolBinding)
                and binding.tool_name == name
            ),
            None,
        )


@dataclass(frozen=True)
class StepToolBinding:
    tool_name: str
    contract_fingerprint: str
    descriptor: ToolDescriptor


class ToolRegistry:
    def __init__(
        self,
        entries: tuple[ToolRegistryEntry, ...],
        *,
        quarantined: tuple[QuarantinedTool, ...] = (),
    ) -> None:
        ordered = sorted(entries, key=lambda entry: entry.spec.name.encode("utf-8"))
        names: set[str] = set()
        for entry in ordered:
            if entry.spec.name in names:
                raise ValueError("duplicate_tool_name")
            _validate_entry(entry)
            names.add(entry.spec.name)
        self._entries = tuple(ordered)
        self._by_name = {entry.spec.name: entry for entry in ordered}
        self.quarantined = quarantined

    @classmethod
    def build(
        cls,
        *,
        builtin_entries: tuple[ToolRegistryEntry, ...],
        external_entries: tuple[ToolRegistryEntry, ...],
    ) -> ToolRegistry:
        builtins = cls(builtin_entries)
        accepted = list(builtins.entries)
        names = set(builtins.names)
        quarantined: list[QuarantinedTool] = []
        for entry in sorted(
            external_entries, key=lambda value: value.spec.name.encode("utf-8")
        ):
            try:
                if entry.spec.name in names:
                    raise ValueError("duplicate_tool_name")
                _validate_entry(entry)
            except ValueError as error:
                quarantined.append(QuarantinedTool(entry.spec.name, str(error)))
                continue
            accepted.append(entry)
            names.add(entry.spec.name)
        return cls(tuple(accepted), quarantined=tuple(quarantined))

    @property
    def entries(self) -> tuple[ToolRegistryEntry, ...]:
        return self._entries

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._by_name)

    def get(self, name: str) -> ToolRegistryEntry | None:
        return self._by_name.get(name)

    def model_definitions(
        self, activated_names: tuple[str, ...] = ()
    ) -> tuple[ModelToolDefinitionLike, ...]:
        active = set(self._bounded_activated(activated_names))
        return tuple(entry.model_definition() for entry in self._entries if (
            entry.spec.visibility == "direct" or entry.spec.name in active
        ))

    def snapshot(
        self, *, activated_names: tuple[str, ...] = ()
    ) -> StepToolSnapshot:
        deferred = tuple(
            entry.spec.name for entry in self._entries
            if entry.spec.visibility == "deferred"
        )
        activated = self._bounded_activated(activated_names)
        direct = tuple(
            entry.spec.name for entry in self._entries
            if entry.spec.visibility == "direct"
        )
        available = tuple(sorted(
            (*direct, *activated), key=lambda value: value.encode("utf-8")
        ))
        bindings = tuple(
            StepToolBinding(
                name,
                self._by_name[name].contract_fingerprint,
                self._by_name[name],
            )
            for name in available
        )
        spec_hashes = tuple(
            (binding.tool_name, binding.contract_fingerprint)
            for binding in bindings
        )
        definitions = self.model_definitions(activated)
        definitions_hash = _hash_json({
            "definitions": [
                definition.model_dump(mode="json") for definition in definitions
            ],
            "contracts": dict(spec_hashes),
        })
        tool_set_hash = _hash_json({
            "availableNames": available,
            "directNames": direct,
            "deferredNames": deferred,
            "activatedNames": activated,
            "specHashes": spec_hashes,
            "definitionsHash": definitions_hash,
        })
        return StepToolSnapshot(
            available, direct, deferred, activated, spec_hashes,
            definitions_hash, tool_set_hash, bindings,
        )

    def _bounded_activated(self, activated_names: tuple[str, ...]) -> tuple[str, ...]:
        deferred = {
            entry.spec.name for entry in self._entries
            if entry.spec.visibility == "deferred"
        }
        accepted: list[str] = []
        total = 0
        for name in sorted(
            set(activated_names) & deferred, key=lambda value: value.encode("utf-8")
        ):
            entry = self._by_name[name]
            size = _definition_size(entry.model_definition())
            if size > MAX_SINGLE_DEFINITION_BYTES or total + size > MAX_ACTIVATED_SCHEMA_BYTES:
                continue
            accepted.append(name)
            total += size
            if len(accepted) >= MAX_ACTIVATED_TOOLS:
                break
        return tuple(accepted)


def _validate_entry(entry: ToolRegistryEntry) -> None:
    if entry.runtime is None or not all(
        hasattr(entry.runtime, method)
        for method in ("prepare", "execute", "verify", "cleanup", "invoke")
    ):
        raise ValueError("missing_tool_runtime")
    if entry.projector is None or entry.execution_policy is None:
        raise ValueError("missing_tool_contract")
    if entry.spec.input_kind == "function":
        assert entry.spec.input_schema is not None
        if entry.input_model is not None and (
            entry.spec.input_schema
            != entry.input_model.model_json_schema(by_alias=True)
        ):
            raise ValueError("input_schema_model_mismatch")
        if (
            entry.input_model is None
            and entry.input_schema_validator is None
            and not _valid_schema(entry.spec.input_schema)
        ):
            raise ValueError("invalid_tool_schema")
    elif (
        entry.input_model is not None
        or entry.input_schema_validator is not None
        or entry.spec.input_schema is not None
    ):
        raise ValueError("invalid_custom_input_contract")
    if entry.result_data_model is not None and (
        entry.spec.result_schema != entry.result_model_json_schema()
    ):
        raise ValueError("result_schema_model_mismatch")
    if (
        entry.result_data_model is None
        and not _valid_schema(entry.spec.result_schema)
    ):
        raise ValueError("invalid_tool_schema")
    if _definition_size(entry.model_definition()) > MAX_SINGLE_DEFINITION_BYTES:
        raise ValueError("tool_definition_too_large")
    if entry.provenance.kind == "mcp":
        if (
            not entry.provenance.plugin_id
            or not entry.provenance.server_id
            or not entry.spec.name.startswith(
                f"mcp__{entry.provenance.server_id}__"
            )
        ):
            raise ValueError("invalid_mcp_provenance")
    if entry.provenance.kind == "skill" and (
        not entry.provenance.plugin_id or not entry.provenance.skill_id
    ):
        raise ValueError("invalid_skill_provenance")


def _execution_policy(spec: ToolSpec) -> ToolExecutionPolicy:
    if spec.side_effect == "none":
        cancellation = "cancel_safe"
    elif spec.side_effect in {"shell", "external"}:
        cancellation = "await_cleanup"
    else:
        cancellation = "uncertain_after_intent"
    parallel = spec.batch_policy == "parallel" and spec.side_effect == "none"
    return ToolExecutionPolicy.model_validate({
        "side_effect": spec.side_effect,
        "approval_required": spec.approval_required,
        "timeout_seconds": spec.timeout_seconds,
        "cancellation": {"mode": cancellation},
        "concurrency": {
            "mode": "parallel_safe" if parallel else "exclusive",
            "max_concurrency": 16 if parallel else 1,
        },
    })


def _hash_json(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _definition_size(definition: ModelToolDefinitionLike) -> int:
    return len(json.dumps(
        definition.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8"))


def _validation_path(value: object) -> str | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    path = ""
    for part in value:
        if isinstance(part, int) and not isinstance(part, bool):
            path += f"[{part}]"
        elif isinstance(part, str) and part:
            path = f"{path}.{part}" if path else part
        else:
            return None
    return path[:256] if path else None


def _validation_reason(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value[:128]


_VALIDATION_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


def _validation_error_reason(detail: object) -> str | None:
    if isinstance(detail, dict):
        context = detail.get("ctx")
        if isinstance(context, dict):
            error = context.get("error")
            if isinstance(error, ValueError):
                candidate = str(error)
                if _VALIDATION_CODE.fullmatch(candidate):
                    return candidate
        return _validation_reason(detail.get("type"))
    return None


def _valid_schema(schema: object, *, nested: bool = False) -> bool:
    if not isinstance(schema, dict) or not set(schema) <= _ALLOWED_SCHEMA_KEYS:
        return False
    schema_type = schema.get("type")
    if schema_type not in {"object", "array", "string", "integer", "number", "boolean"}:
        return False
    if schema_type == "object":
        properties = schema.get("properties")
        if not isinstance(properties, dict) or schema.get("additionalProperties") is not False:
            return False
        if not all(isinstance(key, str) and _valid_schema(value, nested=True) for key, value in properties.items()):
            return False
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(
            isinstance(key, str) and key in properties for key in required
        ):
            return False
    elif schema_type == "array" and not _valid_schema(schema.get("items"), nested=True):
        return False
    elif "properties" in schema or "required" in schema or "additionalProperties" in schema:
        return False
    return nested or schema_type == "object"
