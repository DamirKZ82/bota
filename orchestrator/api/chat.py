"""Диалог с агентом. Вызывается формой «Агент» в 1С (ТЗ п.7)."""

from __future__ import annotations

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, Field

from orchestrator.api.deps import AgentDep, SettingsDep, StoreDep, TenantDep
from orchestrator.db.repo import journal
from orchestrator.tools.envelope import CallContext

router = APIRouter(prefix="/v1/chat", tags=["chat"])
log = structlog.get_logger(__name__)


class AskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    dialog_id: str | None = Field(
        default=None,
        description="ID диалога; не указан — начинается новый",
    )
    user_id: str = Field(
        default="unknown",
        description="UUID пользователя 1С — уходит в контекст вызова и в журнал",
    )
    allow_write_plans: bool = Field(
        default=True,
        description="Разрешить агенту готовить планы изменений (не применять их)",
    )


class ToolCallView(BaseModel):
    tool: str
    ok: bool
    duration_ms: int
    error_code: str | None = None


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
        tenant_id=tenant.id, user_key=request.user_id, dialog_id=request.dialog_id
    )
    history = await store.load(tenant_id=tenant.id, dialog_id=dialog_id)
    already_saved = len(history)

    # session_id = dialog_id: по нему расширение 1С находит таблицу псевдонимов
    # маскирования, поэтому в рамках диалога он обязан быть постоянным (A.0.5).
    context = CallContext(
        user_id=request.user_id,
        session_id=dialog_id,
        locale="ru",
        masking=tenant.masking_enabled,
    )

    result, turns = await agent.run(
        tenant_id=tenant.id,
        user_message=request.message,
        context=context,
        history=history,
        allow_write_plans=request.allow_write_plans,
    )

    await store.save(
        tenant_id=tenant.id,
        dialog_id=dialog_id,
        turns=turns,
        already_saved=already_saved,
    )

    if settings.storage == "postgres":
        for call in result.calls:
            await journal.record_tool_call(
                tenant_id=tenant.id,
                dialog_id=dialog_id,
                user_key=request.user_id,
                tool_name=call.tool,
                arguments=call.arguments,
                ok=call.ok,
                duration_ms=call.duration_ms,
                error_code=call.error_code.value if call.error_code else None,
            )
            for plan_id, action in call.plans:
                await journal.record_plan(
                    plan_id=plan_id,
                    tenant_id=tenant.id,
                    dialog_id=dialog_id,
                    tool_name=call.tool,
                    action=action,
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
                ok=c.ok,
                duration_ms=c.duration_ms,
                error_code=c.error_code.value if c.error_code else None,
            )
            for c in result.calls
        ],
        truncated=result.truncated,
        usage=result.usage,
    )


@router.delete("/{dialog_id}", status_code=204)
async def close_dialog(dialog_id: str, tenant: TenantDep, store: StoreDep) -> None:
    """Закрыть диалог. Рабочая история больше не нужна."""
    await store.close(tenant_id=tenant.id, dialog_id=dialog_id)
