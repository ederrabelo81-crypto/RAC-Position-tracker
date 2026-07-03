"""
Transporte HTTP do cliente PriceTrack.

Responsabilidades:
  * montar headers de autenticação (a key nunca é logada nem anexada a erros);
  * mapear códigos de status para a taxonomia de exceções (400/401/409/429/5xx);
  * retry com backoff exponencial + jitter para falhas transitórias
    (rede, timeout, 5xx) — nunca para erros determinísticos (400/401/409);
  * download em streaming de URLs pré-assinadas (sem headers de auth).

``session`` e ``sleep_fn`` são injetáveis para testes determinísticos.
"""
from __future__ import annotations

import os
import random
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import requests
from loguru import logger

from .config import PriceTrackSettings
from .exceptions import (
    DownloadUrlExpiredError,
    PriceTrackAuthError,
    PriceTrackBadRequestError,
    PriceTrackExportLimitError,
    PriceTrackHTTPError,
    PriceTrackNetworkError,
    PriceTrackNoCollectionError,
    PriceTrackServerError,
)

_RETRYABLE_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


def _body_excerpt(resp: requests.Response, limit: int = 300) -> str:
    try:
        return (resp.text or "")[:limit]
    except Exception:
        return ""


def raise_for_api_status(resp: requests.Response, context: str) -> None:
    """Converte status HTTP de erro na exceção tipada correspondente."""
    code = resp.status_code
    if code < 400:
        return
    excerpt = _body_excerpt(resp)
    if code == 400:
        raise PriceTrackBadRequestError(
            f"400 em {context} — filtros/parâmetros inválidos: {excerpt}",
            code, excerpt,
        )
    if code == 401:
        raise PriceTrackAuthError(
            f"401 em {context} — API key ausente/inválida. Verifique "
            f"PRICETRACK_API_KEY no ambiente.",
            code, excerpt,
        )
    if code == 409:
        raise PriceTrackNoCollectionError(
            f"409 em {context} — nenhuma tabela de coleta para a data pedida.",
            code, excerpt,
        )
    if code == 429:
        retry_after = None
        header = resp.headers.get("Retry-After")
        if header:
            try:
                retry_after = float(header)
            except ValueError:
                retry_after = None
        raise PriceTrackExportLimitError(
            f"429 em {context} — limite de 3 exports concorrentes atingido.",
            code, excerpt, retry_after=retry_after,
        )
    if code >= 500:
        raise PriceTrackServerError(
            f"{code} em {context} — erro do servidor: {excerpt}", code, excerpt,
        )
    raise PriceTrackHTTPError(f"{code} em {context}: {excerpt}", code, excerpt)


class HttpTransport:
    """Camada fina sobre ``requests`` com retry/backoff e erros tipados."""

    def __init__(
        self,
        settings: PriceTrackSettings,
        session: Optional[requests.Session] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
    ):
        self._settings = settings
        self._session = session or requests.Session()
        self._sleep = sleep_fn
        self._rng = rng

    # ── internals ────────────────────────────────────────────────────────

    def _auth_headers(self) -> Dict[str, str]:
        return {
            self._settings.auth_header: self._settings.api_key,
            "Content-Type": "application/json",
        }

    def _backoff_delay(self, attempt: int) -> float:
        """Backoff exponencial com jitter: base·2^attempt, teto configurável."""
        s = self._settings
        exp = s.backoff_base_seconds * (2 ** attempt)
        capped = min(s.backoff_max_seconds, exp)
        return capped * (0.5 + self._rng() * 0.5)

    # ── API JSON ─────────────────────────────────────────────────────────

    def request_json(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Chama a API e devolve o JSON, com retry para falhas transitórias.

        Retryable: exceções de rede/timeout e 5xx. Erros determinísticos
        (400/401/409/429) sobem imediatamente como exceção tipada.
        """
        s = self._settings
        url = f"{s.base_url}{path}"
        context = f"{method} {path}"
        last_error: Exception | None = None

        for attempt in range(s.max_retries + 1):
            if attempt > 0:
                delay = self._backoff_delay(attempt - 1)
                logger.warning(
                    f"PriceTrack {context}: retry {attempt}/{s.max_retries} "
                    f"em {delay:.1f}s ({last_error})"
                )
                self._sleep(delay)
            try:
                resp = self._session.request(
                    method=method,
                    url=url,
                    headers=self._auth_headers(),
                    params=params,
                    json=json_body,
                    timeout=s.timeout_seconds,
                )
            except _RETRYABLE_EXCEPTIONS as e:
                last_error = PriceTrackNetworkError(f"Falha de rede em {context}: {e}")
                continue

            try:
                raise_for_api_status(resp, context)
            except PriceTrackServerError as e:
                last_error = e
                continue

            try:
                return resp.json()
            except ValueError as e:
                last_error = PriceTrackServerError(
                    f"Resposta não-JSON em {context}: {e}",
                    resp.status_code, _body_excerpt(resp),
                )
                continue

        assert last_error is not None
        raise last_error

    # ── Download de arquivo pré-assinado ─────────────────────────────────

    def stream_download(self, url: str, dest: Path) -> int:
        """Baixa a URL pré-assinada em streaming para ``dest`` (atômico).

        Sem headers de autenticação — a URL já embute a assinatura. 403/404
        na URL pré-assinada indicam expiração (TTL 1h) e viram
        ``DownloadUrlExpiredError`` para o chamador renovar via status.

        Returns:
            Tamanho do arquivo em bytes.
        """
        s = self._settings
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + f".tmp-{os.getpid()}")
        last_error: Exception | None = None

        for attempt in range(s.max_retries + 1):
            if attempt > 0:
                delay = self._backoff_delay(attempt - 1)
                logger.warning(
                    f"PriceTrack download: retry {attempt}/{s.max_retries} "
                    f"em {delay:.1f}s ({last_error})"
                )
                self._sleep(delay)
            try:
                resp = self._session.request(
                    method="GET", url=url, stream=True,
                    timeout=s.download_timeout_seconds,
                )
            except _RETRYABLE_EXCEPTIONS as e:
                last_error = PriceTrackNetworkError(f"Falha de rede no download: {e}")
                continue

            # stream=True mantém o socket aberto até close() explícito —
            # fecha em TODOS os caminhos (retry/erro) para não vazar conexões.
            try:
                if resp.status_code in (403, 404):
                    raise DownloadUrlExpiredError(
                        f"downloadUrl retornou {resp.status_code} — URL "
                        f"pré-assinada expirada (TTL 1h). Renove via "
                        f"GET /exports-external/{{id}}."
                    )
                try:
                    raise_for_api_status(resp, "download")
                except PriceTrackServerError as e:
                    last_error = e
                    continue

                try:
                    with open(tmp, "wb") as fh:
                        for chunk in resp.iter_content(chunk_size=1 << 16):
                            if chunk:
                                fh.write(chunk)
                    os.replace(tmp, dest)
                    return dest.stat().st_size
                except _RETRYABLE_EXCEPTIONS as e:
                    last_error = PriceTrackNetworkError(f"Stream interrompido: {e}")
                    continue
                finally:
                    if tmp.exists():
                        tmp.unlink(missing_ok=True)
            finally:
                close = getattr(resp, "close", None)
                if callable(close):
                    close()

        assert last_error is not None
        raise last_error
