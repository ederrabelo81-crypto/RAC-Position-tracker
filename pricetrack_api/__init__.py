"""
pricetrack_api — cliente tipado e resiliente da API Externa do PriceTrack.

Uso mínimo:

    from pricetrack_api import PriceTrackSettings, PriceTrackClient, SmartCollector

    settings = PriceTrackSettings.from_env()      # lê PRICETRACK_API_KEY etc.
    client = PriceTrackClient(settings)
    collector = SmartCollector(client)
    result = collector.collect_offers("2026-07-01")

Ver pricetrack_api/README.md para o fluxo completo.
"""
from .client import PriceTrackClient
from .collector import (
    STRATEGY_AUTO,
    STRATEGY_EXPORT,
    STRATEGY_PAGINATED,
    CollectionResult,
    SmartCollector,
)
from .config import PriceTrackSettings
from .exceptions import (
    DownloadUrlExpiredError,
    ExportFailedError,
    ExportTimeoutError,
    PriceTrackAuthError,
    PriceTrackBadRequestError,
    PriceTrackConfigError,
    PriceTrackError,
    PriceTrackExportLimitError,
    PriceTrackHTTPError,
    PriceTrackNetworkError,
    PriceTrackNoCollectionError,
    PriceTrackServerError,
)
from .exports import (
    OUTCOME_ERROR,
    OUTCOME_FAILED,
    OUTCOME_NO_DATA,
    OUTCOME_OK,
    OUTCOME_TIMEOUT,
    ExportManager,
    ExportOutcome,
)
from .metrics import (
    AlertSink,
    CollectionMetrics,
    LogAlertSink,
    TelegramAlertSink,
    alert_if_failed,
)
from .models import (
    CollectQuery,
    ExportJob,
    ExportRequest,
    Offer,
    Page,
    PageMeta,
    Shipping,
)
from .normalize import NormalizedPrices, clean_price, effective_price, normalize_prices
from .store import NdjsonStore, UpsertStats

__all__ = [
    # cliente / config
    "PriceTrackClient", "PriceTrackSettings",
    # coleta
    "SmartCollector", "CollectionResult",
    "STRATEGY_AUTO", "STRATEGY_PAGINATED", "STRATEGY_EXPORT",
    # exports
    "ExportManager", "ExportOutcome",
    "OUTCOME_OK", "OUTCOME_NO_DATA", "OUTCOME_FAILED",
    "OUTCOME_TIMEOUT", "OUTCOME_ERROR",
    # models
    "Offer", "Shipping", "Page", "PageMeta",
    "CollectQuery", "ExportRequest", "ExportJob",
    # storage
    "NdjsonStore", "UpsertStats",
    # normalização
    "NormalizedPrices", "normalize_prices", "clean_price", "effective_price",
    # observabilidade
    "CollectionMetrics", "AlertSink", "LogAlertSink", "TelegramAlertSink",
    "alert_if_failed",
    # exceções
    "PriceTrackError", "PriceTrackConfigError", "PriceTrackHTTPError",
    "PriceTrackAuthError", "PriceTrackBadRequestError",
    "PriceTrackNoCollectionError", "PriceTrackExportLimitError",
    "PriceTrackServerError", "PriceTrackNetworkError",
    "ExportFailedError", "ExportTimeoutError", "DownloadUrlExpiredError",
]
