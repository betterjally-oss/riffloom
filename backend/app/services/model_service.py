from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import AgentTask, ModelCall
from app.providers.models import GenerationProvider, ModelProviderError, ModelResult


T = TypeVar("T", bound=BaseModel)


def call_model(
    session: Session,
    *,
    task: AgentTask,
    attempt_no: int,
    provider: GenerationProvider,
    operation: str,
    contract_version: str,
    payload: dict[str, Any],
    output_schema: type[T],
) -> tuple[T, ModelCall]:
    try:
        result: ModelResult = provider.generate(operation, payload, output_schema)
    except ModelProviderError as exc:
        trace = ModelCall(
            task_id=task.id,
            workspace_id=task.workspace_id,
            attempt_no=attempt_no,
            provider=provider.provider_id,
            model=provider.model_id,
            operation=operation,
            contract_version=contract_version,
            status="failed",
            error_code=exc.code,
        )
        session.add(trace)
        session.commit()
        raise
    trace = ModelCall(
        task_id=task.id,
        workspace_id=task.workspace_id,
        attempt_no=attempt_no,
        provider=result.provider,
        model=result.model,
        operation=operation,
        contract_version=contract_version,
        status="success",
        latency_ms=result.latency_ms,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        estimated_cost_usd=result.estimated_cost_usd,
        repair_count=result.repair_count,
        provider_request_id=result.request_id,
    )
    session.add(trace)
    session.flush()
    return output_schema.model_validate(result.output.model_dump()), trace
