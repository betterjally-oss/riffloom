from __future__ import annotations

import mimetypes
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import AuthContext, bearer_token, get_db, resolve_auth_context
from app.core.errors import AppError
from app.core.permissions import PermissionAction, require_permission
from app.models import User
from app.models.entities import utcnow
from app.schemas.api import (
    AdoptionInput,
    AdoptionRead,
    AttachmentCreateInput,
    AttachmentRead,
    AuthTokenRead,
    BreakdownDetailRead,
    BreakdownTaskInput,
    BatchCollectionMetadataInput,
    BatchCollectionMetadataRead,
    CollectRewriteTaskInput,
    CollectionBatchTaskInput,
    CollectionBatchTaskRead,
    CollectionDetailRead,
    CollectionKind,
    ConversationList,
    ConversationRead,
    ConversationSummaryRead,
    ConversationUpdateInput,
    CoverList,
    CoverProviderStatusRead,
    CoverRead,
    CoverRevisionInput,
    CoverSaveRead,
    CoverTaskInput,
    CreateCollectionTaskInput,
    CreateTaskInput,
    CreationDetailRead,
    CreationTaskInput,
    CreationVersionRead,
    FeishuBindingCreateInput,
    FeishuBindingList,
    FeishuBindingPreflightInput,
    FeishuBindingPreflightRead,
    FeishuBindingRead,
    FeishuBindingUpdateInput,
    FeishuConnectionCreateInput,
    FeishuConnectionList,
    FeishuConnectionRead,
    FeishuProviderStatusRead,
    FeishuSyncCreateInput,
    FeishuSyncRunRead,
    FeishuTargetList,
    HealthRead,
    InvitationRedeemInput,
    KnowledgeSearchInput,
    KnowledgeSearchRead,
    LibraryList,
    LibraryDeleteRead,
    MediaAssetCreateInput,
    MediaAssetList,
    MediaAssetRead,
    ProviderStatusRead,
    ManualCreationVersionInput,
    PilotMetricsRead,
    SessionRead,
    SignOutRead,
    TaskList,
    TaskRead,
    TranscriptionProviderStatusRead,
    TranscriptionTaskInput,
    TranscriptionUploadInitInput,
    TranscriptionUploadInitRead,
    TrendTaskInput,
    SkillList,
    SkillRead,
    WorkspaceMemberList,
    WorkspaceMemberCreate,
    WorkspaceMemberInvitationInput,
    WorkspaceMemberInvitationRead,
    WorkspaceMemberRead,
    WorkspaceMemberUpdate,
)
from app.services.generation_service import add_manual_creation_version
from app.services.attachment_service import create_attachment
from app.services.cover_service import (
    cover_to_read,
    create_cover_revision_task,
    create_cover_task,
    create_media_asset,
    get_cover,
    get_media_asset,
    list_covers,
    list_media_assets,
    save_cover,
)
from app.services.knowledge_service import search_knowledge
from app.services.feishu_service import (
    binding_to_read,
    can_manage_feishu,
    complete_oauth_connection,
    create_binding,
    create_connection,
    create_sync_run,
    disconnect_connection,
    get_sync_run,
    get_binding_target_url,
    get_targets,
    list_bindings,
    list_connections,
    oauth_authorization_url,
    preflight_binding,
    retry_sync_run,
    update_binding,
)
from app.services.membership_service import (
    create_workspace_member,
    issue_workspace_member_invitation,
    list_workspace_members,
    update_workspace_member,
)
from app.services.skill_service import load_skill_registry
from app.services.collection_service import (
    create_collect_rewrite_task,
    create_collection_batch_tasks,
    create_collection_task,
    get_collection_detail,
    list_collection_library,
    update_collection_metadata,
)
from app.services.task_service import (
    add_audit,
    adopt_creation,
    cancel_task,
    create_task,
    delete_agent_conversation,
    delete_library_record,
    get_creation_detail,
    get_breakdown_detail,
    get_agent_conversation,
    get_task,
    get_pilot_metrics,
    list_library_records,
    list_agent_conversations,
    list_tasks,
    retry_task,
    task_to_read,
    update_agent_conversation,
)
from app.services.trend_service import create_trend_task
from app.services.transcription_service import (
    create_transcription_task,
    create_transcription_upload,
)

router = APIRouter(prefix="/api/v1")
DbSession = Annotated[Session, Depends(get_db)]
Context = Annotated[AuthContext, Depends(resolve_auth_context)]


@router.get("/health", response_model=HealthRead, tags=["system"])
def health(request: Request, session: DbSession) -> HealthRead:
    session.execute(text("SELECT 1"))
    provider = request.app.state.generation_provider
    routing_summary = getattr(provider, "routing_summary", None)
    routing = (
        routing_summary()
        if callable(routing_summary)
        else {
            "provider": provider.provider_id,
            "text": {"provider": provider.provider_id, "model": provider.model_id},
        }
    )
    return HealthRead(
        status="ok",
        database="ok",
        worker="ok",
        version="1.1.0-pilot-prep",
        model_provider=provider.provider_id,
        model_routing=routing,
        transcription_provider=request.app.state.transcription_provider.capabilities(),
    )


@router.get("/session", response_model=SessionRead, tags=["session"])
def session_info(session: DbSession, context: Context) -> SessionRead:
    user = session.get(User, context.user_id)
    return SessionRead(
        user_id=context.user_id,
        user_name=context.user_name,
        workspace_id=context.workspace_id,
        workspace_name=context.workspace_name,
        role=context.role,
        auth_mode=context.auth_mode,
        onboarding_completed=bool(user and user.onboarding_completed_at),
    )


@router.post(
    "/session/onboarding/complete", response_model=SessionRead, tags=["session"]
)
def complete_onboarding(session: DbSession, context: Context) -> SessionRead:
    user = session.get(User, context.user_id)
    if user is None:
        raise AppError("SESSION_INVALID", "当前用户不存在或已失效", 401)
    if user.onboarding_completed_at is None:
        user.onboarding_completed_at = utcnow()
        add_audit(
            session,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            action="user.onboarding_completed",
            entity_type="User",
            entity_id=user.id,
        )
        session.commit()
    return session_info(session, context)


@router.post(
    "/auth/invitations/redeem",
    response_model=AuthTokenRead,
    tags=["session"],
)
def redeem_invitation(
    request: Request,
    payload: InvitationRedeemInput,
    session: DbSession,
) -> AuthTokenRead:
    if request.app.state.settings.auth_mode != "invite_token":
        raise AppError(
            "AUTH_MODE_DISABLED",
            "当前环境未启用邀请码登录",
            409,
        )
    redeemed = request.app.state.auth_service.redeem(session, payload.invitation_code)
    identity = redeemed.identity
    user = session.get(User, identity.user_id)
    return AuthTokenRead(
        access_token=redeemed.access_token,
        expires_at=redeemed.expires_at,
        session=SessionRead(
            user_id=identity.user_id,
            user_name=identity.user_name,
            workspace_id=identity.workspace_id,
            workspace_name=identity.workspace_name,
            role=identity.role,
            auth_mode="invite_token",
            onboarding_completed=bool(user and user.onboarding_completed_at),
        ),
    )


@router.post("/auth/logout", response_model=SignOutRead, tags=["session"])
def sign_out(
    request: Request,
    session: DbSession,
    context: Context,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> SignOutRead:
    del context
    if request.app.state.settings.auth_mode != "invite_token":
        raise AppError("AUTH_MODE_DISABLED", "当前环境未启用邀请码登录", 409)
    request.app.state.auth_service.revoke(session, bearer_token(authorization))
    return SignOutRead()


@router.get(
    "/workspace/members",
    response_model=WorkspaceMemberList,
    tags=["workspace"],
)
def get_workspace_members(session: DbSession, context: Context) -> WorkspaceMemberList:
    items = list_workspace_members(session, context)
    return WorkspaceMemberList(items=items, total=len(items))


@router.get(
    "/workspace/pilot-metrics",
    response_model=PilotMetricsRead,
    tags=["workspace"],
)
def get_workspace_pilot_metrics(
    session: DbSession, context: Context
) -> PilotMetricsRead:
    require_permission(context, PermissionAction.PILOT_METRICS_READ)
    return get_pilot_metrics(session, context)


@router.post(
    "/workspace/members",
    response_model=WorkspaceMemberInvitationRead,
    status_code=status.HTTP_201_CREATED,
    tags=["workspace"],
)
def post_workspace_member(
    request: Request,
    payload: WorkspaceMemberCreate,
    session: DbSession,
    context: Context,
) -> WorkspaceMemberInvitationRead:
    if request.app.state.auth_service is None:
        raise AppError("AUTH_MODE_DISABLED", "当前环境未启用邀请码登录", 409)
    return create_workspace_member(
        session,
        context,
        request.app.state.auth_service,
        user_name=payload.user_name,
        role=payload.role,
        expires_in_hours=payload.expires_in_hours,
    )


@router.patch(
    "/workspace/members/{membership_id}",
    response_model=WorkspaceMemberRead,
    tags=["workspace"],
)
def patch_workspace_member(
    membership_id: str,
    payload: WorkspaceMemberUpdate,
    session: DbSession,
    context: Context,
) -> WorkspaceMemberRead:
    return update_workspace_member(
        session, context, membership_id, payload.role, payload.status
    )


@router.post(
    "/workspace/members/{membership_id}/invitation",
    response_model=WorkspaceMemberInvitationRead,
    tags=["workspace"],
)
def post_workspace_member_invitation(
    request: Request,
    membership_id: str,
    payload: WorkspaceMemberInvitationInput,
    session: DbSession,
    context: Context,
) -> WorkspaceMemberInvitationRead:
    if request.app.state.auth_service is None:
        raise AppError("AUTH_MODE_DISABLED", "当前环境未启用邀请码登录", 409)
    return issue_workspace_member_invitation(
        session,
        context,
        request.app.state.auth_service,
        membership_id=membership_id,
        expires_in_hours=payload.expires_in_hours,
    )


@router.post(
    "/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["tasks"],
)
def create_agent_task(
    request: Request,
    payload: CreateTaskInput,
    session: DbSession,
    context: Context,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=128)
    ],
) -> TaskRead:
    if payload.skill_id == "viral_topic_coach":
        direction = str(payload.input.get("prompt") or "").strip()
        if len(direction) < 2:
            raise AppError(
                "TOPIC_INPUT_REQUIRED",
                "选题指导需要行业、账号方向或目标人群",
                422,
            )
        trend_payload = TrendTaskInput(
            direction=direction,
            conversation_id=payload.conversation_id,
        )
        task, created = create_trend_task(
            session, context, trend_payload, idempotency_key
        )
        if created:
            request.app.state.worker.submit(task.id)
        return task_to_read(get_task(session, context.workspace_id, task.id))
    require_permission(context, PermissionAction.TASK_CREATE)
    task, created = create_task(session, context, payload, idempotency_key)
    if created:
        request.app.state.worker.submit(task.id)
    return task_to_read(get_task(session, context.workspace_id, task.id))


@router.post(
    "/collection-tasks",
    response_model=TaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["collections"],
)
def create_collection_agent_task(
    request: Request,
    payload: CreateCollectionTaskInput,
    session: DbSession,
    context: Context,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=128)
    ],
) -> TaskRead:
    require_permission(context, PermissionAction.COLLECTION_CREATE)
    task, created, transient_query = create_collection_task(
        session,
        context,
        payload,
        idempotency_key,
        request.app.state.collection_provider,
    )
    if created:
        request.app.state.worker.submit(task.id, collection_query=transient_query)
    return task_to_read(get_task(session, context.workspace_id, task.id))


@router.post(
    "/collect-rewrite-tasks",
    response_model=TaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["collections"],
)
def create_collect_rewrite_agent_task(
    request: Request,
    payload: CollectRewriteTaskInput,
    session: DbSession,
    context: Context,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=128)
    ],
) -> TaskRead:
    require_permission(context, PermissionAction.TASK_CREATE)
    if payload.url:
        require_permission(context, PermissionAction.COLLECTION_CREATE)
    task, created, transient_query = create_collect_rewrite_task(
        session,
        context,
        payload,
        idempotency_key,
        request.app.state.collection_provider,
    )
    if created:
        request.app.state.worker.submit(task.id, collection_query=transient_query)
    return task_to_read(get_task(session, context.workspace_id, task.id))


@router.patch(
    "/collections/batch-metadata",
    response_model=BatchCollectionMetadataRead,
    tags=["collections"],
)
def patch_collection_batch_metadata(
    payload: BatchCollectionMetadataInput,
    session: DbSession,
    context: Context,
) -> BatchCollectionMetadataRead:
    require_permission(context, PermissionAction.COLLECTION_CREATE)
    return BatchCollectionMetadataRead(
        updated_ids=update_collection_metadata(session, context, payload)
    )


@router.post(
    "/collection-batch-tasks",
    response_model=CollectionBatchTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["collections"],
)
def create_collection_batch_agent_tasks(
    request: Request,
    payload: CollectionBatchTaskInput,
    session: DbSession,
    context: Context,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=128)
    ],
) -> CollectionBatchTaskRead:
    require_permission(context, PermissionAction.TASK_CREATE)
    results, issues = create_collection_batch_tasks(
        session,
        context,
        payload,
        idempotency_key,
        request.app.state.collection_provider,
    )
    for task, created in results:
        if created:
            request.app.state.worker.submit(task.id)
    return CollectionBatchTaskRead(
        tasks=[
            task_to_read(get_task(session, context.workspace_id, task.id))
            for task, _ in results
        ],
        issues=issues,
    )


@router.post(
    "/trend-tasks",
    response_model=TaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["trends"],
)
def create_trend_agent_task(
    request: Request,
    payload: TrendTaskInput,
    session: DbSession,
    context: Context,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=128)
    ],
) -> TaskRead:
    task, created = create_trend_task(session, context, payload, idempotency_key)
    if created:
        request.app.state.worker.submit(task.id)
    return task_to_read(get_task(session, context.workspace_id, task.id))


@router.get(
    "/collection-provider", response_model=ProviderStatusRead, tags=["collections"]
)
def get_collection_provider(request: Request, context: Context) -> ProviderStatusRead:
    return ProviderStatusRead.model_validate(
        request.app.state.collection_provider.capabilities()
    )


@router.get(
    "/transcription-provider",
    response_model=TranscriptionProviderStatusRead,
    tags=["collections"],
)
def get_transcription_provider(
    request: Request, context: Context
) -> TranscriptionProviderStatusRead:
    capabilities = request.app.state.transcription_provider.capabilities()
    capabilities["upload_mode"] = (
        "tos_presign" if request.app.state.upload_presigner.enabled else "base64"
    )
    return TranscriptionProviderStatusRead.model_validate(capabilities)


@router.post(
    "/transcription-uploads",
    response_model=TranscriptionUploadInitRead,
    status_code=status.HTTP_201_CREATED,
    tags=["collections"],
)
def init_transcription_upload(
    request: Request,
    payload: TranscriptionUploadInitInput,
    session: DbSession,
    context: Context,
) -> TranscriptionUploadInitRead:
    require_permission(context, PermissionAction.COLLECTION_CREATE)
    return create_transcription_upload(
        session,
        context,
        payload=payload,
        store=request.app.state.asset_store,
        presigner=request.app.state.upload_presigner,
        max_bytes=request.app.state.settings.transcription_upload_max_bytes,
        ttl_seconds=request.app.state.settings.tos_presign_url_ttl_seconds,
    )


@router.get(
    "/cover-provider",
    response_model=CoverProviderStatusRead,
    tags=["covers"],
)
def get_cover_provider(request: Request, context: Context) -> CoverProviderStatusRead:
    return CoverProviderStatusRead.model_validate(
        request.app.state.cover_provider.capabilities()
    )


@router.post(
    "/attachments",
    response_model=AttachmentRead,
    status_code=status.HTTP_201_CREATED,
    tags=["tasks"],
)
def upload_attachment(
    request: Request,
    payload: AttachmentCreateInput,
    session: DbSession,
    context: Context,
) -> AttachmentRead:
    return create_attachment(
        session,
        context,
        payload,
        request.app.state.asset_store,
        request.app.state.settings.asset_max_bytes,
    )


@router.post(
    "/media-assets",
    response_model=MediaAssetRead,
    status_code=status.HTTP_201_CREATED,
    tags=["covers"],
)
def upload_media_asset(
    request: Request,
    payload: MediaAssetCreateInput,
    session: DbSession,
    context: Context,
) -> MediaAssetRead:
    return create_media_asset(
        session,
        context,
        payload,
        request.app.state.asset_store,
        request.app.state.settings.asset_max_bytes,
    )


@router.get(
    "/media-assets",
    response_model=MediaAssetList,
    tags=["covers"],
)
def get_media_assets(session: DbSession, context: Context) -> MediaAssetList:
    items = list_media_assets(session, context)
    return MediaAssetList(items=items, total=len(items))


@router.get("/media-assets/{asset_id}/content", tags=["covers"])
def get_media_asset_content(
    request: Request,
    asset_id: str,
    session: DbSession,
    context: Context,
) -> Response:
    asset = get_media_asset(session, context, asset_id)
    extension = mimetypes.guess_extension(asset.mime_type) or ""
    try:
        content = request.app.state.asset_store.read(asset.storage_key)
    except FileNotFoundError as exc:
        raise AppError("MEDIA_CONTENT_NOT_FOUND", "素材文件不存在", 404) from exc
    return Response(
        content=content,
        media_type=asset.mime_type,
        headers={
            "Content-Disposition": f'inline; filename="{asset.id}{extension}"',
            "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/cover-tasks",
    response_model=TaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["covers"],
)
def create_cover_agent_task(
    request: Request,
    payload: CoverTaskInput,
    session: DbSession,
    context: Context,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=128)
    ],
) -> TaskRead:
    task, created = create_cover_task(
        session,
        context,
        payload,
        idempotency_key,
        request.app.state.cover_provider,
    )
    if created:
        request.app.state.worker.submit(task.id)
    return task_to_read(get_task(session, context.workspace_id, task.id))


@router.get("/covers", response_model=CoverList, tags=["covers"])
def get_cover_assets(session: DbSession, context: Context) -> CoverList:
    items = list_covers(session, context)
    return CoverList(items=items, total=len(items))


@router.get("/covers/{cover_id}", response_model=CoverRead, tags=["covers"])
def get_cover_asset(cover_id: str, session: DbSession, context: Context) -> CoverRead:
    return cover_to_read(get_cover(session, context, cover_id))


@router.get("/covers/{cover_id}/content", tags=["covers"])
def get_cover_content(
    request: Request,
    cover_id: str,
    session: DbSession,
    context: Context,
) -> Response:
    cover = get_cover(session, context, cover_id)
    asset = get_media_asset(session, context, cover.media_asset_id)
    try:
        content = request.app.state.asset_store.read(asset.storage_key)
    except FileNotFoundError as exc:
        raise AppError("COVER_CONTENT_NOT_FOUND", "封面文件不存在", 404) from exc
    return Response(
        content=content,
        media_type=cover.mime_type,
        headers={
            "Content-Disposition": f'inline; filename="{cover.id}{mimetypes.guess_extension(cover.mime_type) or ""}"',
            "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/covers/{cover_id}/revisions",
    response_model=TaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["covers"],
)
def revise_cover_asset(
    request: Request,
    cover_id: str,
    payload: CoverRevisionInput,
    session: DbSession,
    context: Context,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=128)
    ],
) -> TaskRead:
    task, created = create_cover_revision_task(
        session,
        context,
        cover_id,
        payload.prompt,
        idempotency_key,
        request.app.state.cover_provider,
        payload.usage_confirmed,
    )
    if created:
        request.app.state.worker.submit(task.id)
    return task_to_read(get_task(session, context.workspace_id, task.id))


@router.post(
    "/covers/{cover_id}/save",
    response_model=CoverSaveRead,
    tags=["covers"],
)
def save_cover_asset(
    cover_id: str,
    session: DbSession,
    context: Context,
) -> CoverSaveRead:
    return save_cover(session, context, cover_id)


@router.get(
    "/integrations/feishu/provider",
    response_model=FeishuProviderStatusRead,
    tags=["feishu"],
)
def get_feishu_provider(
    request: Request, session: DbSession, context: Context
) -> FeishuProviderStatusRead:
    require_permission(context, PermissionAction.FEISHU_SYNC_VIEW)
    return FeishuProviderStatusRead.model_validate(
        {
            **request.app.state.feishu_provider.capabilities(),
            "can_configure": can_manage_feishu(session, context),
        }
    )


@router.get(
    "/integrations/feishu/connections",
    response_model=FeishuConnectionList,
    tags=["feishu"],
)
def get_feishu_connections(
    session: DbSession, context: Context
) -> FeishuConnectionList:
    items = list_connections(session, context)
    return FeishuConnectionList(items=items, total=len(items))


@router.get(
    "/integrations/feishu/oauth/authorize",
    include_in_schema=False,
    tags=["feishu"],
)
def authorize_feishu_user(
    request: Request,
    scope_key: Annotated[str, Query(min_length=3, max_length=64)],
    session: DbSession,
    context: Context,
) -> RedirectResponse:
    return RedirectResponse(
        oauth_authorization_url(
            session, context, request.app.state.feishu_provider, scope_key
        ),
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


@router.get(
    "/integrations/feishu/oauth/callback",
    include_in_schema=False,
    tags=["feishu"],
)
def complete_feishu_user_oauth(
    request: Request,
    code: Annotated[str, Query(min_length=8, max_length=4096)],
    state_value: Annotated[str, Query(alias="state", min_length=20, max_length=4096)],
    session: DbSession,
    context: Context,
) -> RedirectResponse:
    return RedirectResponse(
        complete_oauth_connection(
            session,
            context,
            request.app.state.feishu_provider,
            code=code,
            state=state_value,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/integrations/feishu/connections",
    response_model=FeishuConnectionRead,
    status_code=status.HTTP_201_CREATED,
    tags=["feishu"],
)
def connect_feishu_sandbox(
    request: Request,
    payload: FeishuConnectionCreateInput,
    session: DbSession,
    context: Context,
) -> FeishuConnectionRead:
    return create_connection(
        session, context, payload, request.app.state.feishu_provider
    )


@router.delete(
    "/integrations/feishu/connections/{connection_id}",
    response_model=FeishuConnectionRead,
    tags=["feishu"],
)
def disconnect_feishu_sandbox(
    connection_id: str,
    retain_external_copies_confirmed: bool,
    session: DbSession,
    context: Context,
) -> FeishuConnectionRead:
    return disconnect_connection(
        session,
        context,
        connection_id,
        retain_external_copies_confirmed,
    )


@router.get(
    "/integrations/feishu/targets",
    response_model=FeishuTargetList,
    tags=["feishu"],
)
def get_feishu_targets(
    request: Request,
    connection_id: str,
    session: DbSession,
    context: Context,
    base_url: str | None = None,
    scope_key: str | None = None,
) -> FeishuTargetList:
    items = get_targets(
        session,
        context,
        connection_id,
        request.app.state.feishu_provider,
        base_url=base_url,
        scope_key=scope_key,
    )
    return FeishuTargetList(
        items=items,
        total=len(items),
        provider=request.app.state.feishu_provider.provider_id,
        external_calls=request.app.state.feishu_provider.external_calls,
    )


@router.post(
    "/integrations/feishu/bindings/preflight",
    response_model=FeishuBindingPreflightRead,
    tags=["feishu"],
)
def preflight_feishu_binding(
    request: Request,
    payload: FeishuBindingPreflightInput,
    session: DbSession,
    context: Context,
) -> FeishuBindingPreflightRead:
    return preflight_binding(
        session, context, payload, request.app.state.feishu_provider
    )


@router.post(
    "/integrations/feishu/bindings",
    response_model=FeishuBindingRead,
    status_code=status.HTTP_201_CREATED,
    tags=["feishu"],
)
def configure_feishu_binding(
    request: Request,
    payload: FeishuBindingCreateInput,
    session: DbSession,
    context: Context,
) -> FeishuBindingRead:
    binding = create_binding(
        session, context, payload, request.app.state.feishu_provider
    )
    return binding_to_read(session, context, binding, request.app.state.feishu_provider)


@router.get(
    "/integrations/feishu/bindings",
    response_model=FeishuBindingList,
    tags=["feishu"],
)
def get_feishu_bindings(
    request: Request, session: DbSession, context: Context
) -> FeishuBindingList:
    items = list_bindings(session, context, request.app.state.feishu_provider)
    return FeishuBindingList(items=items, total=len(items))


@router.get(
    "/integrations/feishu/bindings/{binding_id}/target",
    include_in_schema=False,
    tags=["feishu"],
)
def open_feishu_binding_target(
    request: Request,
    binding_id: str,
    session: DbSession,
    context: Context,
) -> RedirectResponse:
    return RedirectResponse(
        get_binding_target_url(
            session, context, binding_id, request.app.state.feishu_provider
        ),
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


@router.patch(
    "/integrations/feishu/bindings/{binding_id}",
    response_model=FeishuBindingRead,
    tags=["feishu"],
)
def patch_feishu_binding(
    request: Request,
    binding_id: str,
    payload: FeishuBindingUpdateInput,
    session: DbSession,
    context: Context,
) -> FeishuBindingRead:
    binding = update_binding(
        session, context, binding_id, payload, request.app.state.feishu_provider
    )
    return binding_to_read(session, context, binding, request.app.state.feishu_provider)


@router.post(
    "/integrations/feishu/bindings/{binding_id}/syncs",
    response_model=TaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["feishu"],
)
def create_feishu_sync(
    request: Request,
    binding_id: str,
    payload: FeishuSyncCreateInput,
    session: DbSession,
    context: Context,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=128)
    ],
) -> TaskRead:
    task, _, created = create_sync_run(
        session,
        context,
        binding_id,
        payload.mode,
        idempotency_key,
        request.app.state.feishu_provider,
        source_record_id=payload.source_record_id,
    )
    if created:
        request.app.state.worker.submit(task.id)
    return task_to_read(get_task(session, context.workspace_id, task.id))


@router.get(
    "/integrations/feishu/syncs/{run_id}",
    response_model=FeishuSyncRunRead,
    tags=["feishu"],
)
def get_feishu_sync(
    run_id: str, session: DbSession, context: Context
) -> FeishuSyncRunRead:
    return get_sync_run(session, context, run_id)


@router.post(
    "/integrations/feishu/syncs/{run_id}/retries",
    response_model=TaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["feishu"],
)
def retry_feishu_sync(
    request: Request,
    run_id: str,
    session: DbSession,
    context: Context,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=128)
    ],
) -> TaskRead:
    task, _, created = retry_sync_run(
        session,
        context,
        run_id,
        idempotency_key,
        request.app.state.feishu_provider,
    )
    if created:
        request.app.state.worker.submit(task.id)
    return task_to_read(get_task(session, context.workspace_id, task.id))


@router.get("/tasks", response_model=TaskList, tags=["tasks"])
def get_tasks(
    session: DbSession,
    context: Context,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> TaskList:
    items = list_tasks(session, context.workspace_id, limit)
    return TaskList(items=[task_to_read(task) for task in items], total=len(items))


@router.get("/conversations", response_model=ConversationList, tags=["conversations"])
def get_conversations(
    session: DbSession,
    context: Context,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ConversationList:
    return list_agent_conversations(session, context, limit)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationRead,
    tags=["conversations"],
)
def get_conversation(
    conversation_id: str, session: DbSession, context: Context
) -> ConversationRead:
    return get_agent_conversation(session, context, conversation_id)


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationSummaryRead,
    tags=["conversations"],
)
def update_conversation(
    conversation_id: str,
    payload: ConversationUpdateInput,
    session: DbSession,
    context: Context,
) -> ConversationSummaryRead:
    return update_agent_conversation(session, context, conversation_id, payload)


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["conversations"],
)
def delete_conversation(
    conversation_id: str, session: DbSession, context: Context
) -> Response:
    delete_agent_conversation(session, context, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/tasks/{task_id}", response_model=TaskRead, tags=["tasks"])
def get_agent_task(task_id: str, session: DbSession, context: Context) -> TaskRead:
    return task_to_read(get_task(session, context.workspace_id, task_id))


@router.post("/tasks/{task_id}/cancel", response_model=TaskRead, tags=["tasks"])
def cancel_agent_task(task_id: str, session: DbSession, context: Context) -> TaskRead:
    return task_to_read(cancel_task(session, context, task_id))


@router.post(
    "/tasks/{task_id}/retry",
    response_model=TaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["tasks"],
)
def retry_agent_task(
    request: Request,
    task_id: str,
    session: DbSession,
    context: Context,
    _: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> TaskRead:
    task = retry_task(session, context, task_id)
    request.app.state.worker.submit(task.id)
    return task_to_read(task)


@router.get("/libraries/{library_type}", response_model=LibraryList, tags=["libraries"])
def get_library(
    library_type: str,
    session: DbSession,
    context: Context,
    kind: CollectionKind | None = None,
) -> LibraryList:
    if library_type == "collections" and kind is not None:
        items = list_collection_library(session, context.workspace_id, kind)
    else:
        items = list_library_records(session, context.workspace_id, library_type)
    return LibraryList(items=items, total=len(items))


@router.delete(
    "/libraries/{library_type}/{record_id}",
    response_model=LibraryDeleteRead,
    tags=["libraries"],
)
def delete_library_item(
    library_type: str,
    record_id: str,
    session: DbSession,
    context: Context,
) -> LibraryDeleteRead:
    return delete_library_record(session, context, library_type, record_id)


@router.get(
    "/collections/{record_id}",
    response_model=CollectionDetailRead,
    tags=["collections"],
)
def get_collection_record(
    record_id: str,
    session: DbSession,
    context: Context,
) -> CollectionDetailRead:
    return get_collection_detail(session, context.workspace_id, record_id)


@router.post(
    "/collections/{record_id}/transcription-tasks",
    response_model=TaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["collections"],
)
def create_collection_transcription_task(
    request: Request,
    record_id: str,
    payload: TranscriptionTaskInput,
    session: DbSession,
    context: Context,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=128)
    ],
) -> TaskRead:
    require_permission(context, PermissionAction.COLLECTION_CREATE)
    task, created = create_transcription_task(
        session,
        context,
        collection_id=record_id,
        payload=payload,
        idempotency_key=idempotency_key,
        provider=request.app.state.transcription_provider,
        store=request.app.state.asset_store,
        max_bytes=request.app.state.settings.transcription_upload_max_bytes,
    )
    if created:
        request.app.state.worker.submit(task.id)
    return task_to_read(get_task(session, context.workspace_id, task.id))


@router.post(
    "/creations/{record_id}/adoptions",
    response_model=AdoptionRead,
    tags=["creations"],
)
def confirm_creation(
    record_id: str,
    payload: AdoptionInput,
    session: DbSession,
    context: Context,
    _: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> AdoptionRead:
    require_permission(context, PermissionAction.CREATION_ADOPT)
    return adopt_creation(session, context, record_id, payload.version_id)


@router.get(
    "/creations/{record_id}", response_model=CreationDetailRead, tags=["creations"]
)
def get_creation(
    record_id: str,
    session: DbSession,
    context: Context,
) -> CreationDetailRead:
    return get_creation_detail(session, context.workspace_id, record_id)


@router.post(
    "/breakdown-tasks",
    response_model=TaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["breakdowns"],
)
def create_breakdown_task(
    request: Request,
    payload: BreakdownTaskInput,
    session: DbSession,
    context: Context,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=128)
    ],
) -> TaskRead:
    generic = CreateTaskInput(
        mode="breakdown",
        skill_id="viral_breakdown",
        input={
            "prompt": payload.prompt,
            "preset": payload.preset,
            "knowledge_refs": [item.model_dump() for item in payload.knowledge_refs],
        },
        source_ids=payload.source_ids,
        attachment_ids=payload.attachment_ids,
        conversation_id=payload.conversation_id,
    )
    task, created = create_task(session, context, generic, idempotency_key)
    if created:
        request.app.state.worker.submit(task.id)
    return task_to_read(get_task(session, context.workspace_id, task.id))


@router.get(
    "/breakdowns/{record_id}", response_model=BreakdownDetailRead, tags=["breakdowns"]
)
def get_breakdown(
    record_id: str,
    session: DbSession,
    context: Context,
) -> BreakdownDetailRead:
    return get_breakdown_detail(session, context.workspace_id, record_id)


@router.post(
    "/creation-tasks",
    response_model=TaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["creations"],
)
def create_creation_task(
    request: Request,
    payload: CreationTaskInput,
    session: DbSession,
    context: Context,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=128)
    ],
) -> TaskRead:
    generic = CreateTaskInput(
        mode="creation",
        skill_id=(
            "copy_rewrite" if payload.creation_type == "rewrite" else "original_copy"
        ),
        input={
            "prompt": payload.prompt,
            "creation_type": payload.creation_type,
            "knowledge_refs": [item.model_dump() for item in payload.knowledge_refs],
            "target_platform": payload.target_platform,
            "audience": payload.audience,
            "trend_task_id": payload.trend_task_id,
        },
        source_ids=payload.source_ids,
        attachment_ids=payload.attachment_ids,
        conversation_id=payload.conversation_id,
    )
    task, created = create_task(session, context, generic, idempotency_key)
    if created:
        request.app.state.worker.submit(task.id)
    return task_to_read(get_task(session, context.workspace_id, task.id))


@router.post(
    "/creations/{record_id}/versions",
    response_model=CreationVersionRead,
    tags=["creations"],
)
def create_manual_creation_version(
    record_id: str,
    payload: ManualCreationVersionInput,
    session: DbSession,
    context: Context,
    _: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> CreationVersionRead:
    require_permission(context, PermissionAction.CREATION_EDIT)
    version = add_manual_creation_version(
        session,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        record_id=record_id,
        base_version_id=payload.base_version_id,
        body=payload.body,
        change_note=payload.change_note,
    )
    return CreationVersionRead(
        id=version.id,
        version=version.version,
        body=version.body,
        topics=version.topics,
        change_note=version.change_note,
        knowledge_snapshot=version.knowledge_snapshot,
        generation_config=version.generation_config,
        title_candidates=version.title_candidates,
        summary=version.summary,
        risk_notes=version.risk_notes,
        source_refs=version.source_refs,
        similarity_report=version.similarity_report,
        model_call_id=version.model_call_id,
        created_at=version.created_at,
    )


@router.post(
    "/knowledge/search", response_model=KnowledgeSearchRead, tags=["knowledge"]
)
def preview_knowledge_search(
    payload: KnowledgeSearchInput,
    request: Request,
    session: DbSession,
    context: Context,
) -> KnowledgeSearchRead:
    fragments, snapshot = search_knowledge(
        session,
        workspace_id=context.workspace_id,
        provider=request.app.state.generation_provider,
        query=payload.query,
        selected_libraries=list(payload.selected_libraries),
        selected_refs=[
            {"type": item.library_type.rstrip("s"), "id": item.record_id}
            for item in payload.selected_refs
        ],
        top_k=payload.top_k,
        max_chars=request.app.state.settings.rag_max_context_chars,
    )
    session.commit()
    return KnowledgeSearchRead(
        fragments=[item.model_dump() for item in fragments],
        snapshot=snapshot.model_dump(),
    )


@router.get("/skills", response_model=SkillList, tags=["skills"])
def get_skills(context: Context) -> SkillList:
    items = [
        SkillRead(
            id=skill.id,
            name=skill.name,
            version=skill.version,
            modes=skill.modes,
            default_mode=skill.default_mode,
            output_contract=skill.output_contract,
            tools=skill.tools,
            max_steps=skill.max_steps,
            fallback=skill.fallback,
        )
        for skill in load_skill_registry().values()
        if context.role in skill.roles
    ]
    return SkillList(items=items, total=len(items))
