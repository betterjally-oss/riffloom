from __future__ import annotations

import json
from argparse import Namespace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.main import create_app
from app.models import (
    AuthSession,
    InvitationCode,
    Membership,
    PilotMemberWorkspace,
    User,
    Workspace,
)
from app.services.auth_service import issue_invitation_code
from conftest import wait_for_terminal
from scripts.manage_pilot_auth import _bootstrap


AUTH_SECRET = "test-only-auth-secret-that-is-long-enough"
INVITATION_CODE = "rfi_test_invitation_code_0001"


@pytest.fixture
def invite_client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'invite-auth.db'}",
        asset_storage_dir=str(tmp_path / "uploads"),
        auth_mode="invite_token",
        auth_secret=AUTH_SECRET,
        auth_session_ttl_seconds=3600,
        auth_last_seen_update_seconds=60,
        worker_step_delay=0.01,
        worker_threads=1,
        log_level="WARNING",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        with app.state.database.session_factory() as session:
            session.add(Workspace(id="ws_pilot", name="Pilot 工作区", config={}))
            session.add(User(id="user_pilot", name="Pilot 内容成员"))
            session.add(User(id="user_admin", name="Pilot 管理员"))
            session.flush()
            session.add(
                Membership(
                    id="mem_pilot",
                    workspace_id="ws_pilot",
                    user_id="user_pilot",
                    role="editor",
                )
            )
            session.add(
                Membership(
                    id="mem_admin",
                    workspace_id="ws_pilot",
                    user_id="user_admin",
                    role="admin",
                )
            )
            session.commit()
            issue_invitation_code(
                session,
                auth_service=app.state.auth_service,
                workspace_id="ws_pilot",
                user_id="user_pilot",
                created_by="user_admin",
                expires_at=datetime.now(UTC) + timedelta(days=1),
                max_uses=1,
                raw_code=INVITATION_CODE,
            )
        yield client, app


def _redeem(client: TestClient, code: str = INVITATION_CODE):
    return client.post(
        "/api/v1/auth/invitations/redeem",
        json={"invitation_code": code},
    )


def _admin_headers(client: TestClient, app) -> dict[str, str]:
    code = "rfi_test_admin_invitation_0001"
    with app.state.database.session_factory() as session:
        issue_invitation_code(
            session,
            auth_service=app.state.auth_service,
            workspace_id="ws_pilot",
            user_id="user_admin",
            created_by="user_admin",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            max_uses=1,
            raw_code=code,
        )
    token = _redeem(client, code).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_bootstrap_admin_access_key_can_be_reused(tmp_path, capsys):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'admin-access-key.db'}",
        asset_storage_dir=str(tmp_path / "uploads"),
        auth_mode="invite_token",
        auth_secret=AUTH_SECRET,
        log_level="WARNING",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        with app.state.database.session_factory() as session:
            _bootstrap(
                session,
                Namespace(
                    workspace_id="ws_admin_key",
                    workspace_name="管理员工作区",
                    admin_id="user_admin_key",
                    admin_name="管理员",
                    expires_hours=720,
                ),
                app.state.auth_service,
            )
        access_key = json.loads(capsys.readouterr().out)["invitation_code"]
        assert _redeem(client, access_key).status_code == 200
        assert _redeem(client, access_key).status_code == 200


def test_invitation_redeems_once_and_database_never_stores_plain_secrets(
    invite_client,
):
    client, app = invite_client
    response = _redeem(client)

    assert response.status_code == 200
    payload = response.json()
    assert payload["access_token"].startswith("rfs_")
    assert payload["token_type"] == "bearer"
    assert payload["session"] == {
        "user_id": "user_pilot",
        "user_name": "Pilot 内容成员",
        "workspace_id": "ws_pilot",
        "workspace_name": "Pilot 工作区",
            "role": "editor",
            "auth_mode": "invite_token",
            "onboarding_completed": False,
        }

    repeated = _redeem(client)
    assert repeated.status_code == 401
    assert repeated.json()["error"]["code"] == "AUTH_CREDENTIAL_INVALID"

    with app.state.database.session_factory() as session:
        invitation = session.scalar(select(InvitationCode))
        auth_session = session.scalar(select(AuthSession))
        assert invitation is not None and auth_session is not None
        assert invitation.use_count == 1
        assert invitation.code_hash != INVITATION_CODE
        assert auth_session.token_hash != payload["access_token"]
        assert INVITATION_CODE not in repr((invitation.code_hash, auth_session.token_hash))


def test_bearer_session_ignores_forged_demo_identity_headers(invite_client):
    client, _ = invite_client
    token = _redeem(client).json()["access_token"]

    missing = client.get("/api/v1/session")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "AUTH_REQUIRED"

    authenticated = client.get(
        "/api/v1/session",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Riffloom-User": "user_admin",
            "X-Riffloom-Workspace": "ws_other",
        },
    )
    assert authenticated.status_code == 200
    assert authenticated.json()["user_id"] == "user_pilot"
    assert authenticated.json()["workspace_id"] == "ws_pilot"
    assert authenticated.json()["role"] == "editor"


def test_logout_revokes_session_and_role_is_loaded_live(invite_client):
    client, app = invite_client
    token = _redeem(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    with app.state.database.session_factory() as session:
        membership = session.scalar(
            select(Membership).where(Membership.id == "mem_pilot")
        )
        assert membership is not None
        membership.role = "lead"
        session.commit()
    assert client.get("/api/v1/session", headers=headers).json()["role"] == "lead"

    logout = client.post("/api/v1/auth/logout", headers=headers)
    assert logout.status_code == 200
    assert logout.json() == {"status": "signed_out"}
    rejected = client.get("/api/v1/session", headers=headers)
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "AUTH_SESSION_INVALID"


def test_expired_and_revoked_invitations_share_generic_failure(invite_client):
    client, app = invite_client
    with app.state.database.session_factory() as session:
        invitation = session.scalar(select(InvitationCode))
        assert invitation is not None
        invitation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    expired = _redeem(client)
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "AUTH_CREDENTIAL_INVALID"

    with app.state.database.session_factory() as session:
        invitation = session.scalar(select(InvitationCode))
        assert invitation is not None
        invitation.expires_at = datetime.now(UTC) + timedelta(days=1)
        invitation.status = "revoked"
        invitation.revoked_at = datetime.now(UTC)
        session.commit()
    revoked = _redeem(client)
    assert revoked.status_code == 401
    assert revoked.json()["error"] == expired.json()["error"] | {
        "request_id": revoked.json()["error"]["request_id"],
        "trace_id": revoked.json()["error"]["trace_id"],
    }


def test_invite_auth_configuration_fails_closed_and_demo_mode_does_not_redeem(
    tmp_path, client
):
    with pytest.raises(RuntimeError, match="至少 32 字节"):
        create_app(
            Settings(
                database_url=f"sqlite:///{tmp_path / 'invalid.db'}",
                auth_mode="invite_token",
                auth_secret="too-short",
            )
        )

    response = client.post(
        "/api/v1/auth/invitations/redeem",
        json={"invitation_code": INVITATION_CODE},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AUTH_MODE_DISABLED"


def test_only_workspace_admin_can_issue_invitation(invite_client):
    _, app = invite_client
    with app.state.database.session_factory() as session:
        with pytest.raises(ValueError, match="只有当前 Workspace 管理员"):
            issue_invitation_code(
                session,
                auth_service=app.state.auth_service,
                workspace_id="ws_pilot",
                user_id="user_pilot",
                created_by="user_pilot",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                max_uses=1,
            )

        with pytest.raises(ValueError, match="过期时间必须晚于"):
            issue_invitation_code(
                session,
                auth_service=app.state.auth_service,
                workspace_id="ws_pilot",
                user_id="user_pilot",
                created_by="user_admin",
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
                max_uses=1,
            )


def test_admin_member_management_issues_one_time_codes_and_revokes_disabled_sessions(
    invite_client,
):
    client, app = invite_client
    editor_token = _redeem(client).json()["access_token"]
    forbidden = client.get(
        "/api/v1/workspace/members",
        headers={"Authorization": f"Bearer {editor_token}"},
    )
    assert forbidden.status_code == 403

    admin_headers = _admin_headers(client, app)
    invalid_admin_member = client.post(
        "/api/v1/workspace/members",
        headers=admin_headers,
        json={"user_name": "错误管理员", "role": "admin", "expires_in_hours": 24},
    )
    assert invalid_admin_member.status_code == 422
    created = client.post(
        "/api/v1/workspace/members",
        headers=admin_headers,
        json={"user_name": "第五位测试成员", "role": "editor", "expires_in_hours": 24},
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["member"]["status"] == "active"
    assert payload["member"]["is_isolated"] is True
    assert payload["invitation_code"].startswith("rfi_")
    membership_id = payload["member"]["membership_id"]

    with app.state.database.session_factory() as session:
        invitation = session.get(InvitationCode, payload["invitation_id"])
        assert invitation is not None
        assert invitation.code_hash != payload["invitation_code"]
        assert invitation.workspace_id != "ws_pilot"
        workspace = session.get(Workspace, invitation.workspace_id)
        assert workspace is not None
        managed_workspace = session.scalar(
            select(PilotMemberWorkspace).where(
                PilotMemberWorkspace.workspace_id == invitation.workspace_id
            )
        )
        assert managed_workspace is not None
        assert managed_workspace.parent_workspace_id == "ws_pilot"
        assert managed_workspace.member_user_id == payload["member"]["user_id"]

    member_token = _redeem(client, payload["invitation_code"]).json()["access_token"]
    member_headers = {"Authorization": f"Bearer {member_token}"}
    member_session = client.get("/api/v1/session", headers=member_headers).json()
    assert member_session["workspace_id"] == invitation.workspace_id
    assert client.get(
        "/api/v1/libraries/collections", headers=member_headers
    ).json()["total"] == 0

    updated = client.patch(
        f"/api/v1/workspace/members/{membership_id}",
        headers=admin_headers,
        json={"role": "lead"},
    )
    assert updated.status_code == 200
    assert updated.json()["role"] == "lead"

    disabled = client.patch(
        f"/api/v1/workspace/members/{membership_id}",
        headers=admin_headers,
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert client.get(
        "/api/v1/session",
        headers=member_headers,
    ).status_code == 401

    last_admin = client.patch(
        "/api/v1/workspace/members/mem_admin",
        headers=admin_headers,
        json={"status": "disabled"},
    )
    assert last_admin.status_code == 409
    assert last_admin.json()["error"]["code"] == "LAST_ADMIN_REQUIRED"


def test_admin_can_issue_a_reusable_access_key_for_self(invite_client):
    client, app = invite_client
    admin_headers = _admin_headers(client, app)

    response = client.post(
        "/api/v1/workspace/members/mem_admin/invitation",
        headers=admin_headers,
        json={"expires_in_hours": 720},
    )

    assert response.status_code == 200
    access_key = response.json()["invitation_code"]
    assert _redeem(client, access_key).status_code == 200
    assert _redeem(client, access_key).status_code == 200


def test_any_parent_admin_can_reissue_an_isolated_member_invitation(invite_client):
    client, app = invite_client
    admin_headers = _admin_headers(client, app)
    created = client.post(
        "/api/v1/workspace/members",
        headers=admin_headers,
        json={"user_name": "独立测试成员", "role": "editor", "expires_in_hours": 24},
    ).json()

    second_admin_code = "rfi_test_second_admin_invitation_0001"
    with app.state.database.session_factory() as session:
        session.add(User(id="user_admin_2", name="第二位管理员"))
        session.flush()
        session.add(
            Membership(
                workspace_id="ws_pilot",
                user_id="user_admin_2",
                role="admin",
            )
        )
        session.commit()
        issue_invitation_code(
            session,
            auth_service=app.state.auth_service,
            workspace_id="ws_pilot",
            user_id="user_admin_2",
            created_by="user_admin",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            max_uses=1,
            raw_code=second_admin_code,
        )
    second_admin_token = _redeem(client, second_admin_code).json()["access_token"]
    response = client.post(
        f"/api/v1/workspace/members/{created['member']['membership_id']}/invitation",
        headers={"Authorization": f"Bearer {second_admin_token}"},
        json={"expires_in_hours": 24},
    )
    assert response.status_code == 200
    assert response.json()["member"]["is_isolated"] is True


def test_test_members_cannot_see_parent_or_each_others_library(invite_client):
    client, app = invite_client
    admin_headers = _admin_headers(client, app)
    parent_task = client.post(
        "/api/v1/collection-tasks",
        headers={**admin_headers, "Idempotency-Key": "parent-private-library-check"},
        json={
            "kind": "single",
            "platform": "riffloom-sandbox",
            "query": {"url": "https://sandbox.riffloom.local/notes/note-001"},
            "usage_confirmed": True,
            "refresh": False,
            "limit": 1,
        },
    )
    assert parent_task.status_code == 202
    wait_for_terminal(client, parent_task.json()["id"], admin_headers)

    invitations = [
        client.post(
            "/api/v1/workspace/members",
            headers=admin_headers,
            json={"user_name": name, "role": "editor", "expires_in_hours": 24},
        ).json()
        for name in ("独立成员甲", "独立成员乙")
    ]
    tokens = [_redeem(client, item["invitation_code"]).json() for item in invitations]
    assert len({item["session"]["workspace_id"] for item in tokens}) == 2

    first_headers = {"Authorization": f"Bearer {tokens[0]['access_token']}"}
    second_headers = {"Authorization": f"Bearer {tokens[1]['access_token']}"}
    assert client.get(
        "/api/v1/libraries/collections", headers=first_headers
    ).json()["total"] == 0
    assert client.get(
        "/api/v1/libraries/collections", headers=second_headers
    ).json()["total"] == 0

    first_task = client.post(
        "/api/v1/collection-tasks",
        headers={**first_headers, "Idempotency-Key": "member-private-library-check"},
        json={
            "kind": "single",
            "platform": "riffloom-sandbox",
            "query": {"url": "https://sandbox.riffloom.local/notes/note-002"},
            "usage_confirmed": True,
            "refresh": False,
            "limit": 1,
        },
    )
    assert first_task.status_code == 202
    first_result = wait_for_terminal(client, first_task.json()["id"], first_headers)
    first_record_id = first_result["result_refs"][0]["id"]
    assert client.get(
        "/api/v1/libraries/collections", headers=first_headers
    ).json()["total"] == 1
    assert client.get(
        "/api/v1/libraries/collections", headers=second_headers
    ).json()["total"] == 0
    assert client.get(
        f"/api/v1/tasks/{first_task.json()['id']}", headers=second_headers
    ).status_code == 404
    assert client.get(
        f"/api/v1/collections/{first_record_id}", headers=second_headers
    ).status_code == 404
    assert client.get(
        f"/api/v1/collections/{first_record_id}", headers=admin_headers
    ).status_code == 404
    assert client.get(
        "/api/v1/libraries/collections", headers=admin_headers
    ).json()["total"] == 1
    metrics = client.get("/api/v1/workspace/pilot-metrics", headers=admin_headers).json()
    assert metrics["accepted_tasks"] == 2
    assert metrics["completed_tasks"] == 2


def test_reissuing_legacy_member_invitation_moves_them_to_empty_workspace(
    invite_client,
):
    client, app = invite_client
    legacy_token = _redeem(client).json()["access_token"]
    admin_headers = _admin_headers(client, app)
    unused_code = "rfi_test_legacy_unused_invitation_0001"
    with app.state.database.session_factory() as session:
        issue_invitation_code(
            session,
            auth_service=app.state.auth_service,
            workspace_id="ws_pilot",
            user_id="user_pilot",
            created_by="user_admin",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            max_uses=1,
            raw_code=unused_code,
        )
    parent_task = client.post(
        "/api/v1/collection-tasks",
        headers={**admin_headers, "Idempotency-Key": "legacy-parent-data-preserved"},
        json={
            "kind": "single",
            "platform": "riffloom-sandbox",
            "query": {"url": "https://sandbox.riffloom.local/notes/note-001"},
            "usage_confirmed": True,
            "refresh": False,
            "limit": 1,
        },
    )
    wait_for_terminal(client, parent_task.json()["id"], admin_headers)

    migrated = client.post(
        "/api/v1/workspace/members/mem_pilot/invitation",
        headers=admin_headers,
        json={"expires_in_hours": 24},
    )
    assert migrated.status_code == 200
    assert migrated.json()["member"]["is_isolated"] is True
    assert client.get(
        "/api/v1/session",
        headers={"Authorization": f"Bearer {legacy_token}"},
    ).status_code == 401
    assert _redeem(client, unused_code).status_code == 401

    replacement = _redeem(client, migrated.json()["invitation_code"]).json()
    assert replacement["session"]["workspace_id"] != "ws_pilot"
    replacement_headers = {"Authorization": f"Bearer {replacement['access_token']}"}
    assert client.get(
        "/api/v1/libraries/collections", headers=replacement_headers
    ).json()["total"] == 0
    assert client.get(
        "/api/v1/libraries/collections", headers=admin_headers
    ).json()["total"] == 1
    listed = client.get("/api/v1/workspace/members", headers=admin_headers).json()
    legacy = next(item for item in listed["items"] if item["user_id"] == "user_pilot")
    assert legacy["is_isolated"] is True
