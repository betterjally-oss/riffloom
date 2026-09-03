from __future__ import annotations

from .base import CollectionRequest, ProviderBatch
from .tikhub import TikHubCollectorProvider
from .xiaohongshu_web import XiaohongshuWebCollectorProvider


class XiaohongshuHybridCollectorProvider:
    """Expose all four collection kinds while preserving the real upstream per batch."""

    provider_id = "xiaohongshu-hybrid-v1"
    is_sandbox = False

    def __init__(
        self,
        single: XiaohongshuWebCollectorProvider,
        metadata: TikHubCollectorProvider,
    ):
        self.single = single
        self.metadata = metadata

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": self.provider_id,
            "mode": "production_third_party",
            "is_sandbox": False,
            "platforms": ["xiaohongshu"],
            "kinds": ["single", "keyword", "creator_content", "creator_profile"],
            "max_items": 30,
            "sample_inputs": {
                **self.metadata.capabilities()["sample_inputs"],
                "single": self.single.capabilities()["sample_inputs"]["single"],
            },
            "actual_upstream": "xiaohongshu-web+tikhub/xiaohongshu-app-v2",
            "requires_local_browser": False,
            "external_calls": True,
            "video_transcript_required": True,
        }

    def collect(self, request: CollectionRequest) -> ProviderBatch:
        return (self.single if request.kind == "single" else self.metadata).collect(request)

    def search_trends(self, keyword: str, *, window_days: int = 7, limit: int = 5) -> ProviderBatch:
        return self.metadata.search_trends(keyword, window_days=window_days, limit=limit)

    def close(self) -> None:
        self.single.client.close()
        self.metadata.close()
