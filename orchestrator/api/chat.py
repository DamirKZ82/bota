"""Диалог с агентом. Вызывается формой «Агент» в 1С (ТЗ п.7)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field

from orchestrator.api.deps import AgentDep, TenantDep
from orchestrator.llm.base import Turn
from orchestrator.masking.masker import MaskingSession

router = APIRouter(prefix="/v1/chat", tags=["chat"])

# Диалоги в памяти процесса. Переезжают в Postgres вместе с журналом —
# многопроцессному оркестратору общая память не подходит.
_DIALOGS: dict[str, list[Turn]] = {}
_MASKING: dict[str, MaskingSession] = {}


class AskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    dialog_id: str | None = Field(
        default=None,
        description="ID диалога; не указан — начинается новый",
    )
    allow_write_plans: bool = Field(
        default=True,
        description="Разрешить агенту готовить планы изменений (не применять их)",
    )


class ToolCallView(BaseModel):
    tool: str
    onec_method: str
    ok: bool


class AskResponse(BaseModel):
    dialog_id: str
    text: str
    calls: list[ToolCallView]
    truncated: bool = Field(description="Ответ промежуточный: достигнут лимит вызовов")
    usage: dict[str, int]


@router.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest, tenant: TenantDep, agent: AgentDep) -> AskResponse:
    dialog_id = request.dialog_id or str(uuid.uuid4())
    history = _DIALOGS.get(dialog_id, [])
    masking = _MASKING.setdefault(
        dialog_id,
        MaskingSession(enabled=tenant.masking_enabled),
    )

    result, turns = await agent.run(
        tenant_id=tenant.id,
        user_message=request.message,
        history=history,
        masking=masking,
        allow_write_plans=request.allow_write_plans,
    )
    _DIALOGS[dialog_id] = turns

    return AskResponse(
        dialog_id=dialog_id,
        text=result.text,
        calls=[
            ToolCallView(tool=c.tool, onec_method=c.onec_method, ok=c.ok)
            for c in result.calls
        ],
        truncated=result.truncated,
        usage=result.usage,
    )


@router.delete("/{dialog_id}", status_code=204)
async def close_dialog(dialog_id: str, tenant: TenantDep) -> None:
    """Закрыть диалог и стереть словарь псевдонимов сессии."""
    _DIALOGS.pop(dialog_id, None)
    _MASKING.pop(dialog_id, None)
