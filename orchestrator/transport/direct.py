"""Прямой режим: оркестратор вызывает HTTP-сервис расширения 1С (A.0.1).

    POST /hs/bota/v1/tools/{tool_name}
    Authorization: Bearer <base_token>
    X-Bota-Request-Id, X-Bota-Session-Id, X-Bota-Timestamp, X-Bota-Signature

Подпись — HMAC-SHA256 от `method + path + timestamp + body`. Метка времени в
подписи защищает от повтора перехваченного запроса: расширение отвергает
запросы, чей `timestamp` разошёлся с его часами больше чем на допуск.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from collections.abc import Awaitable, Callable

import httpx

from orchestrator.tools.envelope import ToolRequest, ToolResponse
from orchestrator.transport.base import OneCTransport, TransportError, TransportTimeout

API_ROOT = "/hs/bota/v1"


class TenantEndpoint:
    """Адрес и секреты одной базы."""

    def __init__(self, tenant_id: str, base_url: str, token: str, signing_key: str) -> None:
        self.tenant_id = tenant_id
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.signing_key = signing_key


EndpointResolver = Callable[[str], Awaitable["TenantEndpoint | None"]]


def sign(signing_key: str, method: str, path: str, timestamp: str, body: str) -> str:
    """HMAC-SHA256 по схеме A.0.1. Вынесено отдельно, чтобы совпасть с 1С в тестах."""
    payload = f"{method}{path}{timestamp}{body}".encode()
    return hmac.new(signing_key.encode("utf-8"), payload, hashlib.sha256).hexdigest()


class DirectTransport(OneCTransport):
    def __init__(
        self,
        resolve_endpoint: EndpointResolver,
        *,
        timeout_seconds: int = 120,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # Резолвер, а не готовый словарь: адрес и ключ подписи базы лежат в БД и
        # могут поменяться, пока оркестратор работает.
        self._resolve = resolve_endpoint
        self._timeout = timeout_seconds
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def call(self, tenant_id: str, request: ToolRequest) -> ToolResponse:
        endpoint = await self._resolve(tenant_id)
        if endpoint is None:
            raise TransportError(f"База «{tenant_id}» не зарегистрирована в оркестраторе")

        path = f"{API_ROOT}/tools/{request.tool}"
        body = request.model_dump_json(by_alias=True)
        timestamp = str(int(time.time() * 1000))
        request_id = str(uuid.uuid4())

        try:
            response = await self._client.post(
                f"{endpoint.base_url}{path}",
                content=body.encode("utf-8"),
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Authorization": f"Bearer {endpoint.token}",
                    "X-Bota-Request-Id": request_id,
                    "X-Bota-Session-Id": request.context.session_id,
                    "X-Bota-Timestamp": timestamp,
                    "X-Bota-Signature": sign(
                        endpoint.signing_key, "POST", path, timestamp, body
                    ),
                },
            )
        except httpx.TimeoutException as err:
            raise TransportTimeout(request.tool, self._timeout) from err
        except httpx.HTTPError as err:
            raise TransportError(f"Сеть недоступна при вызове «{request.tool}»: {err}") from err

        if response.status_code >= 500:
            raise TransportError(
                f"Расширение вернуло {response.status_code} на «{request.tool}»",
                retryable=True,
            )

        try:
            # Ошибки предметной области приезжают с ok=false и кодом из A.0.2,
            # поэтому 4xx разбираем как обычный конверт, а не как сбой канала.
            return ToolResponse.model_validate(response.json())
        except ValueError as err:
            raise TransportError(
                f"Расширение вернуло непонятный ответ на «{request.tool}»: {err}"
            ) from err

    async def aclose(self) -> None:
        await self._client.aclose()
