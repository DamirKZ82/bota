"""Диалог с агентом. Вызывается формой «Агент» в 1С (ТЗ п.7)."""

from __future__ import annotations

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, Field

from orchestrator.api.deps import AgentDep, SettingsDep, StoreDep, TenantDep
from orchestrator.db.repo import journal

router = APIRouter(prefix="/v1/chat", tags=["chat"])
log = structlog.get_logger(__name__)


class AskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    dialog_id: str | None = Field(
        default=None,
        description="ID диалога; не указан — начинается новый",
    )
    user_key: str = Field(
        default="unknown",
        description="Идентификатор пользователя 1С — попадает в журнал",
    )
    allow_write_plans: bool = Field(
        default=True,
        description="Разрешить агенту готовить планы изменений (не применять их)",
    )


class ToolCallView(BaseModel):
    tool: str
    onec_method: str
    ok: bool
    duration_ms: int


class AskResponse(BaseModel):
    dialog_id: str
    text: str
    calls: list[ToolCallView]
    truncated: bool = Field(description="Ответ промежуточный: достигнут лимит вызовов")
    usage: dict[str, int]


@router.post("/ask", response_model=AskResponse)
async def ask(
    request: AskRequest,
    tenant: TenantDep,
    agent: AgentDep,
    store: StoreDep,
    settings: SettingsDep,
) -> AskResponse:
    dialog_id = await store.open(
        tenant_id=tenant.id, user_key=request.user_key, dialog_id=request.dialog_id
    )
    history, masking = await store.load(
        tenant_id=tenant.id,
        dialog_id=dialog_id,
        masking_enabled=tenant.masking_enabled,
    )
    already_saved = len(history)

    result, turns = await agent.run(
        tenant_id=tenant.id,
        user_message=request.message,
        history=history,
        masking=masking,
        allow_write_plans=request.allow_write_plans,
    )

    await store.save(
        tenant_id=tenant.id,
        dialog_id=dialog_id,
        turns=turns,
        already_saved=already_saved,
        masking=masking,
    )

    if settings.storage == "postgres":
        for call in result.calls:
            await journal.record_tool_call(
                tenant_id=tenant.id,
                dialog_id=dialog_id,
                user_key=request.user_key,
                tool_name=call.tool,
                onec_method=call.onec_method,
                arguments=call.arguments,
                ok=call.ok,
                duration_ms=call.duration_ms,
                error_message=call.error_message,
            )

    log.info(
        "dialog_answered",
        tenant_id=tenant.id,
        dialog_id=dialog_id,
        calls=len(result.calls),
        truncated=result.truncated,
    )

    return AskResponse(
        dialog_id=dialog_id,
        text=result.text,
        calls=[
            ToolCallView(
                tool=c.tool,
                onec_method=c.onec_method,
                ok=c.ok,
                duration_ms=c.duration_ms,
            )
            for c in result.calls
        ],
        truncated=result.truncated,
        usage=result.usage,
    )


@router.delete("/{dialog_id}", status_code=204)
async def close_dialog(dialog_id: str, tenant: TenantDep, store: StoreDep) -> None:
    """Закрыть диалог и стереть словарь псевдонимов, не дожидаясь ретеншна."""
    await store.close(tenant_id=tenant.id, dialog_id=dialog_id)
