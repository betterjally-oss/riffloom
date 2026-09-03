from __future__ import annotations

import hashlib
import html
import base64
import binascii
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx


class CoverProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class CoverGenerationRequest:
    task_id: str
    attempt_no: int
    prompt: str
    width: int
    height: int
    variant_numbers: tuple[int, ...]
    input_assets: tuple[dict[str, object], ...]
    revision_no: int = 0


@dataclass(frozen=True)
class CoverVariant:
    variant_no: int
    content: bytes
    mime_type: str
    width: int
    height: int


@dataclass(frozen=True)
class CoverGenerationBatch:
    request_id: str
    provider: str
    model: str
    variants: tuple[CoverVariant, ...]
    failed_variants: tuple[int, ...]
    estimated_cost_usd: float = 0.0
    estimated_cost_cny: float = 0.0


class CoverProvider(Protocol):
    provider_id: str
    model_id: str
    is_mock: bool
    external_calls: bool

    def capabilities(self) -> dict[str, object]: ...

    def generate(self, request: CoverGenerationRequest) -> CoverGenerationBatch: ...

    def close(self) -> None: ...


class MockCoverProvider:
    provider_id = "mock-cover-v1"
    model_id = "deterministic-svg-cover-v1"
    is_mock = True
    external_calls = False

    _palettes = (
        ("#182033", "#8fb8e8", "#f7fbff"),
        ("#263323", "#d7c78e", "#fffbed"),
        ("#4a211f", "#edaa94", "#fff4ef"),
        ("#3e2d1d", "#d9b982", "#fff9ed"),
    )

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": self.provider_id,
            "model": self.model_id,
            "mode": "deterministic_mock",
            "is_mock": True,
            "ratios": ["3:4"],
            "width": 1200,
            "height": 1600,
            "variants": 4,
            "max_revisions": 2,
            "external_calls": False,
        }

    def generate(self, request: CoverGenerationRequest) -> CoverGenerationBatch:
        if "[cover-fail]" in request.prompt:
            raise CoverProviderError(
                "COVER_PROVIDER_FAILED", "mock 封面 Provider 模拟失败"
            )
        failed = (
            (4,)
            if "[cover-partial]" in request.prompt
            and request.attempt_no == 1
            and 4 in request.variant_numbers
            else ()
        )
        variants = tuple(
            CoverVariant(
                variant_no=number,
                content=self._svg(request, number),
                mime_type="image/svg+xml",
                width=request.width,
                height=request.height,
            )
            for number in request.variant_numbers
            if number not in failed
        )
        digest = hashlib.sha256(
            f"{request.task_id}:{request.attempt_no}:{request.prompt}".encode()
        ).hexdigest()[:16]
        return CoverGenerationBatch(
            request_id=f"cover_mock_{digest}",
            provider=self.provider_id,
            model=self.model_id,
            variants=variants,
            failed_variants=failed,
        )

    def _svg(self, request: CoverGenerationRequest, variant_no: int) -> bytes:
        dark, accent, light = self._palettes[(variant_no - 1) % len(self._palettes)]
        clean_prompt = html.escape(" ".join(request.prompt.split())[:88])
        subtitle = (
            f"第 {request.revision_no} 轮修改 · 方案 {variant_no}"
            if request.revision_no
            else f"初始方案 {variant_no}"
        )
        asset_count = len(request.input_assets)
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{request.width}" height="{request.height}" viewBox="0 0 1200 1600">
<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop stop-color="{dark}"/><stop offset="1" stop-color="{accent}"/></linearGradient></defs>
<rect width="1200" height="1600" fill="url(#bg)"/>
<circle cx="1020" cy="210" r="260" fill="{light}" opacity=".12"/>
<circle cx="160" cy="1390" r="330" fill="{dark}" opacity=".22"/>
<rect x="90" y="104" width="360" height="54" rx="27" fill="{light}" opacity=".92"/>
<text x="120" y="141" fill="{dark}" font-family="PingFang SC, sans-serif" font-size="24" font-weight="700">RIFFLOOM · LOCAL MOCK</text>
<text x="92" y="360" fill="{light}" font-family="PingFang SC, sans-serif" font-size="44" opacity=".82">{html.escape(subtitle)}</text>
<foreignObject x="88" y="430" width="1024" height="620"><div xmlns="http://www.w3.org/1999/xhtml" style="color:{light};font:700 78px/1.25 'PingFang SC',sans-serif;word-break:break-word">{clean_prompt}</div></foreignObject>
<line x1="92" y1="1190" x2="1108" y2="1190" stroke="{light}" stroke-width="3" opacity=".48"/>
<text x="92" y="1260" fill="{light}" font-family="PingFang SC, sans-serif" font-size="30">3:4 · 1200×1600 · 授权素材 {asset_count}</text>
<text x="92" y="1450" fill="{light}" font-family="PingFang SC, sans-serif" font-size="26" opacity=".72">确定性模拟图 · 未调用外部图片服务</text>
</svg>"""
        return svg.encode("utf-8")

    def close(self) -> None:
        return None


class VolcengineSeedreamCoverProvider:
    provider_id = "volcengine-seedream-v1"
    is_mock = False
    external_calls = True

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "doubao-seedream-4-0-250828",
        base_url: str = "https://ark.cn-beijing.volces.com/api/v3",
        timeout_seconds: float = 180,
        cost_cny_per_image: float = 0.2,
        client: httpx.Client | None = None,
    ):
        parts = urlsplit(base_url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise RuntimeError("Seedream API 地址必须是无内嵌凭据的 HTTPS URL")
        if not api_key:
            raise RuntimeError("volcengine-seedream-v1 必须配置 ARK_API_KEY")
        self.api_key = api_key
        self.model_id = model
        self.cost_cny_per_image = max(0.0, cost_cny_per_image)
        self.client = client or httpx.Client(
            base_url=f"{base_url.rstrip('/')}/",
            timeout=timeout_seconds,
            follow_redirects=False,
        )

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": self.provider_id,
            "model": self.model_id,
            "mode": "production",
            "is_mock": False,
            "ratios": ["3:4"],
            "width": 1200,
            "height": 1600,
            "variants": 4,
            "max_revisions": 2,
            "external_calls": True,
            "requires_usage_confirmation": True,
            "estimated_cost_cny_per_image": self.cost_cny_per_image,
            "estimated_cost_cny_max_request": self.cost_cny_per_image * 4,
        }

    @staticmethod
    def _input_images(assets: tuple[dict[str, object], ...]) -> list[str]:
        images: list[str] = []
        for asset in assets:
            content = asset.get("content")
            mime_type = str(asset.get("mime_type") or "")
            if not isinstance(content, bytes) or mime_type not in {
                "image/png",
                "image/jpeg",
                "image/webp",
            }:
                raise CoverProviderError(
                    "COVER_INPUT_INVALID", "封面输入素材不可读取", retryable=False
                )
            images.append(
                f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}"
            )
        if not images:
            raise CoverProviderError(
                "COVER_INPUT_REQUIRED", "真实封面生成至少需要一张授权素材", retryable=False
            )
        return images

    @staticmethod
    def _decode_image(value: object) -> tuple[bytes, str]:
        if not isinstance(value, str):
            raise CoverProviderError(
                "COVER_RESPONSE_INVALID", "图片服务返回了无效结果", retryable=False
            )
        try:
            content = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise CoverProviderError(
                "COVER_RESPONSE_INVALID", "图片服务返回了无效结果", retryable=False
            ) from exc
        if not content or len(content) > 20 * 1024 * 1024:
            raise CoverProviderError(
                "COVER_RESPONSE_INVALID", "图片服务返回了无效结果", retryable=False
            )
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return content, "image/png"
        if content.startswith(b"\xff\xd8"):
            return content, "image/jpeg"
        if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return content, "image/webp"
        raise CoverProviderError(
            "COVER_RESPONSE_INVALID", "图片服务返回了不支持的图片格式", retryable=False
        )

    def _generate_one(
        self,
        request: CoverGenerationRequest,
        variant_no: int,
        images: list[str],
    ) -> tuple[CoverVariant, str]:
        try:
            response = self.client.post(
                "images/generations",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model_id,
                    "prompt": (
                        f"{request.prompt}\n生成第 {variant_no} 个差异化方案；"
                        "保持主体特征，不添加平台水印之外的品牌标识。"
                    ),
                    "image": images,
                    "size": f"{request.width}x{request.height}",
                    "sequential_image_generation": "disabled",
                    "response_format": "b64_json",
                    "stream": False,
                    "watermark": True,
                },
            )
        except httpx.TimeoutException as exc:
            raise CoverProviderError(
                "COVER_PROVIDER_TIMEOUT", "图片服务请求超时", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise CoverProviderError(
                "COVER_PROVIDER_REQUEST_FAILED", "图片服务请求失败", retryable=True
            ) from exc

        if response.status_code in {401, 403}:
            raise CoverProviderError(
                "COVER_PROVIDER_AUTH_FAILED", "图片服务密钥无效或权限不足", retryable=False
            )
        if response.status_code == 429:
            raise CoverProviderError(
                "COVER_PROVIDER_RATE_LIMITED", "图片服务请求过于频繁，请稍后重试"
            )
        if response.status_code in {400, 422}:
            raise CoverProviderError(
                "COVER_PROVIDER_INPUT_REJECTED", "图片服务拒绝了生成参数", retryable=False
            )
        if response.status_code >= 400:
            raise CoverProviderError(
                "COVER_PROVIDER_REQUEST_FAILED",
                "图片服务暂时不可用",
                retryable=response.status_code >= 500,
            )
        try:
            payload = response.json()
            data = payload.get("data")
            usage = payload.get("usage")
            if (
                not isinstance(data, list)
                or len(data) != 1
                or not isinstance(data[0], dict)
                or not isinstance(usage, dict)
                or usage.get("generated_images") != 1
            ):
                raise ValueError("unexpected image response")
            content, mime_type = self._decode_image(data[0].get("b64_json"))
        except (ValueError, TypeError, AttributeError) as exc:
            if isinstance(exc, CoverProviderError):
                raise
            raise CoverProviderError(
                "COVER_RESPONSE_INVALID", "图片服务返回了无效结果", retryable=False
            ) from exc
        request_id = str(
            response.headers.get("x-request-id") or payload.get("id") or ""
        ).strip()[:160]
        return CoverVariant(
            variant_no=variant_no,
            content=content,
            mime_type=mime_type,
            width=request.width,
            height=request.height,
        ), request_id

    def generate(self, request: CoverGenerationRequest) -> CoverGenerationBatch:
        images = self._input_images(request.input_assets)
        variants: list[CoverVariant] = []
        failed: list[int] = []
        request_ids: list[str] = []
        numbers = list(request.variant_numbers)
        for index, variant_no in enumerate(numbers):
            try:
                variant, request_id = self._generate_one(request, variant_no, images)
                variants.append(variant)
                if request_id:
                    request_ids.append(request_id)
            except CoverProviderError as exc:
                if not exc.retryable:
                    raise
                failed.extend(numbers[index:])
                break
        return CoverGenerationBatch(
            request_id=",".join(request_ids)[:160] or "seedream-no-request-id",
            provider=self.provider_id,
            model=self.model_id,
            variants=tuple(variants),
            failed_variants=tuple(failed),
            estimated_cost_cny=round(len(variants) * self.cost_cny_per_image, 4),
        )

    def close(self) -> None:
        self.client.close()
