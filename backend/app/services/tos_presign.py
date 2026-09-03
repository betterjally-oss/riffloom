from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlsplit


class PresignError(Exception):
    pass


class UploadPresigner(Protocol):
    enabled: bool

    def presigned_get(self, object_key: str, *, ttl_seconds: int) -> str: ...

    def presigned_put(
        self, object_key: str, *, content_type: str, ttl_seconds: int
    ) -> str: ...


class DisabledUploadPresigner:
    """No TOS signing configured; callers must use the base64 upload path."""

    enabled = False

    def presigned_get(self, object_key: str, *, ttl_seconds: int) -> str:
        raise PresignError("TOS 预签名未启用")

    def presigned_put(
        self, object_key: str, *, content_type: str, ttl_seconds: int
    ) -> str:
        raise PresignError(
            "TOS 预签名未启用；请配置 RIFFLOOM_TOS_* 或改用本机 base64 上传"
        )


class TosUploadPresigner:
    """Sign browser-to-TOS PUT urls through the TOS SDK.

    ``tos`` is imported lazily so local development without presign
    configuration never needs the SDK at import time.
    """

    enabled = True

    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        client: Any | None = None,
    ):
        if not (bucket and access_key_id and secret_access_key):
            raise PresignError("TOS 预签名配置不完整")
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("RIFFLOOM_TOS_ENDPOINT 必须是 HTTPS 地址")
        self.bucket = bucket
        self._client = client or self._build_client(
            endpoint=endpoint.rstrip("/"),
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )

    @staticmethod
    def _build_client(
        *, endpoint: str, region: str, access_key_id: str, secret_access_key: str
    ) -> Any:
        from tos import TosClientV2

        return TosClientV2(
            ak=access_key_id,
            sk=secret_access_key,
            endpoint=endpoint,
            region=region,
        )

    def presigned_put(
        self, object_key: str, *, content_type: str, ttl_seconds: int
    ) -> str:
        from tos import HttpMethodType

        result = self._client.pre_signed_url(
            HttpMethodType.Http_Method_Put,
            self.bucket,
            object_key,
            expires=max(60, ttl_seconds),
            header={"Content-Type": content_type},
        )
        return result.signed_url

    def presigned_get(self, object_key: str, *, ttl_seconds: int) -> str:
        from tos import HttpMethodType

        result = self._client.pre_signed_url(
            HttpMethodType.Http_Method_Get,
            self.bucket,
            object_key,
            expires=max(60, ttl_seconds),
        )
        return result.signed_url
