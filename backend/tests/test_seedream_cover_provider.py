from __future__ import annotations

import base64
import json

import httpx

from app.providers import (
    CoverGenerationRequest,
    CoverProviderError,
    VolcengineSeedreamCoverProvider,
)
from conftest import wait_for_terminal
from test_phase4b_cover_api import PNG_1X1, _upload


def _provider(handler) -> VolcengineSeedreamCoverProvider:
    return VolcengineSeedreamCoverProvider(
        api_key="test-secret-key",
        client=httpx.Client(
            base_url="https://ark.cn-beijing.volces.com/api/v3/",
            transport=httpx.MockTransport(handler),
        ),
    )


def test_seedream_requests_exact_watermarked_variants_and_tracks_cny_cost():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"x-request-id": f"req-{len(requests)}"},
            json={
                "data": [{"b64_json": PNG_1X1}],
                "usage": {"generated_images": 1},
            },
        )

    provider = _provider(handler)
    try:
        batch = provider.generate(
            CoverGenerationRequest(
                task_id="task_demo",
                attempt_no=1,
                prompt="生成重点清晰的知识卡片",
                width=1200,
                height=1600,
                variant_numbers=(2, 4),
                input_assets=(
                    {
                        "mime_type": "image/png",
                        "content": base64.b64decode(PNG_1X1),
                    },
                ),
            )
        )
    finally:
        provider.close()

    assert [item.variant_no for item in batch.variants] == [2, 4]
    assert batch.estimated_cost_cny == 0.4
    assert batch.request_id == "req-1,req-2"
    assert len(requests) == 2
    body = json.loads(requests[0].content)
    assert requests[0].url.path == "/api/v3/images/generations"
    assert body["size"] == "1200x1600"
    assert body["response_format"] == "b64_json"
    assert body["sequential_image_generation"] == "disabled"
    assert body["watermark"] is True
    assert body["image"][0].startswith("data:image/png;base64,")


def test_seedream_api_requires_confirmation_and_keeps_errors_sanitized(
    client, headers
):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"x-request-id": f"req-api-{calls}"},
            json={
                "data": [{"b64_json": PNG_1X1}],
                "usage": {"generated_images": 1},
            },
        )

    provider = _provider(handler)
    client.app.state.cover_provider = provider
    client.app.state.worker.cover_provider = provider
    asset = _upload(client, headers)

    denied = client.post(
        "/api/v1/cover-tasks",
        headers={**headers, "Idempotency-Key": "seedream-confirm-denied"},
        json={"prompt": "真实图片服务封面", "media_asset_ids": [asset["id"]]},
    )
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "EXTERNAL_USE_CONFIRMATION_REQUIRED"
    assert calls == 0

    accepted = client.post(
        "/api/v1/cover-tasks",
        headers={**headers, "Idempotency-Key": "seedream-confirm-accepted"},
        json={
            "prompt": "真实图片服务封面",
            "media_asset_ids": [asset["id"]],
            "usage_confirmed": True,
        },
    )
    task = wait_for_terminal(client, accepted.json()["id"], headers)
    assert task["status"] == "success"
    assert task["result_summary"]["estimated_cost_cny"] == 0.8
    assert calls == 4
    covers = client.get("/api/v1/covers", headers=headers).json()["items"]
    assert {item["estimated_cost_cny"] for item in covers} == {0.2}
    content = client.get(covers[0]["content_url"], headers=headers)
    assert content.headers["content-disposition"].endswith('.png"')
    provider.close()

    def unauthorized(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="upstream leaked test-secret-key")

    rejected_provider = _provider(unauthorized)
    try:
        try:
            rejected_provider.generate(
                CoverGenerationRequest(
                    task_id="task_auth",
                    attempt_no=1,
                    prompt="测试鉴权失败",
                    width=1200,
                    height=1600,
                    variant_numbers=(1,),
                    input_assets=(
                        {
                            "mime_type": "image/png",
                            "content": base64.b64decode(PNG_1X1),
                        },
                    ),
                )
            )
        except CoverProviderError as exc:
            assert exc.code == "COVER_PROVIDER_AUTH_FAILED"
            assert "test-secret-key" not in exc.message
        else:
            raise AssertionError("expected sanitized provider error")
    finally:
        rejected_provider.close()
