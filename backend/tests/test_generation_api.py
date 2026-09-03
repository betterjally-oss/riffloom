from __future__ import annotations

from dataclasses import replace
import sys
from types import ModuleType, SimpleNamespace

from sqlalchemy import func, select

from app.core.config import Settings
from app.main import _build_generation_provider
from app.models import (
    BreakdownRecord,
    BreakdownVersion,
    CollectionRecord,
    KnowledgeChunk,
    MediaAsset,
    ModelCall,
    ProviderCall,
)
from app.models.entities import new_id
from app.providers import VideoAnalysisResult
from app.providers.models import (
    MockGenerationProvider,
    OpenAIGenerationProvider,
    RoutedGenerationProvider,
    VolcengineMultimodalEmbeddingProvider,
    _strict_json_schema,
)
from app.schemas.generation import (
    AgentChatOutputV1,
    AgentChatOutputV2,
    BreakdownOutputV1,
)
from conftest import wait_for_terminal


def _collect_one(client, headers, key="phase3-source-001") -> str:
    response = client.post(
        "/api/v1/collection-tasks",
        headers={**headers, "Idempotency-Key": key},
        json={
            "kind": "single",
            "platform": "riffloom-sandbox",
            "query": {"url": "https://sandbox.riffloom.local/notes/note-001"},
            "usage_confirmed": True,
            "limit": 1,
        },
    )
    assert response.status_code == 202
    task = wait_for_terminal(client, response.json()["id"], headers)
    assert task["status"] == "success"
    return next(ref["id"] for ref in task["result_refs"] if ref["type"] == "collection")


def _breakdown(client, headers, source_id: str | None = None, key="phase3-breakdown-001"):
    response = client.post(
        "/api/v1/breakdown-tasks",
        headers={**headers, "Idempotency-Key": key},
        json={
            "prompt": "拆解这段内容为什么能让读者产生继续阅读的动机",
            "source_ids": [source_id] if source_id else [],
        },
    )
    assert response.status_code == 202
    task = wait_for_terminal(client, response.json()["id"], headers)
    assert task["status"] == "success"
    return task


def test_collection_drawer_breakdown_uses_saved_video_and_links_current_result(client, headers):
    source_id = _collect_one(client, headers, key="drawer-video-source-001")
    asset_id = new_id("asset")
    with client.app.state.database.session_factory() as session:
        source = session.get(CollectionRecord, source_id)
        assert source is not None
        source.content_type = "视频"
        source.video_transcript = "先用一句情绪冲突开场，再通过现场演唱推进。"
        source.video_transcript_status = "complete"
        source.media_refs = [
            {
                "type": "video_attachment",
                "url": f"/api/v1/media-assets/{asset_id}/content",
                "asset_id": asset_id,
                "mime_type": "video/mp4",
                "duration_seconds": "20",
            }
        ]
        session.add(
            MediaAsset(
                id=asset_id,
                workspace_id="ws_demo",
                storage_key=f"test/{asset_id}.mp4",
                original_name="source.mp4",
                mime_type="video/mp4",
                byte_size=1024,
                width=1080,
                height=1920,
                role="reference",
                rights_status="approved",
                content_hash="drawer-video-analysis-hash",
                created_by="user_demo",
            )
        )
        session.commit()

    class Presigner:
        enabled = True

        def presigned_get(self, object_key, *, ttl_seconds):
            assert object_key.endswith(f"{asset_id}.mp4")
            assert ttl_seconds > 0
            return "https://tos.example.test/source.mp4?signature=test"

    class Analyzer:
        provider_id = "fake-video-analysis-v1"

        def analyze_video(self, request, *, transcript):
            assert request.source_url == "https://tos.example.test/source.mp4?signature=test"
            assert request.duration_seconds == 20
            assert "情绪冲突" in transcript
            return VideoAnalysisResult(
                analysis={
                    "summary": "现场演唱结合逐句字幕",
                    "timeline": [{"start": 0, "end": 3, "role": "开头钩子"}],
                    "visual_hooks": ["人物近景"],
                    "editing": ["长镜头"],
                    "audio": ["现场演唱"],
                    "onscreen_text": ["逐句字幕"],
                },
                provider=self.provider_id,
                model="doubao-seed-2-0-lite-260428",
                provider_request_id="video-analysis-request-1",
                elapsed_ms=12,
            )

    client.app.state.worker.media_presigner = Presigner()
    client.app.state.worker.transcription_provider = Analyzer()
    response = client.post(
        "/api/v1/breakdown-tasks",
        headers={**headers, "Idempotency-Key": "drawer-breakdown-current-v1"},
        json={
            "prompt": "生成 800～1500 字的精简爆款拆解",
            "source_ids": [source_id],
            "preset": "drawer_compact",
        },
    )
    task = wait_for_terminal(client, response.json()["id"], headers)

    assert task["status"] == "success"
    breakdown_id = next(ref["id"] for ref in task["result_refs"] if ref["type"] == "breakdown")
    with client.app.state.database.session_factory() as session:
        record = session.get(BreakdownRecord, breakdown_id)
        assert record is not None
        version = session.get(BreakdownVersion, record.current_version_id)
        assert version is not None
        assert version.source_snapshot["video_analysis"]["visual_hooks"] == ["人物近景"]
        call = session.scalar(
            select(ProviderCall).where(
                ProviderCall.task_id == task["id"],
                ProviderCall.operation == "breakdown_video_analysis",
            )
        )
        assert call is not None

    detail = client.get(f"/api/v1/collections/{source_id}", headers=headers).json()
    assert detail["breakdown_id"] == breakdown_id
    assert detail["breakdown_is_current"] is True


def test_real_provider_schema_marks_every_object_field_required():
    schema = _strict_json_schema(BreakdownOutputV1)

    def assert_strict(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                assert_strict(value)
        elif isinstance(node, list):
            for value in node:
                assert_strict(value)

    assert_strict(schema)


def test_real_provider_schema_inlines_refs_inside_any_of_for_deepseek():
    schema = _strict_json_schema(AgentChatOutputV2)

    action = schema["properties"]["action"]["anyOf"][0]
    assert "$ref" not in action
    assert action["type"] == "object"
    assert "name" in action["properties"]


def test_real_provider_repairs_one_invalid_structured_result():
    payload = {
        "prompt": "拆解内容",
        "source_title": "测试",
        "source_text": "先保存来源，再拆解结构，最后形成可修改版本。",
    }
    valid = (
        MockGenerationProvider()
        .generate("breakdown", payload, BreakdownOutputV1)
        .output.model_dump_json()
    )

    class FakeResponses:
        def __init__(self):
            self.outputs = ["{}", valid]

        def create(self, **_kwargs):
            return SimpleNamespace(
                output_text=self.outputs.pop(0),
                usage=SimpleNamespace(input_tokens=10, output_tokens=20),
                model="gpt-5.6-terra",
                id="resp_test",
            )

    provider = OpenAIGenerationProvider.__new__(OpenAIGenerationProvider)
    provider.model_id = "gpt-5.6-terra"
    provider.embedding_model_id = "text-embedding-3-small"
    provider.reasoning_effort = "low"
    provider.max_output_tokens = 5000
    provider.max_repairs = 1
    provider.client = SimpleNamespace(responses=FakeResponses())

    result = provider.generate("breakdown", payload, BreakdownOutputV1)
    assert result.repair_count == 1
    assert isinstance(result.output, BreakdownOutputV1)


def test_volcengine_251215_embedding_uses_multimodal_api_for_each_text():
    class FakeResponse:
        def __init__(self, index: int):
            self.index = index

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "model": "doubao-embedding-vision-251215",
                "data": {"object": "embedding", "embedding": [self.index, 0.5]},
                "usage": {"total_tokens": 10 + self.index},
            }

    class FakeClient:
        def __init__(self):
            self.requests = []

        def post(self, path, *, json):
            self.requests.append((path, json))
            return FakeResponse(len(self.requests))

    client = FakeClient()
    provider = VolcengineMultimodalEmbeddingProvider(
        api_key="test-ark-key",
        model="doubao-embedding-vision-251215",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        timeout_seconds=30,
        client=client,
    )

    result = provider.embed(["第一段", "第二段"])

    assert client.requests == [
        (
            "https://ark.cn-beijing.volces.com/api/v3/embeddings/multimodal",
            {
                "model": "doubao-embedding-vision-251215",
                "input": [{"type": "text", "text": "第一段"}],
                "encoding_format": "float",
            },
        ),
        (
            "https://ark.cn-beijing.volces.com/api/v3/embeddings/multimodal",
            {
                "model": "doubao-embedding-vision-251215",
                "input": [{"type": "text", "text": "第二段"}],
                "encoding_format": "float",
            },
        ),
    ]
    assert result.vectors == [[1.0, 0.5], [2.0, 0.5]]
    assert result.input_tokens == 23


class RecordingProvider(MockGenerationProvider):
    def __init__(self, provider_id: str, model_id: str):
        self.provider_id = provider_id
        self.model_id = model_id
        self.embedding_model_id = f"{model_id}-embedding"
        self.operations: list[str] = []

    def generate(self, operation, payload, output_schema):
        self.operations.append(operation)
        result = super().generate(operation, payload, output_schema)
        return replace(result, provider=self.provider_id, model=self.model_id)


def test_routed_provider_uses_vision_only_for_authorized_breakdown_media():
    text = RecordingProvider("deepseek-v1", "deepseek-v4-flash")
    vision = RecordingProvider("volcengine-vision-v1", "doubao-seed-2-0-lite")
    router = RoutedGenerationProvider(
        text_provider=text,
        vision_provider=vision,
        embedding_provider=vision,
    )
    payload = {
        "prompt": "拆解图文",
        "media_refs": [
            {
                "type": "image",
                "url": "https://media.example.test/cover.jpg",
                "authorized": True,
            }
        ],
    }

    result = router.generate("breakdown", payload, BreakdownOutputV1)
    assert result.provider == "volcengine-vision-v1"
    assert vision.operations == ["breakdown"]
    assert text.operations == []

    payload["media_refs"][0]["authorized"] = False
    result = router.generate("breakdown", payload, BreakdownOutputV1)
    assert result.provider == "deepseek-v1"
    assert text.operations == ["breakdown"]

    result = router.generate(
        "agent_chat",
        {"prompt": "你好", "available_capabilities": ["文案原创"]},
        AgentChatOutputV1,
    )
    assert result.provider == "deepseek-v1"
    assert text.operations == ["breakdown", "agent_chat"]


def test_deepseek_agent_uses_pro_for_complex_prompts_and_flash_failures():
    class FakeResponses:
        def __init__(self, *, fail_flash: bool = False):
            self.fail_flash = fail_flash
            self.models: list[str] = []

        def create(self, **kwargs):
            model = kwargs["model"]
            self.models.append(model)
            if self.fail_flash and model == "deepseek-v4-flash":
                raise RuntimeError("flash unavailable")
            return SimpleNamespace(
                output_text='{"reply":"已完成分析"}',
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                model=model,
                id="resp_agent",
            )

    def provider(responses: FakeResponses) -> OpenAIGenerationProvider:
        instance = OpenAIGenerationProvider.__new__(OpenAIGenerationProvider)
        instance.model_id = "deepseek-v4-flash"
        instance.agent_pro_model = "deepseek-v4-pro"
        instance.reasoning_effort = "none"
        instance.reasoning_by_operation = {}
        instance.max_output_tokens = 5000
        instance.max_repairs = 0
        instance.supports_images = False
        instance.client = SimpleNamespace(responses=responses)
        return instance

    complex_responses = FakeResponses()
    result = provider(complex_responses).generate(
        "agent_chat",
        {"prompt": "请深入分析这个选题并给出执行建议"},
        AgentChatOutputV1,
    )
    assert result.model == "deepseek-v4-pro"
    assert complex_responses.models == ["deepseek-v4-pro"]

    fallback_responses = FakeResponses(fail_flash=True)
    result = provider(fallback_responses).generate(
        "agent_chat",
        {"prompt": "你好"},
        AgentChatOutputV1,
    )
    assert result.model == "deepseek-v4-pro"
    assert fallback_responses.models == ["deepseek-v4-flash", "deepseek-v4-pro"]


def test_routed_provider_factory_freezes_confirmed_defaults_without_network_calls(
    monkeypatch,
):
    fake_openai = ModuleType("openai")

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            pass

    fake_openai.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    provider = _build_generation_provider(
        Settings(
            model_provider="routed-v1",
            deepseek_api_key="test-deepseek-key",
            volcengine_ark_api_key="test-ark-key",
        )
    )
    assert isinstance(provider, RoutedGenerationProvider)
    assert provider.text_provider.agent_pro_model == "deepseek-v4-pro"
    assert provider.routing_summary() == {
        "provider": "routed-v1",
        "text": {"provider": "deepseek-v1", "model": "deepseek-v4-flash"},
        "vision": {
            "provider": "volcengine-vision-v1",
            "model": "doubao-seed-2-0-lite-260215",
        },
        "embedding": {
            "provider": "volcengine-embedding-v1",
            "model": "doubao-embedding-vision-251215",
        },
    }


def test_seven_versioned_skills_are_exposed(client, headers):
    response = client.get("/api/v1/skills", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 7
    assert {item["id"] for item in data["items"]} == {
        "riffloom_agent",
        "viral_breakdown",
        "viral_topic_coach",
        "collect_content",
        "copy_rewrite",
        "collect_breakdown_rewrite",
        "original_copy",
    }
    versions = {item["id"]: item["version"] for item in data["items"]}
    assert versions["viral_topic_coach"] == "3.0.0"
    assert versions["riffloom_agent"] == "2.0.0"
    assert all(
        version == "1.0.0"
        for skill_id, version in versions.items()
        if skill_id not in {"viral_topic_coach", "riffloom_agent"}
    )
    assert all(1 <= item["max_steps"] <= 4 for item in data["items"])


def test_temporary_text_breakdown_only_enters_breakdown_library(client, headers):
    task = _breakdown(client, headers)
    breakdown_id = task["result_refs"][0]["id"]
    assert task["result_summary"]["provider"] == "mock-v1"

    assert client.get("/api/v1/libraries/collections", headers=headers).json()["total"] == 0
    assert client.get("/api/v1/libraries/breakdowns", headers=headers).json()["total"] == 1
    assert client.get("/api/v1/libraries/creations", headers=headers).json()["total"] == 0

    detail = client.get(f"/api/v1/breakdowns/{breakdown_id}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["source_record_id"] is None
    assert body["current_version_id"] == body["versions"][0]["id"]
    assert body["versions"][0]["visual"]["limitations"]
    assert body["versions"][0]["skill_snapshot"]["id"] == "viral_breakdown"


def test_original_creation_uses_selected_knowledge_and_stays_in_creation_library(client, headers):
    collection_id = _collect_one(client, headers)
    response = client.post(
        "/api/v1/creation-tasks",
        headers={**headers, "Idempotency-Key": "phase3-original-001"},
        json={
            "creation_type": "original",
            "prompt": "面向内容新人写一篇小红书文案，说明为什么要保存版本",
            "knowledge_refs": [{"library_type": "collections", "record_id": collection_id}],
            "target_platform": "小红书",
            "audience": "刚加入团队的内容新人",
        },
    )
    assert response.status_code == 202
    task = wait_for_terminal(client, response.json()["id"], headers)
    assert task["status"] == "success"
    creation_id = next(ref["id"] for ref in task["result_refs"] if ref["type"] == "creation")
    assert client.get("/api/v1/libraries/breakdowns", headers=headers).json()["total"] == 0
    assert client.get("/api/v1/libraries/creations", headers=headers).json()["total"] == 1

    detail = client.get(f"/api/v1/creations/{creation_id}", headers=headers).json()
    version = detail["versions"][0]
    assert len(version["title_candidates"]) >= 2
    assert version["knowledge_snapshot"]["empty"] is False
    assert version["knowledge_snapshot"]["fragments"][0]["record_id"] == collection_id
    assert version["generation_config"]["model"] == "mock-riffloom-v1"

    with client.app.state.database.session_factory() as session:
        assert session.scalar(select(func.count(KnowledgeChunk.id))) == 1
        assert session.scalar(select(func.count(ModelCall.id))) == 1


def test_rewrite_requires_source_and_breakdown(client, headers):
    missing = client.post(
        "/api/v1/creation-tasks",
        headers={**headers, "Idempotency-Key": "phase3-rewrite-missing"},
        json={"creation_type": "rewrite", "prompt": "仿写成职场新人版本"},
    )
    task = wait_for_terminal(client, missing.json()["id"], headers)
    assert task["status"] == "failed"
    assert task["error"]["code"] == "SOURCE_REQUIRED"

    collection_id = _collect_one(client, headers, "phase3-source-rewrite")
    breakdown_task = _breakdown(client, headers, collection_id, "phase3-breakdown-rewrite")
    breakdown_id = next(
        ref["id"] for ref in breakdown_task["result_refs"] if ref["type"] == "breakdown"
    )
    response = client.post(
        "/api/v1/creation-tasks",
        headers={**headers, "Idempotency-Key": "phase3-rewrite-success"},
        json={
            "creation_type": "rewrite",
            "prompt": "保留结构策略，改写成适合职场新人的原创表达",
            "source_ids": [collection_id, breakdown_id],
        },
    )
    task = wait_for_terminal(client, response.json()["id"], headers)
    assert task["status"] == "success"
    creation_id = next(ref["id"] for ref in task["result_refs"] if ref["type"] == "creation")
    version = client.get(f"/api/v1/creations/{creation_id}", headers=headers).json()["versions"][0]
    assert {ref["type"] for ref in version["source_refs"]} == {
        "collection",
        "breakdown",
    }
    assert version["similarity_report"]["level"] in {"low", "medium", "high"}
    assert "不构成版权" in version["similarity_report"]["note"]


def test_manual_edit_creates_new_version_without_overwriting_adopted_version(client, headers):
    response = client.post(
        "/api/v1/creation-tasks",
        headers={**headers, "Idempotency-Key": "phase3-version-base"},
        json={"creation_type": "original", "prompt": "写一篇关于版本管理的内容"},
    )
    task = wait_for_terminal(client, response.json()["id"], headers)
    creation_id = next(ref["id"] for ref in task["result_refs"] if ref["type"] == "creation")
    before = client.get(f"/api/v1/creations/{creation_id}", headers=headers).json()
    first = before["versions"][0]
    client.post(
        f"/api/v1/creations/{creation_id}/adoptions",
        headers={**headers, "Idempotency-Key": "phase3-adopt-v1"},
        json={"version_id": first["id"]},
    )
    edited_body = first["body"] + "\n\n这是人工补充的新结尾，旧版本正文必须保持不变。"
    edited = client.post(
        f"/api/v1/creations/{creation_id}/versions",
        headers={**headers, "Idempotency-Key": "phase3-manual-v2"},
        json={
            "base_version_id": first["id"],
            "body": edited_body,
            "change_note": "补充行动号召",
        },
    )
    assert edited.status_code == 200
    assert edited.json()["version"] == 2
    after = client.get(f"/api/v1/creations/{creation_id}", headers=headers).json()
    assert after["status"] == "pending_review"
    assert after["adopted_version_id"] == first["id"]
    assert after["current_version_id"] == edited.json()["id"]
    assert after["versions"][0]["body"] == first["body"]
    assert after["versions"][1]["body"] == edited_body


def test_topic_guidance_uses_external_signals_and_does_not_create_library_record(client, headers):
    response = client.post(
        "/api/v1/tasks",
        headers={**headers, "Idempotency-Key": "phase3-topic-001"},
        json={
            "mode": "trend",
            "skill_id": "viral_topic_coach",
            "input": {"prompt": "AI 工作流内容方向"},
        },
    )
    task = wait_for_terminal(client, response.json()["id"], headers)
    assert task["status"] == "success"
    assert task["result_refs"] == []
    assert task["result_summary"]["contract_version"] == "topic-guidance.v3"
    assert task["result_summary"]["freshness"] == "verified_external_signals"
    assert len(task["result_summary"]["candidates"]) == 5
    assert all(item["sources"] for item in task["result_summary"]["candidates"])
    assert client.get("/api/v1/libraries/creations", headers=headers).json()["total"] == 0
