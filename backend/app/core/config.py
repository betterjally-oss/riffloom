from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent
TRUE_ENV_VALUES = {"1", "true", "yes", "on"}


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in TRUE_ENV_VALUES


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _env_mapping(name: str) -> tuple[tuple[str, str], ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return ()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{name} 必须是 JSON 对象") from error
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) and key.strip() and item.strip()
        for key, item in value.items()
    ):
        raise ValueError(f"{name} 必须是非空字符串到非空字符串的 JSON 对象")
    return tuple((key.strip(), item.strip()) for key, item in value.items())


@dataclass(frozen=True)
class Settings:
    deployment_profile: str = "local"
    database_url: str = f"sqlite:///{PROJECT_DIR / 'data' / 'riffloom.db'}"
    database_schema_mode: str = "create_all"
    database_migrate_on_startup: bool = False
    database_backup_enabled: bool = False
    database_restore_policy: str = "disabled"
    database_backup_interval_seconds: int = 5 * 60
    database_backup_dir: str = str(PROJECT_DIR / "data" / "backups")
    sqlite_single_instance_acknowledged: bool = False
    cors_origins: tuple[str, ...] = ("http://localhost:3100", "http://127.0.0.1:3100")
    auth_mode: str = "demo_headers"
    auth_secret: str | None = None
    auth_session_ttl_seconds: int = 24 * 60 * 60
    auth_last_seen_update_seconds: int = 5 * 60
    worker_step_delay: float = 0.35
    worker_threads: int = 2
    collection_provider: str = "sandbox-v1"
    collection_opencli_command: str = "opencli"
    collection_opencli_timeout_seconds: float = 60.0
    collection_tikhub_timeout_seconds: float = 60.0
    collection_media_max_bytes: int = 50 * 1024 * 1024
    tikhub_api_key: str | None = None
    model_provider: str = "mock-v1"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_text_model: str = "deepseek-v4-flash"
    deepseek_pro_model: str = "deepseek-v4-pro"
    deepseek_chat_reasoning_effort: str = "none"
    deepseek_generation_reasoning_effort: str = "high"
    volcengine_ark_api_key: str | None = None
    volcengine_ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    volcengine_vision_model: str = "doubao-seed-2-0-lite-260215"
    volcengine_vision_fallback_model: str = "doubao-seed-2-0-pro-260215"
    volcengine_embedding_model: str = "doubao-embedding-vision-251215"
    cover_provider: str = "mock-cover-v1"
    volcengine_image_model: str = "doubao-seedream-4-0-250828"
    cover_timeout_seconds: float = 180.0
    cover_cost_cny_per_image: float = 0.2
    asr_provider: str = "disabled"
    volcengine_asr_model: str = "doubao-seed-2-0-lite-260428"
    volcengine_asr_max_seconds: int = 300
    transcription_upload_max_bytes: int = 15 * 1024 * 1024
    tos_endpoint: str | None = None
    tos_bucket: str | None = None
    tos_region: str = "cn-beijing"
    tos_access_key_id: str | None = None
    tos_secret_access_key: str | None = None
    tos_presign_url_ttl_seconds: int = 900
    model_timeout_seconds: float = 90.0
    model_max_output_tokens: int = 5000
    model_max_repairs: int = 1
    rag_top_k_keyword: int = 20
    rag_top_k_vector: int = 20
    rag_top_k_final: int = 8
    rag_max_context_chars: int = 12000
    asset_storage_dir: str = str(PROJECT_DIR / "uploads")
    asset_max_bytes: int = 5 * 1024 * 1024
    object_storage_provider: str = "local"
    object_storage_mount_dir: str | None = None
    object_storage_prefix: str = "riffloom"
    feishu_provider: str = "sandbox-feishu-v1"
    feishu_api_base_url: str = "https://open.feishu.cn/open-apis"
    feishu_oauth_redirect_uri: str | None = None
    feishu_app_id: str | None = None
    feishu_app_secret: str | None = None
    feishu_app_token: str | None = None
    feishu_test_table_id: str | None = None
    feishu_smoke_scope: str = "collection.single"
    feishu_target_tables: tuple[tuple[str, str], ...] = ()
    feishu_real_writes_enabled: bool = False
    feishu_max_records_per_run: int = 1
    feishu_timeout_seconds: float = 20.0
    request_max_body_bytes: int = 24 * 1024 * 1024
    api_docs_enabled: bool = True
    log_level: str = "INFO"
    log_format: str = "json"

    @classmethod
    def from_env(cls) -> "Settings":
        auth_mode = os.getenv("RIFFLOOM_AUTH_MODE", "demo_headers")
        origins = tuple(
            origin.strip()
            for origin in os.getenv(
                "RIFFLOOM_CORS_ORIGINS",
                "http://localhost:3100,http://127.0.0.1:3100",
            ).split(",")
            if origin.strip()
        )
        return cls(
            deployment_profile=os.getenv("RIFFLOOM_DEPLOYMENT_PROFILE", "local"),
            database_url=os.getenv(
                "RIFFLOOM_DATABASE_URL",
                f"sqlite:///{PROJECT_DIR / 'data' / 'riffloom.db'}",
            ),
            database_schema_mode=os.getenv(
                "RIFFLOOM_DATABASE_SCHEMA_MODE", "create_all"
            ),
            database_migrate_on_startup=_env_bool(
                "RIFFLOOM_DATABASE_MIGRATE_ON_STARTUP"
            ),
            database_backup_enabled=_env_bool("RIFFLOOM_DATABASE_BACKUP_ENABLED"),
            database_restore_policy=os.getenv(
                "RIFFLOOM_DATABASE_RESTORE_POLICY", "disabled"
            ),
            database_backup_interval_seconds=_env_int(
                "RIFFLOOM_DATABASE_BACKUP_INTERVAL_SECONDS", 300
            ),
            database_backup_dir=os.getenv(
                "RIFFLOOM_DATABASE_BACKUP_DIR",
                str(PROJECT_DIR / "data" / "backups"),
            ),
            sqlite_single_instance_acknowledged=_env_bool(
                "RIFFLOOM_SQLITE_SINGLE_INSTANCE_ACKNOWLEDGED"
            ),
            cors_origins=origins,
            auth_mode=auth_mode,
            auth_secret=os.getenv("RIFFLOOM_AUTH_SECRET") or None,
            auth_session_ttl_seconds=_env_int(
                "RIFFLOOM_AUTH_SESSION_TTL_SECONDS", 86400
            ),
            auth_last_seen_update_seconds=_env_int(
                "RIFFLOOM_AUTH_LAST_SEEN_UPDATE_SECONDS", 300
            ),
            worker_step_delay=_env_float("RIFFLOOM_WORKER_STEP_DELAY", 0.35),
            worker_threads=_env_int("RIFFLOOM_WORKER_THREADS", 2),
            collection_provider=os.getenv("RIFFLOOM_COLLECTION_PROVIDER", "sandbox-v1"),
            collection_opencli_command=os.getenv(
                "RIFFLOOM_COLLECTION_OPENCLI_COMMAND", "opencli"
            ),
            collection_opencli_timeout_seconds=_env_float(
                "RIFFLOOM_COLLECTION_OPENCLI_TIMEOUT_SECONDS", 60
            ),
            collection_tikhub_timeout_seconds=_env_float(
                "RIFFLOOM_COLLECTION_TIKHUB_TIMEOUT_SECONDS", 60
            ),
            collection_media_max_bytes=_env_int(
                "RIFFLOOM_COLLECTION_MEDIA_MAX_BYTES", 50 * 1024 * 1024
            ),
            tikhub_api_key=os.getenv("TIKHUB_API_KEY") or None,
            model_provider=os.getenv("RIFFLOOM_MODEL_PROVIDER", "mock-v1"),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY") or None,
            deepseek_base_url=os.getenv(
                "RIFFLOOM_DEEPSEEK_BASE_URL", "https://api.deepseek.com"
            ),
            deepseek_text_model=os.getenv(
                "RIFFLOOM_DEEPSEEK_TEXT_MODEL", "deepseek-v4-flash"
            ),
            deepseek_pro_model=os.getenv(
                "RIFFLOOM_DEEPSEEK_PRO_MODEL", "deepseek-v4-pro"
            ),
            deepseek_chat_reasoning_effort=os.getenv(
                "RIFFLOOM_DEEPSEEK_CHAT_REASONING_EFFORT", "none"
            ),
            deepseek_generation_reasoning_effort=os.getenv(
                "RIFFLOOM_DEEPSEEK_GENERATION_REASONING_EFFORT", "high"
            ),
            volcengine_ark_api_key=os.getenv("ARK_API_KEY") or None,
            volcengine_ark_base_url=os.getenv(
                "RIFFLOOM_VOLCENGINE_ARK_BASE_URL",
                "https://ark.cn-beijing.volces.com/api/v3",
            ),
            volcengine_vision_model=os.getenv(
                "RIFFLOOM_VOLCENGINE_VISION_MODEL",
                "doubao-seed-2-0-lite-260215",
            ),
            volcengine_vision_fallback_model=os.getenv(
                "RIFFLOOM_VOLCENGINE_VISION_FALLBACK_MODEL",
                "doubao-seed-2-0-pro-260215",
            ),
            volcengine_embedding_model=os.getenv(
                "RIFFLOOM_VOLCENGINE_EMBEDDING_MODEL",
                "doubao-embedding-vision-251215",
            ),
            cover_provider=os.getenv("RIFFLOOM_COVER_PROVIDER", "mock-cover-v1"),
            volcengine_image_model=os.getenv(
                "RIFFLOOM_VOLCENGINE_IMAGE_MODEL",
                "doubao-seedream-4-0-250828",
            ),
            cover_timeout_seconds=_env_float("RIFFLOOM_COVER_TIMEOUT_SECONDS", 180),
            cover_cost_cny_per_image=_env_float(
                "RIFFLOOM_COVER_COST_CNY_PER_IMAGE", 0.2
            ),
            asr_provider=os.getenv("RIFFLOOM_ASR_PROVIDER", "disabled"),
            volcengine_asr_model=os.getenv(
                "RIFFLOOM_VOLCENGINE_ASR_MODEL", "doubao-seed-2-0-lite-260428"
            ),
            volcengine_asr_max_seconds=_env_int(
                "RIFFLOOM_VOLCENGINE_ASR_MAX_SECONDS", 300
            ),
            transcription_upload_max_bytes=_env_int(
                "RIFFLOOM_TRANSCRIPTION_UPLOAD_MAX_BYTES", 15 * 1024 * 1024
            ),
            tos_endpoint=os.getenv("RIFFLOOM_TOS_ENDPOINT") or None,
            tos_bucket=os.getenv("RIFFLOOM_TOS_BUCKET") or None,
            tos_region=os.getenv("RIFFLOOM_TOS_REGION", "cn-beijing"),
            tos_access_key_id=os.getenv("RIFFLOOM_TOS_ACCESS_KEY_ID") or None,
            tos_secret_access_key=os.getenv("RIFFLOOM_TOS_SECRET_ACCESS_KEY") or None,
            tos_presign_url_ttl_seconds=_env_int(
                "RIFFLOOM_TOS_PRESIGN_URL_TTL_SECONDS", 900
            ),
            model_timeout_seconds=_env_float("RIFFLOOM_MODEL_TIMEOUT_SECONDS", 90),
            model_max_output_tokens=_env_int("RIFFLOOM_MODEL_MAX_OUTPUT_TOKENS", 5000),
            model_max_repairs=_env_int("RIFFLOOM_MODEL_MAX_REPAIRS", 1),
            rag_top_k_keyword=_env_int("RIFFLOOM_RAG_TOP_K_KEYWORD", 20),
            rag_top_k_vector=_env_int("RIFFLOOM_RAG_TOP_K_VECTOR", 20),
            rag_top_k_final=_env_int("RIFFLOOM_RAG_TOP_K_FINAL", 8),
            rag_max_context_chars=_env_int("RIFFLOOM_RAG_MAX_CONTEXT_CHARS", 12000),
            asset_storage_dir=os.getenv(
                "RIFFLOOM_ASSET_STORAGE_DIR", str(PROJECT_DIR / "uploads")
            ),
            asset_max_bytes=_env_int("RIFFLOOM_ASSET_MAX_BYTES", 5 * 1024 * 1024),
            object_storage_provider=os.getenv(
                "RIFFLOOM_OBJECT_STORAGE_PROVIDER", "local"
            ),
            object_storage_mount_dir=os.getenv("RIFFLOOM_OBJECT_STORAGE_MOUNT_DIR")
            or None,
            object_storage_prefix=os.getenv(
                "RIFFLOOM_OBJECT_STORAGE_PREFIX", "riffloom"
            ),
            feishu_provider=os.getenv("RIFFLOOM_FEISHU_PROVIDER", "sandbox-feishu-v1"),
            feishu_api_base_url=os.getenv(
                "RIFFLOOM_FEISHU_API_BASE_URL",
                "https://open.feishu.cn/open-apis",
            ),
            feishu_oauth_redirect_uri=os.getenv("RIFFLOOM_FEISHU_OAUTH_REDIRECT_URI")
            or None,
            feishu_app_id=os.getenv("FEISHU_APP_ID") or None,
            feishu_app_secret=os.getenv("FEISHU_APP_SECRET") or None,
            feishu_app_token=os.getenv("RIFFLOOM_FEISHU_APP_TOKEN") or None,
            feishu_test_table_id=os.getenv("RIFFLOOM_FEISHU_TEST_TABLE_ID") or None,
            feishu_smoke_scope=os.getenv(
                "RIFFLOOM_FEISHU_SMOKE_SCOPE", "collection.single"
            ),
            feishu_target_tables=_env_mapping("RIFFLOOM_FEISHU_TARGET_TABLES"),
            feishu_real_writes_enabled=_env_bool("RIFFLOOM_FEISHU_REAL_WRITES_ENABLED"),
            feishu_max_records_per_run=_env_int(
                "RIFFLOOM_FEISHU_MAX_RECORDS_PER_RUN", 1
            ),
            feishu_timeout_seconds=_env_float("RIFFLOOM_FEISHU_TIMEOUT_SECONDS", 20),
            request_max_body_bytes=_env_int(
                "RIFFLOOM_REQUEST_MAX_BODY_BYTES", 24 * 1024 * 1024
            ),
            api_docs_enabled=_env_bool("RIFFLOOM_API_DOCS_ENABLED", True),
            log_level=os.getenv("RIFFLOOM_LOG_LEVEL", "INFO"),
            log_format=os.getenv("RIFFLOOM_LOG_FORMAT", "json"),
        )
