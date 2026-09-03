from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.auth import AuthContext
from app.core.config import Settings
from app.core.database import Database
from app.main import create_app
from app.models import AgentTask, TaskAttempt
from app.providers import SandboxCollectorProvider
from app.schemas.api import CollectRewriteTaskInput
from app.services.collection_service import create_collect_rewrite_task
from app.services.task_service import seed_demo_data
from conftest import wait_for_terminal


def test_startup_recovers_a_task_left_running(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'recover.db'}"
    database = Database(database_url)
    database.create_schema()
    with database.session_factory() as session:
        seed_demo_data(session)
        context = AuthContext(
            user_id="user_demo",
            workspace_id="ws_demo",
            role="editor",
            user_name="示例用户",
            workspace_name="示例内容团队",
        )
        task, _, _ = create_collect_rewrite_task(
            session,
            context,
            CollectRewriteTaskInput(
                url="https://sandbox.riffloom.local/notes/note-001?access=transient",
                prompt="重启恢复",
                usage_confirmed=True,
            ),
            "recovery-flow-001",
            SandboxCollectorProvider(),
        )
        task.status = "running"
        task.stage = "服务中断前执行中"
        attempt = session.scalar(
            select(TaskAttempt).where(TaskAttempt.task_id == task.id)
        )
        assert attempt is not None
        attempt.status = "running"
        session.commit()
        task_id = task.id
    database.dispose()

    settings = Settings(
        database_url=database_url,
        worker_step_delay=0.01,
        worker_threads=1,
        log_level="WARNING",
    )
    headers = {
        "X-Riffloom-User": "user_demo",
        "X-Riffloom-Workspace": "ws_demo",
    }
    with TestClient(create_app(settings)) as client:
        recovered = wait_for_terminal(client, task_id, headers)
        assert recovered["status"] == "failed"
        assert recovered["error"]["code"] == "SOURCE_LINK_REQUIRED_AGAIN"
        assert recovered["attempts"][0]["attempt_no"] == 1
