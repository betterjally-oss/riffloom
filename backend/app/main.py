from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.core.config import Settings
from app.core.database import Database
from app.core.errors import AppError
from app.core.deployment_safety import validate_deployment_settings
from app.core.http_safety import RequestBodyLimitMiddleware
from app.core.migrations import upgrade_database
from app.core.observability import (
    bind_log_context,
    configure_application_logging,
    safe_correlation_id,
)
from app.schemas.api import ErrorDetail, ErrorEnvelope
from app.providers import (
    DisabledTranscriptionProvider,
    VolcengineDoubaoASRProvider,
    MockCoverProvider,
    VolcengineSeedreamCoverProvider,
    OpenCLILocalCollectorProvider,
    OpenApiFeishuProvider,
    SandboxCollectorProvider,
    TikHubCollectorProvider,
    XiaohongshuHybridCollectorProvider,
    XiaohongshuWebCollectorProvider,
    SandboxFeishuProvider,
)
from app.providers.models import (
    GenerationProvider,
    MockGenerationProvider,
    OpenAIGenerationProvider,
    RoutedGenerationProvider,
    VolcengineMultimodalEmbeddingProvider,
)
from app.services.task_service import seed_demo_data
from app.services.asset_store import AssetStore
from app.services.auth_service import InviteTokenAuthService
from app.services.credential_store import EnvCredentialStore, FeishuTokenCipher
from app.services.object_store import ByteStore, FilesystemByteStore
from app.services.persistence import SQLiteBackupManager
from app.services.transcription_service import purge_expired_transcription_assets
from app.services.tos_presign import DisabledUploadPresigner, TosUploadPresigner
from app.services.workflow import TaskWorker

persistence_logger = logging.getLogger("riffloom.persistence")
http_logger = logging.getLogger("riffloom.http")
api_logger = logging.getLogger("riffloom.api")


def _storage_prefix(value: str) -> str:
    prefix = value.strip("/")
    if not prefix or any(part in {"", ".", ".."} for part in prefix.split("/")):
        raise RuntimeError("RIFFLOOM_OBJECT_STORAGE_PREFIX 不是安全的对象前缀")
    return prefix


def _build_storage(settings: Settings) -> tuple[AssetStore, ByteStore, str]:
    if settings.object_storage_provider == "local":
        asset_store = AssetStore(FilesystemByteStore(settings.asset_storage_dir))
        backup_store = FilesystemByteStore(settings.database_backup_dir)
        return asset_store, backup_store, ""
    if settings.object_storage_provider != "tos_mount":
        raise RuntimeError("RIFFLOOM_OBJECT_STORAGE_PROVIDER 仅支持 local 或 tos_mount")
    if not settings.object_storage_mount_dir:
        raise RuntimeError("tos_mount 模式必须配置 RIFFLOOM_OBJECT_STORAGE_MOUNT_DIR")
    mounted_store = FilesystemByteStore(
        settings.object_storage_mount_dir,
        create_root=False,
    )
    prefix = _storage_prefix(settings.object_storage_prefix)
    return (
        AssetStore(mounted_store, key_prefix=f"{prefix}/assets"),
        mounted_store,
        f"{prefix}/database",
    )


def _build_generation_provider(settings: Settings) -> GenerationProvider:
    if settings.model_provider == "mock-v1":
        return MockGenerationProvider()
    if settings.model_provider != "routed-v1":
        raise RuntimeError("RIFFLOOM_MODEL_PROVIDER 仅支持 mock-v1 或 routed-v1")

    deepseek = OpenAIGenerationProvider(
        provider_id="deepseek-v1",
        api_key_label="DEEPSEEK_API_KEY",
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_text_model,
        embedding_model="",
        timeout_seconds=settings.model_timeout_seconds,
        reasoning_effort=settings.deepseek_generation_reasoning_effort,
        agent_pro_model=settings.deepseek_pro_model,
        reasoning_by_operation={
            "agent_chat": settings.deepseek_chat_reasoning_effort,
            "topic_guidance": settings.deepseek_chat_reasoning_effort,
            "creation": settings.deepseek_generation_reasoning_effort,
            "breakdown": settings.deepseek_generation_reasoning_effort,
        },
        max_output_tokens=settings.model_max_output_tokens,
        max_repairs=settings.model_max_repairs,
        supports_images=False,
        input_cost_per_million=0.14,
        output_cost_per_million=0.28,
    )
    volcengine_vision = OpenAIGenerationProvider(
        provider_id="volcengine-vision-v1",
        api_key_label="ARK_API_KEY",
        api_key=settings.volcengine_ark_api_key,
        base_url=settings.volcengine_ark_base_url,
        model=settings.volcengine_vision_model,
        embedding_model="",
        timeout_seconds=settings.model_timeout_seconds,
        reasoning_effort="low",
        max_output_tokens=settings.model_max_output_tokens,
        max_repairs=settings.model_max_repairs,
        supports_images=True,
        input_cost_per_million=0.0,
        output_cost_per_million=0.0,
        embedding_cost_per_million=0.0,
    )
    volcengine_vision_fallback = OpenAIGenerationProvider(
        provider_id="volcengine-vision-v1",
        api_key_label="ARK_API_KEY",
        api_key=settings.volcengine_ark_api_key,
        base_url=settings.volcengine_ark_base_url,
        model=settings.volcengine_vision_fallback_model,
        embedding_model="",
        timeout_seconds=settings.model_timeout_seconds,
        reasoning_effort="medium",
        max_output_tokens=settings.model_max_output_tokens,
        max_repairs=settings.model_max_repairs,
        supports_images=True,
        input_cost_per_million=0.0,
        output_cost_per_million=0.0,
        embedding_cost_per_million=0.0,
    )
    volcengine_embedding = VolcengineMultimodalEmbeddingProvider(
        api_key=settings.volcengine_ark_api_key,
        model=settings.volcengine_embedding_model,
        base_url=settings.volcengine_ark_base_url,
        timeout_seconds=settings.model_timeout_seconds,
    )
    return RoutedGenerationProvider(
        text_provider=deepseek,
        vision_provider=volcengine_vision,
        vision_fallback_provider=volcengine_vision_fallback,
        embedding_provider=volcengine_embedding,
    )


def _build_collection_provider(settings: Settings):
    if settings.collection_provider == "sandbox-v1":
        return SandboxCollectorProvider()
    if settings.collection_provider == "opencli-local-v1":
        if settings.deployment_profile != "local":
            raise RuntimeError(
                "opencli-local-v1 只能在用户本机运行，Pilot/云端必须保持 sandbox-v1"
            )
        return OpenCLILocalCollectorProvider(
            command=settings.collection_opencli_command,
            timeout_seconds=settings.collection_opencli_timeout_seconds,
        )
    if settings.collection_provider == "xiaohongshu-web-v1":
        return XiaohongshuWebCollectorProvider(
            timeout_seconds=settings.collection_opencli_timeout_seconds,
            media_max_bytes=settings.collection_media_max_bytes,
        )
    if settings.collection_provider == "tikhub-v1":
        return TikHubCollectorProvider(
            api_key=settings.tikhub_api_key or "",
            timeout_seconds=settings.collection_tikhub_timeout_seconds,
        )
    if settings.collection_provider == "xiaohongshu-hybrid-v1":
        return XiaohongshuHybridCollectorProvider(
            XiaohongshuWebCollectorProvider(
                timeout_seconds=settings.collection_opencli_timeout_seconds,
                media_max_bytes=settings.collection_media_max_bytes,
            ),
            TikHubCollectorProvider(
                api_key=settings.tikhub_api_key or "",
                timeout_seconds=settings.collection_tikhub_timeout_seconds,
            ),
        )
    raise RuntimeError(
        "RIFFLOOM_COLLECTION_PROVIDER 仅支持 sandbox-v1、opencli-local-v1、"
        "xiaohongshu-web-v1、tikhub-v1 或 xiaohongshu-hybrid-v1"
    )


def _build_transcription_provider(settings: Settings):
    if settings.asr_provider == "disabled":
        return DisabledTranscriptionProvider()
    if settings.asr_provider != "volcengine-doubao-asr-v1":
        raise RuntimeError(
            "RIFFLOOM_ASR_PROVIDER 仅支持 disabled 或 volcengine-doubao-asr-v1"
        )
    return VolcengineDoubaoASRProvider(
        api_key=settings.volcengine_ark_api_key,
        base_url=settings.volcengine_ark_base_url,
        model=settings.volcengine_asr_model,
        max_seconds=settings.volcengine_asr_max_seconds,
        timeout_seconds=settings.model_timeout_seconds,
    )


def _build_cover_provider(settings: Settings):
    if settings.cover_provider == "mock-cover-v1":
        return MockCoverProvider()
    if settings.cover_provider != "volcengine-seedream-v1":
        raise RuntimeError(
            "RIFFLOOM_COVER_PROVIDER 仅支持 mock-cover-v1 或 volcengine-seedream-v1"
        )
    return VolcengineSeedreamCoverProvider(
        api_key=settings.volcengine_ark_api_key or "",
        model=settings.volcengine_image_model,
        base_url=settings.volcengine_ark_base_url,
        timeout_seconds=settings.cover_timeout_seconds,
        cost_cny_per_image=settings.cover_cost_cny_per_image,
    )


def _build_upload_presigner(settings: Settings):
    if not (
        settings.tos_endpoint
        and settings.tos_bucket
        and settings.tos_access_key_id
        and settings.tos_secret_access_key
    ):
        return DisabledUploadPresigner()
    return TosUploadPresigner(
        endpoint=settings.tos_endpoint,
        bucket=settings.tos_bucket,
        region=settings.tos_region,
        access_key_id=settings.tos_access_key_id,
        secret_access_key=settings.tos_secret_access_key,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or Settings.from_env()
    validate_deployment_settings(active_settings)
    configure_application_logging(
        level=active_settings.log_level,
        log_format=active_settings.log_format,
    )
    # HTTPX request logs include full URL paths; Feishu app_token must not enter
    # ordinary logs even though it is not the application secret.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if active_settings.database_schema_mode not in {"create_all", "migrations_only"}:
        raise RuntimeError(
            "RIFFLOOM_DATABASE_SCHEMA_MODE 仅支持 create_all 或 migrations_only"
        )
    if (
        active_settings.object_storage_provider == "tos_mount"
        and active_settings.database_backup_enabled
        and not active_settings.sqlite_single_instance_acknowledged
    ):
        raise RuntimeError(
            "TOS 备份模式必须显式确认 RIFFLOOM_SQLITE_SINGLE_INSTANCE_ACKNOWLEDGED=true"
        )
    if (
        active_settings.database_backup_enabled
        and active_settings.database_backup_interval_seconds < 60
    ):
        raise RuntimeError("数据库定期备份间隔不得小于 60 秒")
    asset_store, backup_store, backup_prefix = _build_storage(active_settings)
    backup_manager = SQLiteBackupManager(
        database_url=active_settings.database_url,
        store=backup_store,
        key_prefix=backup_prefix,
        backup_enabled=active_settings.database_backup_enabled,
        restore_policy=active_settings.database_restore_policy,  # type: ignore[arg-type]
    )
    database = Database(active_settings.database_url)
    if active_settings.auth_mode not in {"demo_headers", "invite_token"}:
        raise RuntimeError("RIFFLOOM_AUTH_MODE 仅支持 demo_headers 或 invite_token")
    auth_service = (
        InviteTokenAuthService(
            secret=active_settings.auth_secret,
            session_ttl_seconds=active_settings.auth_session_ttl_seconds,
            last_seen_update_seconds=active_settings.auth_last_seen_update_seconds,
        )
        if active_settings.auth_mode == "invite_token"
        else None
    )
    collection_provider = _build_collection_provider(active_settings)
    generation_provider = _build_generation_provider(active_settings)
    transcription_provider = _build_transcription_provider(active_settings)
    upload_presigner = _build_upload_presigner(active_settings)
    cover_provider = _build_cover_provider(active_settings)
    credential_store = EnvCredentialStore(
        feishu_app_id=active_settings.feishu_app_id,
        feishu_app_secret=active_settings.feishu_app_secret,
    )
    if active_settings.feishu_provider == "sandbox-feishu-v1":
        feishu_provider = SandboxFeishuProvider()
    elif active_settings.feishu_provider == "openapi-v1":
        feishu_provider = OpenApiFeishuProvider(
            credential_store=credential_store,
            token_cipher=(
                FeishuTokenCipher(active_settings.auth_secret)
                if active_settings.feishu_oauth_redirect_uri
                else None
            ),
            oauth_redirect_uri=active_settings.feishu_oauth_redirect_uri,
            oauth_state_secret=active_settings.auth_secret,
            app_token=active_settings.feishu_app_token,
            test_table_id=active_settings.feishu_test_table_id,
            smoke_scope=active_settings.feishu_smoke_scope,
            target_tables=dict(active_settings.feishu_target_tables),
            writes_enabled=active_settings.feishu_real_writes_enabled,
            max_records_per_run=active_settings.feishu_max_records_per_run,
            base_url=active_settings.feishu_api_base_url,
            timeout_seconds=active_settings.feishu_timeout_seconds,
        )
    else:
        raise RuntimeError(
            "RIFFLOOM_FEISHU_PROVIDER 仅支持 sandbox-feishu-v1 或 openapi-v1"
        )
    worker = TaskWorker(
        database,
        provider=collection_provider,
        generation_provider=generation_provider,
        transcription_provider=transcription_provider,
        cover_provider=cover_provider,
        feishu_provider=feishu_provider,
        asset_store=asset_store,
        media_presigner=upload_presigner,
        settings=active_settings,
        step_delay=active_settings.worker_step_delay,
        max_workers=active_settings.worker_threads,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        def purge_transcription_assets() -> int:
            with database.session_factory() as session:
                return purge_expired_transcription_assets(session, asset_store)

        restore_state = backup_manager.restore_before_startup()
        if active_settings.database_migrate_on_startup:
            await asyncio.to_thread(upgrade_database, active_settings.database_url)
            persistence_logger.info("database_migration_completed")
        if active_settings.database_schema_mode == "create_all":
            database.create_schema()
        else:
            database.verify_schema()
        if active_settings.auth_mode == "demo_headers":
            with database.session_factory() as session:
                seed_demo_data(session)
        purged_assets = purge_transcription_assets()
        if purged_assets:
            persistence_logger.info(
                "expired_transcription_assets_purged",
                extra={"count": purged_assets},
            )
        worker.recover()
        backup_task: asyncio.Task[None] | None = None
        purge_task: asyncio.Task[None] | None = None

        async def backup_periodically() -> None:
            while True:
                await asyncio.sleep(active_settings.database_backup_interval_seconds)
                try:
                    result = await asyncio.to_thread(backup_manager.backup_now)
                    if result is not None:
                        persistence_logger.info(
                            "database_backup_completed",
                            extra={
                                "slot": result.slot,
                                "byte_size": result.byte_size,
                                "reason": "periodic",
                            },
                        )
                except Exception:
                    persistence_logger.exception(
                        "database_backup_failed",
                        extra={"reason": "periodic"},
                    )

        async def purge_periodically() -> None:
            while True:
                await asyncio.sleep(60)
                try:
                    count = await asyncio.to_thread(purge_transcription_assets)
                    if count:
                        persistence_logger.info(
                            "expired_transcription_assets_purged",
                            extra={"count": count},
                        )
                except Exception:
                    persistence_logger.exception(
                        "expired_transcription_assets_purge_failed"
                    )

        if active_settings.database_backup_enabled:
            initial = await asyncio.to_thread(backup_manager.backup_now)
            if initial is not None:
                persistence_logger.info(
                    "database_backup_completed",
                    extra={
                        "slot": initial.slot,
                        "byte_size": initial.byte_size,
                        "reason": "startup",
                    },
                )
            backup_task = asyncio.create_task(backup_periodically())
        if transcription_provider.capabilities().get("enabled"):
            purge_task = asyncio.create_task(purge_periodically())
        persistence_logger.info(
            "database_restore_checked",
            extra={"restore_state": restore_state},
        )
        try:
            yield
        finally:
            if backup_task is not None:
                backup_task.cancel()
                with suppress(asyncio.CancelledError):
                    await backup_task
            if purge_task is not None:
                purge_task.cancel()
                with suppress(asyncio.CancelledError):
                    await purge_task
            worker.shutdown()
            if active_settings.database_backup_enabled:
                try:
                    final = await asyncio.to_thread(backup_manager.backup_now)
                    if final is not None:
                        persistence_logger.info(
                            "database_backup_completed",
                            extra={
                                "slot": final.slot,
                                "byte_size": final.byte_size,
                                "reason": "shutdown",
                            },
                        )
                except Exception:
                    persistence_logger.exception(
                        "database_backup_failed",
                        extra={"reason": "shutdown"},
                    )
            feishu_provider.close()
            cover_provider.close()
            close_collection_provider = getattr(collection_provider, "close", None)
            if callable(close_collection_provider):
                close_collection_provider()
            database.dispose()

    app = FastAPI(
        title="Riffloom V1.1 API",
        version="1.1.0-pilot-prep",
        description="Pilot 上线前本地构建；邀请码认证默认关闭，外部写入保持受限。",
        lifespan=lifespan,
        docs_url="/docs" if active_settings.api_docs_enabled else None,
        redoc_url="/redoc" if active_settings.api_docs_enabled else None,
        openapi_url="/openapi.json" if active_settings.api_docs_enabled else None,
    )
    app.state.settings = active_settings
    app.state.database = database
    app.state.auth_service = auth_service
    app.state.worker = worker
    app.state.collection_provider = collection_provider
    app.state.transcription_provider = transcription_provider
    app.state.upload_presigner = upload_presigner
    app.state.generation_provider = generation_provider
    app.state.cover_provider = cover_provider
    app.state.feishu_provider = feishu_provider
    app.state.credential_store = credential_store
    app.state.asset_store = asset_store
    app.state.backup_manager = backup_manager

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(active_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "Idempotency-Key",
            "X-Riffloom-User",
            "X-Riffloom-Workspace",
            "X-Request-ID",
            "X-Trace-ID",
        ],
        expose_headers=["X-Request-ID", "X-Trace-ID"],
    )
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=active_settings.request_max_body_bytes,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = safe_correlation_id(
            request.headers.get("X-Request-ID"), kind="request"
        )
        trace_id = safe_correlation_id(request.headers.get("X-Trace-ID"), kind="trace")
        request.state.request_id = request_id
        request.state.trace_id = trace_id
        started = time.perf_counter()
        with bind_log_context(request_id=request_id, trace_id=trace_id):
            try:
                response = await call_next(request)
            except Exception as exc:
                http_logger.error(
                    "http_request_failed",
                    extra={
                        "method": request.method,
                        "route": "unmatched",
                        "status_code": 500,
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                        "error_type": type(exc).__name__,
                    },
                    exc_info=True,
                )
                raise
            route = getattr(request.scope.get("route"), "path", "unmatched")
            http_logger.info(
                "http_request_completed",
                extra={
                    "method": request.method,
                    "route": route,
                    "status_code": response.status_code,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Trace-ID"] = trace_id
            return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        api_logger.warning(
            "api_error",
            extra={
                "status_code": exc.status_code,
                "error_code": exc.code,
                "retryable": exc.retryable,
            },
        )
        body = ErrorEnvelope(
            error=ErrorDetail(
                code=exc.code,
                message=exc.message,
                retryable=exc.retryable,
                request_id=getattr(request.state, "request_id", None),
                trace_id=getattr(request.state, "trace_id", None),
            )
        )
        return JSONResponse(
            status_code=exc.status_code, content=body.model_dump(mode="json")
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        fields = []
        for item in exc.errors():
            clean = {
                key: value
                for key, value in item.items()
                if key in {"type", "loc", "msg"}
            }
            if item.get("ctx"):
                clean["context"] = {
                    key: str(value) for key, value in item["ctx"].items()
                }
            fields.append(clean)
        api_logger.warning(
            "api_validation_error",
            extra={
                "status_code": 422,
                "error_code": "VALIDATION_ERROR",
                "field_count": len(fields),
            },
        )
        body = ErrorEnvelope(
            error=ErrorDetail(
                code="VALIDATION_ERROR",
                message="请求参数不符合接口约束",
                retryable=False,
                request_id=getattr(request.state, "request_id", None),
                trace_id=getattr(request.state, "trace_id", None),
                details={"fields": fields},
            )
        )
        return JSONResponse(status_code=422, content=body.model_dump(mode="json"))

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        api_logger.error(
            "api_unexpected_error",
            extra={
                "status_code": 500,
                "error_code": "INTERNAL_ERROR",
                "error_type": type(exc).__name__,
            },
            exc_info=True,
        )
        body = ErrorEnvelope(
            error=ErrorDetail(
                code="INTERNAL_ERROR",
                message="服务暂时无法完成请求",
                retryable=True,
                request_id=getattr(request.state, "request_id", None),
                trace_id=getattr(request.state, "trace_id", None),
            )
        )
        return JSONResponse(status_code=500, content=body.model_dump(mode="json"))

    app.include_router(router)
    return app


app = create_app()
