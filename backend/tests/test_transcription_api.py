from __future__ import annotations

import base64
from datetime import timedelta

from app.models import CollectionRecord, MediaAsset
from app.models.entities import new_id, utcnow
from app.providers import TranscriptSegment, TranscriptionResult
from app.services.transcription_service import purge_expired_transcription_assets
from conftest import wait_for_terminal


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
        assert request.duration_seconds == 12.5
        assert request.mime_type == "audio/mpeg"
        assert request.data_url.startswith("data:audio/mpeg;base64,")
        return TranscriptionResult(
            text="先给结论，再展示过程，最后邀请观众互动。",
            segments=[
                TranscriptSegment(
                    start_ms=0,
                    end_ms=2500,
                    text="先给结论，再展示过程，最后邀请观众互动。",
                    confidence=0.97,
                )
            ],
            provider=self.provider_id,
            model="doubao-seed-2-0-lite-260428",
            provider_request_id="asr-request-001",
            elapsed_ms=25,
            confidence=0.97,
        )


def _collect_one(client, headers):
    response = client.post(
        "/api/v1/collection-tasks",
        headers={**headers, "Idempotency-Key": "transcription-source-001"},
        json={
            "kind": "single",
            "platform": "riffloom-sandbox",
            "query": {"url": "https://sandbox.riffloom.local/notes/note-001"},
            "usage_confirmed": True,
            "limit": 1,
        },
    )
    task = wait_for_terminal(client, response.json()["id"], headers)
    assert task["status"] == "success"
    return next(ref["id"] for ref in task["result_refs"] if ref["type"] == "collection")


def _payload(*, rights_confirmed=True):
    content = b"ID3" + b"test-audio" * 20
    encoded = base64.b64encode(content).decode("ascii")
    return {
        "filename": "rights-confirmed-source.mp3",
        "mime_type": "audio/mpeg",
        "duration_seconds": 12.5,
        "language": "zh",
        "rights_confirmed": rights_confirmed,
        "data_url": f"data:audio/mpeg;base64,{encoded}",
    }


def test_transcription_status_is_explicitly_disabled_by_default(client, headers):
    response = client.get("/api/v1/transcription-provider", headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "provider": "disabled",
        "enabled": False,
        "external_calls": False,
        "short_model": None,
        "long_model": None,
        "short_max_seconds": 0,
        "raw_retention_hours": None,
        "upload_mode": "base64",
    }


def test_rights_confirmed_media_becomes_required_video_copy_and_raw_is_purged(
    client, headers
):
    provider = FakeTranscriptionProvider()
    client.app.state.transcription_provider = provider
    client.app.state.worker.transcription_provider = provider
    record_id = _collect_one(client, headers)

    response = client.post(
        f"/api/v1/collections/{record_id}/transcription-tasks",
        headers={**headers, "Idempotency-Key": "transcription-run-001"},
        json=_payload(),
    )
    assert response.status_code == 202
    task = wait_for_terminal(client, response.json()["id"], headers)
    assert task["status"] == "success"
    assert task["result_summary"]["raw_media_purged"] is True
    assert task["result_summary"]["model"] == "doubao-seed-2-0-lite-260428"

    detail = client.get(f"/api/v1/collections/{record_id}", headers=headers).json()
    assert detail["video_transcript_status"] == "complete"
    assert detail["video_transcript"] == "先给结论，再展示过程，最后邀请观众互动。"
    assert detail["video_transcript_segments"][0]["start_ms"] == 0

    asset_id = task["input"]["asset_id"]
    with client.app.state.database.session_factory() as session:
        asset = session.get(MediaAsset, asset_id)
        assert asset is not None
        assert asset.purged_at is not None
        storage_key = asset.storage_key
    try:
        client.app.state.asset_store.read(storage_key)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("successful transcription must purge raw media")

    breakdown = client.post(
        "/api/v1/breakdown-tasks",
        headers={**headers, "Idempotency-Key": "transcript-breakdown-001"},
        json={"prompt": "拆解视频文案结构", "source_ids": [record_id]},
    )
    breakdown_task = wait_for_terminal(client, breakdown.json()["id"], headers)
    assert breakdown_task["status"] == "success"


def test_transcription_requires_rights_confirmation(client, headers):
    provider = FakeTranscriptionProvider()
    client.app.state.transcription_provider = provider
    client.app.state.worker.transcription_provider = provider
    record_id = _collect_one(client, headers)

    response = client.post(
        f"/api/v1/collections/{record_id}/transcription-tasks",
        headers={**headers, "Idempotency-Key": "transcription-rights-001"},
        json=_payload(rights_confirmed=False),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_video_without_transcript_cannot_enter_breakdown(client, headers):
    record_id = _collect_one(client, headers)
    with client.app.state.database.session_factory() as session:
        record = session.get(CollectionRecord, record_id)
        assert record is not None
        record.content_type = "视频"
        record.video_transcript_status = "required"
        record.status = "needs_transcript"
        session.commit()

    response = client.post(
        "/api/v1/breakdown-tasks",
        headers={**headers, "Idempotency-Key": "missing-transcript-breakdown-001"},
        json={"prompt": "拆解视频文案结构", "source_ids": [record_id]},
    )
    task = wait_for_terminal(client, response.json()["id"], headers)

    assert task["status"] == "failed"
    assert task["error"]["code"] == "VIDEO_TRANSCRIPT_REQUIRED"


def test_expired_transcription_media_is_deleted(client):
    asset_id = new_id("asset")
    storage_key = client.app.state.asset_store.put(
        workspace_id="ws_demo",
        asset_id=asset_id,
        suffix=".mp3",
        content=b"ID3expired",
    )
    with client.app.state.database.session_factory() as session:
        asset = MediaAsset(
            id=asset_id,
            workspace_id="ws_demo",
            storage_key=storage_key,
            original_name="expired.mp3",
            mime_type="audio/mpeg",
            byte_size=10,
            width=0,
            height=0,
            role="transcription_source",
            rights_status="approved",
            content_hash="expired-hash",
            created_by="user_demo",
            expires_at=utcnow() - timedelta(seconds=1),
        )
        session.add(asset)
        session.commit()

        assert purge_expired_transcription_assets(
            session, client.app.state.asset_store
        ) == 1
        assert asset.purged_at is not None

    try:
        client.app.state.asset_store.read(storage_key)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expired transcription media must be deleted")
