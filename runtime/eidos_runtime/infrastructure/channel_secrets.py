from __future__ import annotations

from pathlib import Path

from eidos_runtime.domain.channels import ChannelError
from eidos_runtime.infrastructure.secret_files import SecretFiles


class ChannelSecrets(SecretFiles):
    def __init__(self, data_directory: Path) -> None:
        super().__init__(data_directory / "channel-secrets")

    def read(self, identifier: str) -> str:
        try:
            return super().read(identifier)
        except ValueError as error:
            raise ChannelError("渠道凭证不可用，请重新保存 Client Secret。") from error

    def write(self, secret: str) -> str:
        try:
            return super().write(secret)
        except ValueError as error:
            raise ChannelError("渠道凭证目录的权限无效。") from error
