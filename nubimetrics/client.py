"""Conexão com o servidor MCP da Nubimetrics (Streamable HTTP).

Fase 1 (descoberta): só abre a sessão e serializa respostas. Retry, rate-limit
e cache em parquet entram com o ETL (seção 5 do prompt do módulo).

Suporta as duas gerações do SDK `mcp`:
- 1.x: ``streamablehttp_client(url, headers=...)`` devolve 3 streams;
- 2.x: ``streamable_http_client(url, http_client=...)`` devolve 2 streams e os
  headers vão num ``httpx2.AsyncClient`` próprio.

Regra dura: a chave nunca aparece em log, exceção ou arquivo gravado.
"""
from __future__ import annotations

import contextlib
import json
import os
from typing import Any, AsyncIterator, Optional

from loguru import logger

MCP_URL = os.getenv("NUBIMETRICS_MCP_URL", "https://mcp.nubimetrics.com/mcp")
API_KEY_ENV = "NUBIMETRICS_API_KEY"


class MissingApiKeyError(RuntimeError):
    """`NUBIMETRICS_API_KEY` ausente no ambiente/.env/st.secrets."""


def get_api_key() -> str:
    """Lê a chave do ambiente (carregando `.env` se houver).

    Raises:
        MissingApiKeyError: chave ausente ou vazia.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # dotenv é opcional aqui
        pass
    key = (os.getenv(API_KEY_ENV) or "").strip()
    if not key:
        raise MissingApiKeyError(
            f"{API_KEY_ENV} não definida. Gere em app.nubimetrics.com/mcp "
            "('Ativar o MCP') e coloque no .env (nunca no git)."
        )
    return key


def redact(text: str, key: Optional[str] = None) -> str:
    """Remove a chave de qualquer texto que vá para log/arquivo."""
    key = key or os.getenv(API_KEY_ENV) or ""
    return text.replace(key, "***") if key else text


@contextlib.asynccontextmanager
async def open_session(url: str = MCP_URL, timeout_s: float = 120.0) -> AsyncIterator[Any]:
    """Abre e inicializa uma `ClientSession` MCP autenticada.

    Yields:
        `mcp.ClientSession` já com `initialize()` feito.
    """
    from mcp import ClientSession

    headers = {"Authorization": f"Bearer {get_api_key()}"}
    import mcp.client.streamable_http as sh

    async with contextlib.AsyncExitStack() as stack:
        if hasattr(sh, "streamable_http_client"):  # SDK 2.x
            http = sh.create_mcp_http_client(headers=headers)
            await stack.enter_async_context(http)
            streams = await stack.enter_async_context(
                sh.streamable_http_client(url, http_client=http)
            )
        else:  # SDK 1.x
            streams = await stack.enter_async_context(
                sh.streamablehttp_client(url, headers=headers)
            )
        read, write = streams[0], streams[1]
        session = await stack.enter_async_context(
            ClientSession(read, write, read_timeout_seconds=timeout_s)
            if _accepts_float_timeout()
            else ClientSession(read, write)
        )
        init = await session.initialize()
        server = getattr(init, "serverInfo", None) or getattr(init, "server_info", None)
        logger.info(f"[Nubimetrics] MCP conectado: {getattr(server, 'name', '?')} "
                    f"{getattr(server, 'version', '')}")
        yield session


def _accepts_float_timeout() -> bool:
    """SDK 2.x aceita `read_timeout_seconds` float; 1.x espera `timedelta`."""
    import inspect

    from mcp import ClientSession

    ann = inspect.signature(ClientSession.__init__).parameters.get("read_timeout_seconds")
    return ann is not None and "float" in str(ann.annotation)


def to_jsonable(obj: Any) -> Any:
    """Serializa objetos pydantic do SDK (tool, resultado) para JSON puro."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(o) for o in obj]
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    return obj


def result_payload(result: Any) -> Any:
    """Extrai o dado útil de um `CallToolResult`.

    Preferência: `structuredContent`; senão o texto de cada bloco, decodificado
    como JSON quando possível.
    """
    data = to_jsonable(result)
    if isinstance(data, dict) and data.get("structuredContent") is not None:
        return data["structuredContent"]
    parts = []
    for block in (data or {}).get("content", []) if isinstance(data, dict) else []:
        txt = block.get("text")
        if txt is None:
            parts.append(block)
            continue
        try:
            parts.append(json.loads(txt))
        except (json.JSONDecodeError, TypeError):
            parts.append(txt)
    return parts[0] if len(parts) == 1 else parts
