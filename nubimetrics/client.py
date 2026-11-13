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
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

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


# ── Cliente do ETL: retry, intervalo mínimo e cache em disco ────────────────

CACHE_DIR = Path(__file__).parent / "cache"

#: Campos de identidade da conta que nunca vão para arquivo versionado
#: (amostra, cache compartilhado, log). Ver revisão do PR #449.
CAMPOS_SENSIVEIS = frozenset({
    "account_id", "seller_id", "email", "user_name", "due_date", "plan_id",
})


def mascarar(obj: Any) -> Any:
    """Troca por ``"***"`` os campos de identidade da conta, em qualquer nível."""
    if isinstance(obj, dict):
        return {k: ("***" if k in CAMPOS_SENSIVEIS and v is not None else mascarar(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [mascarar(v) for v in obj]
    return obj


class NubiToolError(RuntimeError):
    """A tool respondeu com ``isError`` (erro de negócio, não de rede)."""


class NubiClient:
    """Sessão MCP reutilizável para o ETL.

    - **retry exponencial** em erro de rede/transporte (``tentativas``);
      erro de negócio da tool (``isError``) sobe na hora como `NubiToolError`;
    - **intervalo mínimo** entre chamadas (``intervalo_s``), para não estourar
      cota;
    - **cache em disco** por ``tool + args + dia`` (``cache=True``): rodar de
      novo no mesmo dia não gasta chamada. Arquivos em ``nubimetrics/cache/``
      (fora do git).

    Uso::

        async with NubiClient() as nubi:
            payload = await nubi.call("meli_market_rankings", {"inp": {...}})
    """

    def __init__(self, cache: bool = True, tentativas: int = 3, intervalo_s: float = 0.4) -> None:
        self.cache = cache
        self.tentativas = tentativas
        self.intervalo_s = intervalo_s
        self.chamadas = 0
        self.do_cache = 0
        self._stack: Optional[contextlib.AsyncExitStack] = None
        self._session: Any = None
        self._ultimo = 0.0

    async def __aenter__(self) -> "NubiClient":
        self._stack = contextlib.AsyncExitStack()
        self._session = await self._stack.enter_async_context(open_session())
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._stack is not None:
            await self._stack.aclose()

    @staticmethod
    def _cache_path(tool: str, args: Dict[str, Any]) -> Path:
        import datetime as _dt
        import hashlib

        chave = json.dumps({"t": tool, "a": args}, sort_keys=True, ensure_ascii=False)
        h = hashlib.sha1(chave.encode("utf-8")).hexdigest()[:16]
        return CACHE_DIR / _dt.date.today().isoformat() / f"{tool}__{h}.json.gz"

    async def call(self, tool: str, args: Dict[str, Any]) -> Any:
        """Chama uma tool e devolve o payload (JSON já decodificado)."""
        import asyncio
        import gzip
        import time

        path = self._cache_path(tool, args)
        if self.cache and path.exists():
            self.do_cache += 1
            return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))

        espera = self.intervalo_s - (time.monotonic() - self._ultimo)
        if espera > 0:
            await asyncio.sleep(espera)
        ultimo_erro: Optional[Exception] = None
        for tentativa in range(1, self.tentativas + 1):
            try:
                self._ultimo = time.monotonic()
                res = await self._session.call_tool(tool, args)
                break
            except Exception as exc:  # noqa: BLE001 — transporte: repete
                ultimo_erro = exc
                atraso = 2 ** tentativa
                logger.warning(f"[Nubimetrics] {tool} falhou ({tentativa}/{self.tentativas}): "
                               f"{redact(str(exc))[:200]} — nova tentativa em {atraso}s")
                await asyncio.sleep(atraso)
        else:
            raise RuntimeError(f"{tool}: {redact(str(ultimo_erro))}")
        self.chamadas += 1
        payload = result_payload(res)
        if bool(getattr(res, "isError", False) or getattr(res, "is_error", False)):
            raise NubiToolError(f"{tool}: {redact(json.dumps(payload, ensure_ascii=False))[:400]}")
        logger.debug(f"[Nubimetrics] {tool} {json.dumps(args, ensure_ascii=False)[:160]}")
        if self.cache:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8")))
        return payload
