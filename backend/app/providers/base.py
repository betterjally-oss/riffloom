from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol


CollectionKind = Literal["single", "keyword", "creator_content", "creator_profile"]


@dataclass(frozen=True)
class CollectionRequest:
    kind: CollectionKind
    platform: str
    query: dict[str, Any]
    limit: int
    attempt_no: int
    retry_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderMedia:
    kind: Literal["cover", "image", "video"]
    source_url: str
    content: bytes
    mime_type: str
    width: int = 0
    height: int = 0
    duration_seconds: float = 0


@dataclass(frozen=True)
class ProviderContent:
    external_id: str
    canonical_url: str
    title: str
    body: str
    author_name: str
    author_external_id: str | None
    content_type: str
    published_at: datetime | None
    cover_url: str | None = None
    media_refs: list[dict[str, str]] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    metrics: dict[str, int] = field(default_factory=dict)
    actual_upstream: str | None = None
    video_transcript: str | None = None
    video_transcript_corrected: str | None = None
    video_transcript_status: str = "not_applicable"
    video_transcript_source: str | None = None
    video_transcript_confidence: float | None = None
    video_transcript_segments: list[dict[str, Any]] = field(default_factory=list)
    downloaded_media: list[ProviderMedia] = field(default_factory=list, repr=False)


@dataclass(frozen=True)
class ProviderBlogger:
    external_id: str
    profile_url: str
    name: str
    avatar_url: str | None
    bio: str
    followers: int
    likes_and_collects: int
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProviderItemError:
    source_key: str
    code: str
    message: str
    retryable: bool


@dataclass(frozen=True)
class ProviderRequestTrace:
    operation: str
    request_id: str
    status: Literal["success", "failed"]
    cost: float
    http_status: int | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class ProviderBatch:
    request_id: str
    provider: str
    request_traces: tuple[ProviderRequestTrace, ...] = ()
    content_items: list[ProviderContent] = field(default_factory=list)
    blogger_items: list[ProviderBlogger] = field(default_factory=list)
    item_errors: list[ProviderItemError] = field(default_factory=list)
    elapsed_ms: int = 0
    rate_limit: dict[str, int] = field(default_factory=dict)
    cost: float = 0.0


class ProviderError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        provider_request_id: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.provider_request_id = provider_request_id


class CollectorProvider(Protocol):
    provider_id: str
    is_sandbox: bool

    def capabilities(self) -> dict[str, Any]: ...

    def collect(self, request: CollectionRequest) -> ProviderBatch: ...
