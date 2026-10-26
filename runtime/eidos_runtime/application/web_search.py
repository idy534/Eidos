from __future__ import annotations

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.domain.web_search import (
    SEARCH_PROVIDERS,
    SearchCredentialRefs,
    StoredWebSearchSettings,
    WebSearchError,
)
from eidos_runtime.infrastructure.secret_files import SecretFiles
from eidos_runtime.persistence.web_search import WebSearchRepository
from eidos_runtime.protocol.web_search import (
    SearchCredentialState,
    SearchCredentialValues,
    WebSearchResponse,
    WebSearchSaveRequest,
)


class WebSearchApplication:
    def __init__(self, repository: WebSearchRepository, secrets: SecretFiles) -> None:
        self.repository = repository
        self.secrets = secrets

    def read(self) -> WebSearchResponse:
        settings = self.repository.read()
        states: dict[str, str] = {}
        values: dict[str, str] = {}
        for provider in SEARCH_PROVIDERS:
            reference = settings.credentials.get(provider)
            states[provider] = "missing"
            values[provider] = ""
            if reference:
                try:
                    values[provider] = self.secrets.read(reference)
                    states[provider] = "stored"
                except (OSError, ValueError):
                    states[provider] = "unavailable"
        return WebSearchResponse(
            revision=settings.revision,
            config=settings.config,
            credentials=SearchCredentialState.model_validate(states),
            credential_values=SearchCredentialValues.model_validate(values),
        )

    def save(self, request: WebSearchSaveRequest) -> WebSearchResponse:
        # The shared DB lock also serializes immutable secret publication with a
        # settings transaction. No plaintext credential enters the operation ledger.
        with self.repository.lock:
            old = self.repository.read()
            if old.revision != request.expected_revision:
                raise ApplicationError(
                    "WEB_SEARCH_CONFIG_CONFLICT",
                    "网页搜索配置已经改变。请刷新后重新保存。",
                )
            refs = old.credentials.to_internal_dict()
            try:
                for change in request.credential_changes:
                    refs[change.provider] = (
                        self.secrets.write(change.value)
                        if change.action == "set"
                        else None
                    )
                credentials = SearchCredentialRefs.model_validate(refs)
                config = request.config
                provider = config.default_provider
                if provider:
                    reference = credentials.get(provider)
                    if not reference:
                        # Clearing a selected key disables search. Selecting an
                        # unconfigured service is a form error, never auto-selection.
                        if any(
                            c.provider == provider and c.action == "clear"
                            for c in request.credential_changes
                        ):
                            config = config.model_copy(
                                update={"default_provider": None}
                            )
                        else:
                            raise ApplicationError(
                                "INVALID_PARAMS", "请先为默认搜索服务填写 API Key。"
                            )
                    else:
                        self.secrets.read(reference)
                value = StoredWebSearchSettings(
                    revision=old.revision + 1, config=config, credentials=credentials
                )
                self.repository.save(value, old.revision)
            except WebSearchError as error:
                raise ApplicationError(error.code, str(error)) from None
            except (OSError, ValueError):
                raise ApplicationError(
                    "WEB_SEARCH_CREDENTIALS_UNAVAILABLE",
                    "搜索凭证无法保存或读取。请检查凭证文件权限并重新保存。",
                ) from None
            # Cleanup is bounded and best effort. It runs only after a confirmed
            # commit, under the same publication lock as secret rotation.
            try:
                self.secrets.prune(self.repository.credential_retained)
            except (OSError, ValueError):
                pass
            return self.read()
