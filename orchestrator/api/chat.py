"""Диалог с агентом. Вызывается формой «Агент» в 1С (ТЗ п.7).

Два способа спросить:

* `POST /v1/chat/ask` — синхронно, ответ приходит в том же запросе. Годится для
  интеграций и отладки, но держит соединение десятки секунд.
* `POST /v1/chat/ask/background` + `GET /v1/chat/progress/{request_id}` — то, чем
  пользуется форма 1С: запрос уходит в работу, форма раз в секунду спрашивает,
  на каком шаге агент, и забирает ответ, когда он готов (ТЗ п.8, индикация).
"""

from __future__ import annotations

import asyncio
import uuid

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, Field

from orchestrator.api.deps import AgentDep, ProgressDep, SettingsDep, StoreDep, TenantDep
from orchestrator.db.repo import journal
from orchestrator.errors import WarnException
from orchestrator.progress import ProgressStore
from orchestrator.store import DialogStore
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


class AcceptedResponse(BaseModel):
    dialog_id: str
    request_id: str = Field(description="Спрашивать прогресс по этому идентификатору")


class ProgressResponse(BaseModel):
    request_id: str
    dialog_id: str
    status: str = Field(description="running | done | failed")
    step_no: int
    step_label: str = Field(description="Что показать пользователю прямо сейчас")
    tool: str | None
    answer: str | None
    calls: list[str]
    error_message: str | None


async def _run_dialog(
    *,
    agent,  # noqa: ANN001 — AgentLoop, аннотация тянет цикл импортов
    store: DialogStore,
    progress: ProgressStore,
    tenant_id: str,
    tenant_masking: bool,
    storage: str,
    request: AskRequest,
    dialog_id: str,
    request_id: str,
) -> tuple[str, list[str]]:
    history = await store.load(tenant_id=tenant_id, dialog_id=dialog_id)
    already_saved = len(history)

    # session_id = dialog_id: по нему расширение 1С находит таблицу псевдонимов
    # маскирования, поэтому в рамках диалога он обязан быть постоянным (A.0.5).
    context = CallContext(
        user_id=request.user_id,
        session_id=dialog_id,
        locale="ru",
        masking=tenant_masking,
    )

    async def report(step_no: int, label: str, tool: str | None) -> None:
        await progress.step(
            tenant_id=tenant_id,
            request_id=request_id,
            step_no=step_no,
            label=label,
            tool=tool,
        )

    result, turns = await agent.run(
        tenant_id=tenant_id,
        user_message=request.message,
        context=context,
        history=history,
        allow_write_plans=request.allow_write_plans,
        on_step=report,
    )

    await store.save(
        tenant_id=tenant_id,
        dialog_id=dialog_id,
        turns=turns,
        already_saved=already_saved,
    )

    if storage == "postgres":
        for call in result.calls:
            await journal.record_tool_call(
                tenant_id=tenant_id,
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
                    tenant_id=tenant_id,
                    dialog_id=dialog_id,
                    tool_name=call.tool,
                    action=action,
                )

    log.info(
        "dialog_answered",
        tenant_id=tenant_id,
        dialog_id=dialog_id,
        calls=len(result.calls),
        truncated=result.truncated,
    )
    return result.text, [c.tool for c in result.calls]


@router.post("/ask", response_model=AskResponse)
async def ask(
    request: AskRequest,
    tenant: TenantDep,
    agent: AgentDep,
    store: StoreDep,
    progress: ProgressDep,
    settings: SettingsDep,
) -> AskResponse:
    """Синхронный ответ. Держит соединение всё время работы агента."""
    dialog_id = await store.open(
        tenant_id=tenant.id, user_key=request.user_id, dialog_id=request.dialog_id
    )
    request_id = str(uuid.uuid4())
    await progress.start(
        tenant_id=tenant.id,
        request_id=request_id,
        dialog_id=dialog_id,
        user_key=request.user_id,
    )

    history_before = await store.load(tenant_id=tenant.id, dialog_id=dialog_id)
    context = CallContext(
        user_id=request.user_id,
        session_id=dialog_id,
        locale="ru",
        masking=tenant.masking_enabled,
    )

    async def report(step_no: int, label: str, tool: str | None) -> None:
        await progress.step(
            tenant_id=tenant.id,
            request_id=request_id,
            step_no=step_no,
            label=label,
            tool=tool,
        )

    result, turns = await agent.run(
        tenant_id=tenant.id,
        user_message=request.message,
        context=context,
        history=history_before,
        allow_write_plans=request.allow_write_plans,
        on_step=report,
    )

    await store.save(
        tenant_id=tenant.id,
        dialog_id=dialog_id,
        turns=turns,
        already_saved=len(history_before),
    )
    await progress.finish(
        tenant_id=tenant.id,
        request_id=request_id,
        answer=result.text,
        calls=[c.tool for c in result.calls],
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


@router.post("/ask/background", response_model=AcceptedResponse, status_code=202)
async def ask_background(
    request: AskRequest,
    tenant: TenantDep,
    agent: AgentDep,
    store: StoreDep,
    progress: ProgressDep,
    settings: SettingsDep,
) -> AcceptedResponse:
    """Запустить агента и сразу вернуть управление форме 1С.

    Форма показывает шаги, опрашивая `/v1/chat/progress/{request_id}`, и не висит
    на HTTP-запросе, который может идти минуту.
    """
    dialog_id = await store.open(
        tenant_id=tenant.id, user_key=request.user_id, dialog_id=request.dialog_id
    )
    request_id = str(uuid.uuid4())
    await progress.start(
        tenant_id=tenant.id,
        request_id=request_id,
        dialog_id=dialog_id,
        user_key=request.user_id,
    )

    async def worker() -> None:
        try:
            answer, calls = await _run_dialog(
                agent=agent,
                store=store,
                progress=progress,
                tenant_id=tenant.id,
                tenant_masking=tenant.masking_enabled,
                storage=settings.storage,
                request=request,
                dialog_id=dialog_id,
                request_id=request_id,
            )
            await progress.finish(
                tenant_id=tenant.id, request_id=request_id, answer=answer, calls=calls
            )
        except Exception as err:
            # Упасть молча нельзя: форма будет ждать ответа, которого не будет.
            log.exception("dialog_failed", tenant_id=tenant.id, dialog_id=dialog_id)
            await progress.fail(
                tenant_id=tenant.id, request_id=request_id, message=str(err)
            )

    # Ссылку держим, иначе задача может быть собрана сборщиком мусора.
    task = asyncio.create_task(worker())
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)

    return AcceptedResponse(dialog_id=dialog_id, request_id=request_id)


_BACKGROUND: set[asyncio.Task[None]] = set()


@router.get("/progress/{request_id}", response_model=ProgressResponse)
async def get_progress(
    request_id: str, tenant: TenantDep, progress: ProgressDep
) -> ProgressResponse:
    state = await progress.get(tenant_id=tenant.id, request_id=request_id)
    if state is None:
        raise WarnException(404, "Запрос не найден или уже удалён")
    return ProgressResponse(
        request_id=state.request_id,
        dialog_id=state.dialog_id,
        status=state.status,
        step_no=state.step_no,
        step_label=state.step_label,
        tool=state.tool,
        answer=state.answer,
        calls=state.calls,
        error_message=state.error_message,
    )


@router.delete("/{dialog_id}", status_code=204)
async def close_dialog(dialog_id: str, tenant: TenantDep, store: StoreDep) -> None:
    """Закрыть диалог. Рабочая история больше не нужна."""
    await store.close(tenant_id=tenant.id, dialog_id=dialog_id)
