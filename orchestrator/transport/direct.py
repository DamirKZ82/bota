"""Прямой режим: оркестратор вызывает HTTP-сервис расширения 1С."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from orchestrator.transport.base import OneCTransport, ToolCallError, ToolTimeout


class TenantEndpoint:
    """Адрес и секреты одной базы. В рабочей версии приезжает из БД тенантов."""

    def __init__(self, tenant_id: str, base_url: str, token: str, signing_key: str) -> None:
        self.tenant_id = tenant_id
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.signing_key = signing_key


EndpointResolver = Callable[[str], Awaitable["TenantEndpoint | None"]]


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

    async def call(
        self, tenant_id: str, onec_method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        endpoint = await self._resolve(tenant_id)
        if endpoint is None:
            raise ToolCallError(f"База «{tenant_id}» не зарегистрирована в оркестраторе")

        body = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
        signature = hmac.new(
            endpoint.signing_key.encode("utf-8"),
            f"{onec_method}\n{body}".encode(),
            hashlib.sha256,
        ).hexdigest()

        try:
            response = await self._client.post(
                f"{endpoint.base_url}/agent/v1/{onec_method}",
                content=body.encode("utf-8"),
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Authorization": f"Bearer {endpoint.token}",
                    "X-Bota-Signature": signature,
                },
            )
        except httpx.TimeoutException as exc:
            raise ToolTimeout(onec_method, self._timeout) from exc
        except httpx.HTTPError as exc:
            raise ToolCallError(f"Сеть недоступна при вызове «{onec_method}»: {exc}") from exc

        if response.status_code >= 400:
            # Текст ошибки 1С может содержать наименования — маскирование
            # применяется выше по стеку, вместе с результатом.
            raise ToolCallError(
                f"1С вернула ошибку {response.status_code} "
                f"на «{onec_method}»: {response.text[:500]}"
            )

        payload = response.json()
        if not isinstance(payload, dict):
            raise ToolCallError(f"«{onec_method}» вернул не объект JSON")
        return payload

    async def aclose(self) -> None:
        await self._client.aclose()
