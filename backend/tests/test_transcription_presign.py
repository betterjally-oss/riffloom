from __future__ import annotations

from app.models import MediaAsset
from app.providers import TranscriptSegment, TranscriptionResult
from conftest import wait_for_terminal


class FakePresigner:
    enabled = True

    def __init__(self):
        self.calls: list[tuple[str, str, int]] = []

    def presigned_put(self, object_key, *, content_type, ttl_seconds):
        self.calls.append((object_key, content_type, ttl_seconds))
        return (
            f"https://tos.example/bucket/{object_key}"
            f"?X-Amz-Signature=fake&X-Amz-Expires={ttl_seconds}"
        )


class DisabledPresigner:
    enabled = False


class FakeTranscriptionProvider:
    provider_id = "fake-doubao-asr-v1"

    def capabilities(self):
        return {
            "provider": self.provider_id,
            "enabled": True,
            "external_calls": True,
            "short_model": "doubao-seed-2-0-lite-260428",
            "long_model": None,
            "short_max_seconds": 300,
            "raw_retention_hours": 24,
        }

    def transcribe(self, request):
        assert request.data_url.startswith("data:audio/wav;base64,")
        return TranscriptionResult(
            text="直传转写结果。",
            segments=[
                TranscriptSegment(
                    start_ms=0, end_ms=1000, text="直传转写结果。", confidence=0.9
                )
            ],
            provider=self.provider_id,
            model="doubao-seed-2-0-lite-260428",
            provider_request_id="presign-request-1",
            elapsed_ms=12,
            confidence=0.9,
        )


def _collect_one(client, headers):
    response = client.post(
        "/api/v1/collection-tasks",
        headers={**headers, "Idempotency-Key": "presign-collect-001"},
        json={
            "kind": "single",
            "platform": "riffloom-sandbox",
            "query": {"url": "https://sandbox.riffloom.local/notes/presign-001"},
            "usage_confirmed": True,
            "limit": 1,
        },
    )
    task = wait_for_terminal(client, response.json()["id"], headers)
    assert task["status"] == "success"
    return next(ref["id"] for ref in task["result_refs"] if ref["type"] == "collection")


def _init_payload(**overrides):
    payload = {
        "filename": "rights-confirmed.wav",
        "mime_type": "audio/wav",
        "byte_size": 1024,
        "duration_seconds": 5.0,
        "language": "zh",
        "rights_confirmed": True,
    }
    payload.update(overrides)
    return payload


def test_upload_init_rejects_when_presigner_disabled(client, headers):
    client.app.state.upload_presigner = DisabledPresigner()
    response = client.post(
        "/api/v1/transcription-uploads", headers=headers, json=_init_payload()
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TOS_PRESIGN_DISABLED"


def test_upload_init_returns_presigned_url_and_persists_asset(client, headers):
    presigner = FakePresigner()
    client.app.state.upload_presigner = presigner
    response = client.post(
        "/api/v1/transcription-uploads", headers=headers, json=_init_payload()
    )
    assert response.status_code == 201
    body = response.json()
    assert body["upload_mode"] == "tos_presign"
    assert body["presigned_put_url"].startswith("https://")
    assert body["asset_id"].startswith("asset_")
    assert body["object_key"]
    assert body["expires_in_seconds"] >= 60

    assert len(presigner.calls) == 1
    object_key, content_type, ttl = presigner.calls[0]
    assert object_key == body["object_key"]
    assert content_type == "audio/wav"

    with client.app.state.database.session_factory() as session:
        asset = session.get(MediaAsset, body["asset_id"])
        assert asset is not None
        assert asset.role == "transcription_source"
        assert asset.byte_size == 1024
        assert asset.content_hash == ""
        assert asset.purged_at is None
        assert asset.expires_at is not None


def test_upload_init_rejects_oversized_media(client, headers):
    client.app.state.upload_presigner = FakePresigner()
    response = client.post(
        "/api/v1/transcription-uploads",
        headers=headers,
        json=_init_payload(byte_size=16 * 1024 * 1024),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TRANSCRIPTION_MEDIA_SIZE_INVALID"


def test_upload_init_rejects_missing_rights(client, headers):
    client.app.state.upload_presigner = FakePresigner()
    response = client.post(
        "/api/v1/transcription-uploads",
        headers=headers,
        json=_init_payload(rights_confirmed=False),
    )
    assert response.status_code == 422


def test_presign_full_flow_transcribes_and_purges(client, headers):
    presigner = FakePresigner()
    provider = FakeTranscriptionProvider()
    client.app.state.upload_presigner = presigner
    client.app.state.transcription_provider = provider
    client.app.state.worker.transcription_provider = provider

    record_id = _collect_one(client, headers)

    media = b"RIFF" + b"\x00" * 8 + b"WAVE" + b"data" * 16
    init = client.post(
        "/api/v1/transcription-uploads",
        headers=headers,
        json=_init_payload(byte_size=len(media)),
    ).json()
    asset_id = init["asset_id"]
    object_key = init["object_key"]

    # Simulate the browser's direct PUT to TOS using the signed object key.
    client.app.state.asset_store.store.put_bytes(object_key, media)

    response = client.post(
        f"/api/v1/collections/{record_id}/transcription-tasks",
        headers={**headers, "Idempotency-Key": "presign-transcribe-001"},
        json={
            "filename": "rights-confirmed.wav",
            "mime_type": "audio/wav",
            "duration_seconds": 5.0,
            "language": "zh",
            "rights_confirmed": True,
            "asset_id": asset_id,
        },
    )
    assert response.status_code == 202
    task = wait_for_terminal(client, response.json()["id"], headers)
    assert task["status"] == "success"
    assert task["result_summary"]["raw_media_purged"] is True

    detail = client.get(f"/api/v1/collections/{record_id}", headers=headers).json()
    assert detail["video_transcript_status"] == "complete"
    assert detail["video_transcript"] == "直传转写结果。"

    try:
        client.app.state.asset_store.store.get_bytes(object_key)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("presign media must be purged after transcription")


def test_asset_id_requires_exactly_one_of_data_url_or_asset(client, headers):
    client.app.state.upload_presigner = FakePresigner()
    client.app.state.transcription_provider = FakeTranscriptionProvider()
    client.app.state.worker.transcription_provider = FakeTranscriptionProvider()
    record_id = _collect_one(client, headers)

    # both missing -> schema rejects
    response = client.post(
        f"/api/v1/collections/{record_id}/transcription-tasks",
        headers={**headers, "Idempotency-Key": "presign-both-missing"},
        json={
            "filename": "x.wav",
            "mime_type": "audio/wav",
            "duration_seconds": 5.0,
            "language": "zh",
            "rights_confirmed": True,
        },
    )
    assert response.status_code == 422
