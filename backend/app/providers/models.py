from __future__ import annotations

import copy
import hashlib
import json
import math
import time
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel

from app.schemas.generation import (
    AgentChatOutputV1,
    AgentChatOutputV2,
    AgentToolIntentV2,
    BreakdownOutputV1,
    CreationOutputV1,
    TopicCandidate,
    TopicGuidanceOutputV3,
    TrendSourceCitation,
)

T = TypeVar("T", bound=BaseModel)


class ModelProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class ModelResult:
    output: BaseModel
    provider: str
    model: str
    request_id: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    repair_count: int = 0


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    provider: str
    model: str
    input_tokens: int
    estimated_cost_usd: float


class GenerationProvider(Protocol):
    provider_id: str
    model_id: str

    def generate(
        self,
        operation: str,
        payload: dict[str, Any],
        output_schema: type[T],
    ) -> ModelResult: ...

    def embed(self, texts: list[str]) -> EmbeddingBatch: ...


class EmbeddingProvider(Protocol):
    provider_id: str
    embedding_model_id: str

    def embed(self, texts: list[str]) -> EmbeddingBatch: ...


def _token_estimate(text: str) -> int:
    return max(1, math.ceil(len(text) / 3))


def _deterministic_vector(text: str, dimensions: int = 48) -> list[float]:
    values = [0.0] * dimensions
    normalized = "".join(text.lower().split())
    tokens = [normalized[index : index + 2] for index in range(max(1, len(normalized) - 1))]
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % dimensions
        values[index] += -1.0 if digest[2] % 2 else 1.0
    magnitude = math.sqrt(sum(value * value for value in values)) or 1.0
    return [round(value / magnitude, 8) for value in values]


def _strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    definitions = schema.get("$defs", {})

    def normalize(node: Any) -> None:
        if isinstance(node, dict):
            alternatives = node.get("anyOf")
            if isinstance(alternatives, list):
                for index, alternative in enumerate(alternatives):
                    ref = alternative.get("$ref") if isinstance(alternative, dict) else None
                    if isinstance(ref, str) and ref.startswith("#/$defs/"):
                        alternatives[index] = copy.deepcopy(
                            definitions[ref.removeprefix("#/$defs/")]
                        )
            properties = node.get("properties")
            if node.get("type") == "object" and isinstance(properties, dict):
                node["additionalProperties"] = False
                node["required"] = list(properties)
            for value in node.values():
                normalize(value)
        elif isinstance(node, list):
            for value in node:
                normalize(value)

    normalize(schema)
    return schema


def _authorized_image_urls(payload: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for item in payload.get("media_refs") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in {"image", "cover", "frame"}:
            continue
        if item.get("authorized") is not True and item.get("authorization_status") != "approved":
            continue
        url = str(item.get("url") or "").strip()
        if url.startswith(("https://", "data:image/")):
            urls.append(url)
    return urls


def _agent_needs_pro(payload: dict[str, Any]) -> bool:
    prompt = str(payload.get("prompt") or "")
    history = payload.get("conversation_history") or []
    complex_markers = (
        "深入分析",
        "完整方案",
        "系统规划",
        "分步骤",
        "多步骤",
        "权衡利弊",
        "逐步推理",
        "综合评估",
    )
    # ponytail: zero-cost heuristic; replace with a classifier only after measured misrouting.
    return (
        len(prompt) >= 600
        or len(history) >= 9
        or any(marker in prompt for marker in complex_markers)
    )


class MockGenerationProvider:
    provider_id = "mock-v1"
    model_id = "mock-riffloom-v1"
    embedding_model_id = "mock-embedding-v1"

    def generate(
        self,
        operation: str,
        payload: dict[str, Any],
        output_schema: type[T],
    ) -> ModelResult:
        started = time.monotonic()
        prompt = str(payload.get("prompt") or payload.get("source_text") or "内容创作")
        if "[model-timeout]" in prompt:
            raise ModelProviderError("MODEL_TIMEOUT", "mock 模型调用超时")
        if operation == "agent_chat":
            capabilities = [
                str(item)
                for item in payload.get("available_capabilities") or []
                if str(item).strip()
            ]
            lowered = prompt.lower()
            urls = [part for part in prompt.split() if part.startswith("https://")]
            source_ids = [str(item) for item in payload.get("source_ids") or []]
            action = None
            if "采集仿写" in prompt and urls:
                action = AgentToolIntentV2(
                    name="collect_rewrite",
                    prompt=prompt,
                    url=urls[0],
                    collection_kind="single",
                )
            elif "采集" in prompt and urls:
                action = AgentToolIntentV2(
                    name="collect", prompt=prompt, url=urls[0], collection_kind="single"
                )
            elif "拆解" in prompt and source_ids:
                action = AgentToolIntentV2(name="breakdown", prompt=prompt, source_ids=source_ids)
            elif "仿写" in prompt and source_ids:
                action = AgentToolIntentV2(name="rewrite", prompt=prompt, source_ids=source_ids)
            elif "原创" in prompt:
                action = AgentToolIntentV2(name="original", prompt=prompt, source_ids=source_ids)
            elif any(marker in lowered for marker in ("热点", "选题", "trend")):
                action = AgentToolIntentV2(name="trend", prompt=prompt)
            reply = (
                "你好呀！我是 Riffloom 智能体 👋\n\n"
                "目前我可以帮你：\n"
                + "\n".join(f"• {item}" for item in capabilities)
                + "\n\n你想先从哪件事开始？"
            )
            output: BaseModel = (
                AgentChatOutputV1(reply=reply)
                if output_schema is AgentChatOutputV1
                else AgentChatOutputV2(reply=reply, action=action)
            )
        elif operation == "breakdown":
            output = BreakdownOutputV1(
                title=f"{str(payload.get('source_title') or '临时内容')[:36]} · 结构化拆解",
                observed_facts=[
                    f"输入正文长度约 {len(str(payload.get('source_text') or prompt))} 字",
                    "文本以问题或反差建立开头注意力",
                ],
                hook={
                    "type": "问题反差",
                    "expression": "先指出常见投入与结果之间的落差",
                    "why_effective": "让目标受众迅速对照自身处境，并产生继续阅读的动机",
                },
                structure=[
                    "提出具体困境",
                    "解释失效原因",
                    "给出分步方法",
                    "收束为可执行动作",
                ],
                emotion={
                    "target": "从焦虑转向可控",
                    "turn": "先承认无效努力，再把问题拆成可处理步骤",
                    "action_driver": "用低门槛的第一步降低行动阻力",
                },
                visual={
                    "observed": [],
                    "limitations": ["当前 mock 链路未提供可观察图片，不能判断封面、构图和字幕"],
                },
                interaction=["结尾用具体问题邀请分享", "让读者选择最需要改善的步骤"],
                reusable_methods=[
                    "用投入与结果的反差开场",
                    "每段只推进一个信息点",
                    "结尾给出单一行动",
                ],
                risks=[
                    "示例未接入真实平台指标，不应宣称为实时爆款",
                    "方法结论需要结合账号受众人工审核",
                ],
            )
        elif operation == "topic_guidance":
            signal_items = [
                item
                for item in (payload.get("signal_snapshot") or {}).get("items", [])
                if isinstance(item, dict)
            ]
            citations = [
                TrendSourceCitation(
                    platform=str(item["platform"]),
                    source_id=str(item["source_id"]),
                    observed_at=str(item["observed_at"]),
                )
                for item in signal_items[:5]
            ]
            count = min(15, max(1, int(payload.get("result_count") or 5)))
            angles = [
                "从反常识问题切入",
                "用低门槛清单切入",
                "从真实使用成本切入",
                "用前后对比切入",
                "从普通人的机会窗口切入",
            ]
            output = TopicGuidanceOutputV3(
                candidates=[
                    TopicCandidate(
                        topic=f"{prompt[:24]}热点方向 {index + 1}",
                        why_hot="近一周多个公开内容平台出现相关讨论与互动信号。",
                        audience="希望提升内容效率的内容创作者",
                        core_value="帮助用户快速判断这个话题是否值得跟进，并找到可执行的内容方向。",
                        title_suggestion=f"《{prompt[:20]}：普通创作者现在怎么抓住机会》",
                        angle=angles[index % len(angles)],
                        sources=[citations[index % len(citations)]],
                    )
                    for index in range(count)
                ],
                caveat="热点依据来自近期开源平台信号，发布前仍需人工核对具体事实。",
            )
        else:
            sources = payload.get("knowledge_fragments") or []
            citations = [
                {
                    "library_type": item["library_type"],
                    "record_id": item["record_id"],
                    "record_version": item["record_version"],
                    "field_name": item["field_name"],
                }
                for item in sources[:6]
            ]
            kind = str(payload.get("creation_type") or "original")
            body_prefix = (
                "参考来源的结构策略重新组织" if kind != "original" else "围绕明确选题从受众问题出发"
            )
            output = CreationOutputV1(
                title_candidates=[
                    f"别再堆工具了，先把这一步做对：{prompt[:18]}",
                    f"把复杂工作变简单，我只保留了这 3 步",
                    f"同样的素材，为什么有人能稳定产出？",
                ],
                summary=f"{body_prefix}，形成可审核的阶段 3 mock 稿件。",
                body=(
                    "你可能已经收藏了很多方法，却依然很难稳定产出。\n\n"
                    "问题通常不是工具不够，而是输入、判断和沉淀没有连成一条线。先固定目标受众与交付格式，"
                    "再拆解素材为什么有效，最后把结论保存成可以继续修改的版本。\n\n"
                    "今天先做一个最小动作：选一条真实素材，只提炼一个可复用结构，然后写出第一版。"
                    "版本不需要完美，但必须能回到来源、看见风险，也能继续迭代。\n\n"
                    "你现在最想先解决采集、拆解，还是创作中的哪一步？"
                ),
                topics=["内容工作流", "内容团队", "AI 创作"],
                used_sources=citations,
                risk_notes=[
                    "当前为 mock-v1 结果，不能代表真实模型质量",
                    "涉及外部事实时需要补充可核验来源",
                ],
            )
        parsed = output_schema.model_validate(output.model_dump())
        raw_input = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        raw_output = parsed.model_dump_json()
        return ModelResult(
            output=parsed,
            provider=self.provider_id,
            model=self.model_id,
            request_id=f"mock_{hashlib.sha256(raw_input.encode()).hexdigest()[:16]}",
            latency_ms=max(1, int((time.monotonic() - started) * 1000)),
            input_tokens=_token_estimate(raw_input),
            output_tokens=_token_estimate(raw_output),
            estimated_cost_usd=0.0,
        )

    def embed(self, texts: list[str]) -> EmbeddingBatch:
        return EmbeddingBatch(
            vectors=[_deterministic_vector(text) for text in texts],
            provider=self.provider_id,
            model=self.embedding_model_id,
            input_tokens=sum(_token_estimate(text) for text in texts),
            estimated_cost_usd=0.0,
        )


class OpenAIGenerationProvider:
    provider_id = "openai-v1"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        embedding_model: str,
        timeout_seconds: float,
        reasoning_effort: str,
        max_output_tokens: int,
        max_repairs: int,
        provider_id: str = "openai-v1",
        base_url: str | None = None,
        api_key_label: str = "OPENAI_API_KEY",
        reasoning_by_operation: dict[str, str] | None = None,
        agent_pro_model: str | None = None,
        supports_images: bool = True,
        input_cost_per_million: float = 2.0,
        output_cost_per_million: float = 12.0,
        embedding_cost_per_million: float = 0.02,
    ):
        if not api_key:
            raise ModelProviderError(
                "MODEL_NOT_CONFIGURED",
                f"{api_key_label} 尚未在服务端配置",
                retryable=False,
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ModelProviderError(
                "MODEL_SDK_MISSING", "后端尚未安装 OpenAI 官方 SDK", retryable=False
            ) from exc
        self.provider_id = provider_id
        self.model_id = model
        self.embedding_model_id = embedding_model
        self.reasoning_effort = reasoning_effort
        self.reasoning_by_operation = reasoning_by_operation or {}
        self.agent_pro_model = agent_pro_model
        self.max_output_tokens = max_output_tokens
        self.max_repairs = max(0, max_repairs)
        self.supports_images = supports_images
        self.input_cost_per_million = input_cost_per_million
        self.output_cost_per_million = output_cost_per_million
        self.embedding_cost_per_million = embedding_cost_per_million
        client_kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout_seconds}
        if base_url:
            client_kwargs["base_url"] = base_url.rstrip("/")
        self.client = OpenAI(**client_kwargs)

    def generate(
        self,
        operation: str,
        payload: dict[str, Any],
        output_schema: type[T],
    ) -> ModelResult:
        started = time.monotonic()
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
        user_content: str | list[dict[str, Any]] = serialized
        image_urls = (
            _authorized_image_urls(payload) if getattr(self, "supports_images", True) else []
        )
        if image_urls:
            user_content = [
                {"type": "input_text", "text": serialized},
                *[{"type": "input_image", "image_url": image_url} for image_url in image_urls],
            ]
        system_prompt = "你是 Riffloom 内容工作台。严格依据输入生成，不编造来源或不可观察事实。"
        if operation == "agent_chat":
            system_prompt = (
                "你是 Riffloom 智能体，面向内容团队与创作者进行自然对话。"
                "只能介绍或承诺 available_capabilities 中已上线的能力；"
                "每轮最多输出一个 action；可选 reply、breakdown、original、rewrite、trend、collect、collect_rewrite。"
                "新链接的 collect 或 collect_rewrite 只能建议确认，不得声称已执行；"
                "不完整请求使用 reply，不得声称已经执行任何未实际调用的工具。"
                "回答使用简洁中文，并结合 conversation_history 承接上下文。"
            )
        elif operation == "topic_guidance":
            system_prompt = (
                "你是 Riffloom 热点选题分析师。只能依据 signal_snapshot 中的近期开源平台信号，"
                "输出数量必须等于 result_count，最多 15 条；按热度与创作价值排序。"
                "每条固定给出热点名称、为什么火、目标人群、核心价值、标题建议、我的切入点；"
                "sources 只能逐字引用 signal_snapshot 中已有的 platform、source_id、observed_at，"
                "不得编造实时热度、来源或数字。"
            )
        repair_count = 0
        response = None
        request_model = (
            self.agent_pro_model
            if operation == "agent_chat" and self.agent_pro_model and _agent_needs_pro(payload)
            else self.model_id
        )

        def create_response():
            return self.client.responses.create(
                model=request_model,
                reasoning={
                    "effort": getattr(self, "reasoning_by_operation", {}).get(
                        operation, self.reasoning_effort
                    )
                },
                max_output_tokens=self.max_output_tokens,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": f"riffloom_{operation}_v1",
                        "schema": _strict_json_schema(output_schema),
                        "strict": True,
                    }
                },
            )

        for attempt in range(self.max_repairs + 1):
            try:
                response = create_response()
            except Exception as exc:
                if (
                    operation != "agent_chat"
                    or not self.agent_pro_model
                    or request_model == self.agent_pro_model
                ):
                    raise ModelProviderError("MODEL_CALL_FAILED", "真实模型调用失败") from exc
                request_model = self.agent_pro_model
                try:
                    response = create_response()
                except Exception as fallback_exc:
                    raise ModelProviderError(
                        "MODEL_CALL_FAILED", "真实模型调用失败"
                    ) from fallback_exc
            try:
                parsed = output_schema.model_validate_json(response.output_text)
                break
            except (ValueError, TypeError) as exc:
                if attempt >= self.max_repairs:
                    raise ModelProviderError(
                        "MODEL_OUTPUT_INVALID",
                        "真实模型结构化结果校验失败",
                        retryable=False,
                    ) from exc
                repair_count += 1
                serialized = json.dumps(
                    {
                        "original_input": payload,
                        "invalid_output": response.output_text,
                        "repair_instruction": "只修复为符合给定 JSON Schema 的完整结果，不添加 schema 外字段。",
                    },
                    ensure_ascii=False,
                    default=str,
                )
                user_content = serialized
        if response is None:
            raise ModelProviderError("MODEL_CALL_FAILED", "真实模型未返回结果")
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        estimated_cost = input_tokens / 1_000_000 * getattr(
            self, "input_cost_per_million", 2.0
        ) + output_tokens / 1_000_000 * getattr(self, "output_cost_per_million", 12.0)
        return ModelResult(
            output=parsed,
            provider=self.provider_id,
            model=str(getattr(response, "model", self.model_id)),
            request_id=str(getattr(response, "id", "")),
            latency_ms=int((time.monotonic() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=round(estimated_cost, 8),
            repair_count=repair_count,
        )

    def embed(self, texts: list[str]) -> EmbeddingBatch:
        try:
            response = self.client.embeddings.create(model=self.embedding_model_id, input=texts)
        except Exception as exc:
            raise ModelProviderError("EMBEDDING_CALL_FAILED", "真实 Embedding 调用失败") from exc
        tokens = int(getattr(response.usage, "total_tokens", 0) or 0)
        return EmbeddingBatch(
            vectors=[list(item.embedding) for item in response.data],
            provider=self.provider_id,
            model=str(response.model),
            input_tokens=tokens,
            estimated_cost_usd=round(
                tokens / 1_000_000 * getattr(self, "embedding_cost_per_million", 0.02),
                8,
            ),
        )


class VolcengineMultimodalEmbeddingProvider:
    """Calls Ark's multimodal embedding API used by Seed 1.6 251215.

    This model does not support the OpenAI-compatible `/embeddings` endpoint.
    Ark returns one embedding object per multimodal request, so text batches are
    sent as one request per text and assembled behind Riffloom's batch boundary.
    """

    provider_id = "volcengine-embedding-v1"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str,
        timeout_seconds: float,
        embedding_cost_per_million: float = 0.0,
        client: Any | None = None,
    ):
        if not api_key:
            raise ModelProviderError(
                "MODEL_NOT_CONFIGURED",
                "ARK_API_KEY 尚未在服务端配置",
                retryable=False,
            )
        self.model_id = model
        self.embedding_model_id = model
        self.embedding_cost_per_million = embedding_cost_per_million
        self.endpoint_url = f"{base_url.rstrip('/')}/embeddings/multimodal"
        self.client = client or httpx.Client(
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def embed(self, texts: list[str]) -> EmbeddingBatch:
        vectors: list[list[float]] = []
        input_tokens = 0
        response_model = self.embedding_model_id
        try:
            for text in texts:
                response = self.client.post(
                    self.endpoint_url,
                    json={
                        "model": self.embedding_model_id,
                        "input": [{"type": "text", "text": text}],
                        "encoding_format": "float",
                    },
                )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data")
                embedding = data.get("embedding") if isinstance(data, dict) else None
                if not isinstance(embedding, list) or not embedding:
                    raise ModelProviderError(
                        "EMBEDDING_OUTPUT_INVALID",
                        "火山多模态 Embedding 返回为空或格式错误",
                        retryable=False,
                    )
                vectors.append([float(value) for value in embedding])
                usage = payload.get("usage")
                if isinstance(usage, dict):
                    input_tokens += int(usage.get("total_tokens") or 0)
                response_model = str(payload.get("model") or response_model)
        except ModelProviderError:
            raise
        except Exception as exc:
            raise ModelProviderError("EMBEDDING_CALL_FAILED", "真实 Embedding 调用失败") from exc
        return EmbeddingBatch(
            vectors=vectors,
            provider=self.provider_id,
            model=response_model,
            input_tokens=input_tokens,
            estimated_cost_usd=round(
                input_tokens / 1_000_000 * self.embedding_cost_per_million,
                8,
            ),
        )


class RoutedGenerationProvider:
    provider_id = "routed-v1"

    def __init__(
        self,
        *,
        text_provider: GenerationProvider,
        embedding_provider: EmbeddingProvider,
        vision_provider: GenerationProvider | None = None,
        vision_fallback_provider: GenerationProvider | None = None,
    ):
        self.text_provider = text_provider
        self.embedding_provider = embedding_provider
        self.vision_provider = vision_provider
        self.vision_fallback_provider = vision_fallback_provider
        self.model_id = text_provider.model_id
        self.embedding_model_id = getattr(
            embedding_provider, "embedding_model_id", embedding_provider.model_id
        )

    def route_for(self, operation: str, payload: dict[str, Any]) -> GenerationProvider:
        if (
            operation in {"agent_chat", "breakdown", "creation"}
            and _authorized_image_urls(payload)
            and self.vision_provider is not None
        ):
            return self.vision_provider
        return self.text_provider

    def generate(
        self,
        operation: str,
        payload: dict[str, Any],
        output_schema: type[T],
    ) -> ModelResult:
        provider = self.route_for(operation, payload)
        try:
            return provider.generate(operation, payload, output_schema)
        except ModelProviderError:
            fallback = self._fallback_for(provider)
            if fallback is None:
                raise
            return fallback.generate(operation, payload, output_schema)

    def _fallback_for(self, provider: GenerationProvider) -> GenerationProvider | None:
        if provider is self.vision_provider:
            return self.vision_fallback_provider
        return None

    def embed(self, texts: list[str]) -> EmbeddingBatch:
        return self.embedding_provider.embed(texts)

    def routing_summary(self) -> dict[str, Any]:
        return {
            "provider": self.provider_id,
            "text": {
                "provider": self.text_provider.provider_id,
                "model": self.text_provider.model_id,
            },
            "vision": (
                {
                    "provider": self.vision_provider.provider_id,
                    "model": self.vision_provider.model_id,
                }
                if self.vision_provider
                else None
            ),
            "embedding": {
                "provider": self.embedding_provider.provider_id,
                "model": self.embedding_model_id,
            },
        }
