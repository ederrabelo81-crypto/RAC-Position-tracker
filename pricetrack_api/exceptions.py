"""
Taxonomia de erros do cliente PriceTrack.

Cada código de status relevante da API vira uma exceção própria, para que a
camada de orquestração decida a política correta (retry, espera por slot,
abort) sem inspecionar strings ou ``response.status_code``.

Nenhuma exceção carrega headers de request — a API key nunca aparece em
mensagens de erro nem em logs.
"""
from __future__ import annotations

from typing import Optional


class PriceTrackError(Exception):
    """Base de todos os erros do cliente PriceTrack."""


class PriceTrackConfigError(PriceTrackError):
    """Configuração inválida ou ausente (ex.: PRICETRACK_API_KEY não setada)."""


class PriceTrackHTTPError(PriceTrackError):
    """Erro HTTP com status conhecido. Guarda o status e um excerto do corpo."""

    def __init__(self, message: str, status_code: Optional[int] = None,
                 body_excerpt: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body_excerpt = body_excerpt


class PriceTrackAuthError(PriceTrackHTTPError):
    """401 — API key ausente, inválida ou revogada. Nunca vale retry."""


class PriceTrackBadRequestError(PriceTrackHTTPError):
    """400 — filtros/parâmetros inválidos (ex.: collectionDate malformado)."""


class PriceTrackNoCollectionError(PriceTrackHTTPError):
    """409 — nenhuma tabela de coleta encontrada para a collectionDate pedida."""


class PriceTrackExportLimitError(PriceTrackHTTPError):
    """429 — limite de 3 exports concorrentes por organização atingido.

    O ExportManager trata esperando um slot liberar; não é erro fatal.
    """

    def __init__(self, message: str, status_code: int = 429,
                 body_excerpt: str = "", retry_after: Optional[float] = None):
        super().__init__(message, status_code, body_excerpt)
        self.retry_after = retry_after


class PriceTrackServerError(PriceTrackHTTPError):
    """5xx — erro do lado da API. Retryable com backoff exponencial."""


class PriceTrackNetworkError(PriceTrackError):
    """Falha de rede/timeout antes de haver resposta. Retryable."""


class ExportFailedError(PriceTrackError):
    """Export assíncrono terminou com status FAILED."""

    def __init__(self, export_id: str, message: str = ""):
        super().__init__(message or f"Export {export_id} terminou como FAILED")
        self.export_id = export_id


class ExportTimeoutError(PriceTrackError):
    """Polling excedeu o tempo máximo sem o export chegar a DONE/FAILED."""

    def __init__(self, export_id: str, waited_seconds: float):
        super().__init__(
            f"Export {export_id} não concluiu em {waited_seconds:.0f}s"
        )
        self.export_id = export_id
        self.waited_seconds = waited_seconds


class DownloadUrlExpiredError(PriceTrackError):
    """downloadUrl pré-assinada expirou (TTL 1h). O cliente renova via status."""
