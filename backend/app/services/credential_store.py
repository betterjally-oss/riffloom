from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

FEISHU_SELF_BUILT_CREDENTIAL_REF = "env://feishu-self-built"


class CredentialStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class FeishuAppCredentials:
    app_id: str
    app_secret: str


class EnvCredentialStore:
    """Local-only credential adapter. Secret values never enter database rows."""

    def __init__(self, *, feishu_app_id: str | None, feishu_app_secret: str | None):
        self._feishu_app_id = feishu_app_id
        self._feishu_app_secret = feishu_app_secret

    def get_feishu_app_credentials(self, credential_ref: str) -> FeishuAppCredentials:
        if credential_ref != FEISHU_SELF_BUILT_CREDENTIAL_REF:
            raise CredentialStoreError("飞书凭据引用不存在或已清除")
        if not self._feishu_app_id or not self._feishu_app_secret:
            raise CredentialStoreError("FEISHU_APP_ID 或 FEISHU_APP_SECRET 尚未配置")
        return FeishuAppCredentials(
            app_id=self._feishu_app_id,
            app_secret=self._feishu_app_secret,
        )


class FeishuTokenCipher:
    def __init__(self, secret: str | None):
        if not secret or len(secret.encode()) < 32:
            raise CredentialStoreError(
                "用户飞书 OAuth 令牌加密要求 RIFFLOOM_AUTH_SECRET 至少 32 字节"
            )
        key = hashlib.sha256(f"riffloom:feishu-oauth:{secret}".encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    def encrypt(self, token: str) -> str:
        return self._fernet.encrypt(token.encode()).decode()

    def decrypt(self, encrypted: str) -> str:
        try:
            return self._fernet.decrypt(encrypted.encode()).decode()
        except (InvalidToken, ValueError) as error:
            raise CredentialStoreError(
                "飞书 OAuth 令牌无法解密，请重新绑定飞书"
            ) from error
