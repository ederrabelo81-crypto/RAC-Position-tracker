"""
Models tipados da API Externa do PriceTrack (v1.2.0).

Fiéis aos schemas ``Offer`` e ``Shipping`` da OpenAPI, incluindo campos
nullable (``color``, ``pixPrice``, ``screenshotUrl``). Os parsers aceitam
tanto camelCase (endpoints paginados) quanto snake_case (linhas do export
NDJSON), que divergem na prática.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Generic, Iterable, List, Optional, Sequence, TypeVar

T = TypeVar("T")

# ── Helpers de parsing tolerante ─────────────────────────────────────────────


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def pick(raw: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Primeiro valor não-None dentre as chaves, testando camelCase e snake_case."""
    for key in keys:
        for variant in (key, _snake(key), key.lower()):
            if variant in raw and raw[variant] is not None:
                return raw[variant]
    return default


def to_float(value: Any) -> Optional[float]:
    """Converte para float; None/''/inválido → None (campos nullable do schema)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value: Any) -> Optional[int]:
    f = to_float(value)
    return int(f) if f is not None else None


def to_hour(value: Any) -> Optional[int]:
    """Normaliza collectionHour ('HH', '08:00', 8) para int 0-23."""
    if value is None:
        return None
    text = str(value).strip()
    match = re.match(r"^(\d{1,2})", text)
    if not match:
        return None
    hour = int(match.group(1))
    return hour if 0 <= hour <= 23 else None


def to_date(value: Any) -> Optional[date]:
    """Converte 'YYYY-MM-DD' (ou ISO datetime) para date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def to_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


STATUS_AVAILABLE = "AVAILABLE"
STATUS_UNAVAILABLE = "UNAVAILABLE"

_HOUR_RANGE_RE = re.compile(r"^\d{2}:\d{2}-\d{2}:\d{2}$")


# ── Schemas de dados ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Offer:
    """Uma oferta coletada — schema ``Offer`` da API."""

    id: str
    sku: str
    title: str
    product_name: str
    brand: str
    category: str
    subcategory: str
    family: str
    color: Optional[str]
    marketplace: str
    seller: str
    spot_price: Optional[float]
    forward_price: Optional[float]
    pix_price: Optional[float]          # nullable no schema
    price_from: Optional[float]         # RRP / preço "de"
    installment_number: Optional[int]
    installment_value: Optional[float]
    status: str                         # AVAILABLE | UNAVAILABLE
    collection_date: Optional[date]
    collection_hour: Optional[int]      # HH
    image_url: str
    screenshot_url: Optional[str]       # nullable no schema
    url: str

    @property
    def is_available(self) -> bool:
        return self.status == STATUS_AVAILABLE

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "Offer":
        """Constrói a partir do JSON da API (camelCase ou snake_case)."""
        color = pick(raw, "color")
        screenshot = pick(raw, "screenshotUrl")
        return cls(
            id=to_str(pick(raw, "id")),
            sku=to_str(pick(raw, "sku", "productSku")),
            title=to_str(pick(raw, "title", "searchTitle")),
            product_name=to_str(pick(raw, "productName", "productsName")),
            brand=to_str(pick(raw, "brand", "productBrand")),
            category=to_str(pick(raw, "category", "productCategory")),
            subcategory=to_str(pick(raw, "subcategory", "productSubcategory")),
            family=to_str(pick(raw, "family", "productFamily")),
            color=to_str(color) if color is not None else None,
            marketplace=to_str(pick(raw, "marketplace")),
            seller=to_str(pick(raw, "seller")),
            spot_price=to_float(pick(raw, "spotPrice")),
            forward_price=to_float(pick(raw, "forwardPrice")),
            pix_price=to_float(pick(raw, "pixPrice")),
            price_from=to_float(pick(raw, "priceFrom")),
            installment_number=to_int(pick(raw, "installmentNumber")),
            installment_value=to_float(pick(raw, "installmentValue")),
            status=to_str(pick(raw, "status")).upper(),
            collection_date=to_date(pick(raw, "collectionDate")),
            collection_hour=to_hour(pick(raw, "collectionHour")),
            image_url=to_str(pick(raw, "imageUrl")),
            screenshot_url=to_str(screenshot) if screenshot is not None else None,
            url=to_str(pick(raw, "url")),
        )


@dataclass(frozen=True, slots=True)
class Shipping:
    """Um frete coletado — schema ``Shipping`` da API."""

    id: str
    sku: str
    title: str
    product_name: str
    brand: str
    category: str
    subcategory: str
    family: str
    marketplace: str
    seller: str
    cep: str
    shipping_cost: Optional[float]
    deadline: Optional[int]             # dias
    transporter_type: str
    status: str
    collection_date: Optional[date]
    collection_hour: Optional[int]
    url: str

    @property
    def is_available(self) -> bool:
        return self.status == STATUS_AVAILABLE

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "Shipping":
        return cls(
            id=to_str(pick(raw, "id")),
            sku=to_str(pick(raw, "sku", "productSku")),
            title=to_str(pick(raw, "title", "searchTitle")),
            product_name=to_str(pick(raw, "productName", "productsName")),
            brand=to_str(pick(raw, "brand", "productBrand")),
            category=to_str(pick(raw, "category", "productCategory")),
            subcategory=to_str(pick(raw, "subcategory", "productSubcategory")),
            family=to_str(pick(raw, "family", "productFamily")),
            marketplace=to_str(pick(raw, "marketplace")),
            seller=to_str(pick(raw, "seller")),
            cep=to_str(pick(raw, "cep")),
            shipping_cost=to_float(pick(raw, "shippingCost")),
            deadline=to_int(pick(raw, "deadline")),
            transporter_type=to_str(pick(raw, "transporterType")),
            status=to_str(pick(raw, "status")).upper(),
            collection_date=to_date(pick(raw, "collectionDate")),
            collection_hour=to_hour(pick(raw, "collectionHour")),
            url=to_str(pick(raw, "url")),
        )


# ── Paginação ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PageMeta:
    """Bloco ``meta`` das respostas paginadas."""

    page: int
    take: int
    page_count: int
    has_next_page: bool
    has_previous_page: bool

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "PageMeta":
        return cls(
            page=to_int(pick(raw, "page")) or 1,
            take=to_int(pick(raw, "take")) or 0,
            page_count=to_int(pick(raw, "pageCount")) or 0,
            has_next_page=bool(pick(raw, "hasNextPage", default=False)),
            has_previous_page=bool(pick(raw, "hasPreviousPage", default=False)),
        )


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    """Uma página de resultados: itens parseados + dicts crus + meta."""

    data: List[T]
    raw: List[Dict[str, Any]]
    meta: PageMeta


# ── Queries ──────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class CollectQuery:
    """Filtros dos endpoints paginados (/collects-offers|shipping-external).

    ``collection_date`` é obrigatório em toda coleta (granularidade diária da
    API). Filtros plurais viajam como arrays (params repetidos na query).
    """

    collection_date: date | str
    marketplace: Optional[Sequence[str]] = None
    seller: Optional[Sequence[str]] = None
    product_brand: Optional[Sequence[str]] = None
    product_category: Optional[Sequence[str]] = None
    product_subcategory: Optional[Sequence[str]] = None
    product_family: Optional[Sequence[str]] = None
    product_sku: Optional[Sequence[str]] = None
    products_name: Optional[Sequence[str]] = None
    color: Optional[Sequence[str]] = None
    search_title: Optional[str] = None
    status: Optional[str] = None                    # AVAILABLE | UNAVAILABLE
    collection_hour_range: Optional[str] = None     # "HH:MM-HH:MM"
    spot_price_min: Optional[float] = None
    spot_price_max: Optional[float] = None
    forward_price_min: Optional[float] = None
    forward_price_max: Optional[float] = None
    order: Optional[str] = None                     # ASC | DESC
    order_by_column: Optional[str] = None
    page: int = 1
    take: int = 10

    def __post_init__(self) -> None:
        cd = to_date(self.collection_date)
        if cd is None:
            raise ValueError(
                f"collectionDate obrigatório em formato YYYY-MM-DD "
                f"(recebido: {self.collection_date!r})"
            )
        self.collection_date = cd
        if self.collection_hour_range and not _HOUR_RANGE_RE.match(
            self.collection_hour_range
        ):
            raise ValueError(
                "collectionHourRange deve ter formato HH:MM-HH:MM "
                f"(recebido: {self.collection_hour_range!r})"
            )
        if self.order and self.order.upper() not in ("ASC", "DESC"):
            raise ValueError(f"order deve ser ASC ou DESC (recebido: {self.order!r})")

    def to_params(self) -> Dict[str, Any]:
        """Query params no formato da API (camelCase; arrays repetidos)."""
        params: Dict[str, Any] = {
            "collectionDate": self.collection_date.isoformat(),
            "page": self.page,
            "take": self.take,
        }
        arrays = {
            "marketplace": self.marketplace,
            "seller": self.seller,
            "productBrand": self.product_brand,
            "productCategory": self.product_category,
            "productSubcategory": self.product_subcategory,
            "productFamily": self.product_family,
            "productSku": self.product_sku,
            "productsName": self.products_name,
            "color": self.color,
        }
        for key, value in arrays.items():
            if value:
                params[key] = list(value)
        scalars = {
            "searchTitle": self.search_title,
            "status": self.status,
            "collectionHourRange": self.collection_hour_range,
            "spotPriceMin": self.spot_price_min,
            "spotPriceMax": self.spot_price_max,
            "forwardPriceMin": self.forward_price_min,
            "forwardPriceMax": self.forward_price_max,
            "order": self.order.upper() if self.order else None,
            "orderByColumn": self.order_by_column,
        }
        for key, value in scalars.items():
            if value is not None:
                params[key] = value
        return params


# ── Export assíncrono ────────────────────────────────────────────────────────


@dataclass(slots=True)
class ExportRequest:
    """Body do POST /exports-external/collects-{offers,shipping}."""

    collection_date: date | str
    marketplaces: Optional[Sequence[str]] = None
    collection_hour_execution_range: Optional[str] = None   # "HH:MM-HH:MM"

    def __post_init__(self) -> None:
        cd = to_date(self.collection_date)
        if cd is None:
            raise ValueError(
                f"collectionDate obrigatório em formato YYYY-MM-DD "
                f"(recebido: {self.collection_date!r})"
            )
        self.collection_date = cd
        if self.collection_hour_execution_range and not _HOUR_RANGE_RE.match(
            self.collection_hour_execution_range
        ):
            raise ValueError(
                "collectionHourExecutionRange deve ter formato HH:MM-HH:MM"
            )

    def to_body(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {"collectionDate": self.collection_date.isoformat()}
        if self.marketplaces:
            body["marketplaces"] = list(self.marketplaces)
        if self.collection_hour_execution_range:
            body["collectionHourExecutionRange"] = self.collection_hour_execution_range
        return body


EXPORT_PENDING = "PENDING"
EXPORT_PROCESSING = "PROCESSING"
EXPORT_DONE = "DONE"
EXPORT_FAILED = "FAILED"
_ACTIVE_STATUSES = frozenset({EXPORT_PENDING, EXPORT_PROCESSING})
_TERMINAL_STATUSES = frozenset({EXPORT_DONE, EXPORT_FAILED})


@dataclass(slots=True)
class ExportJob:
    """Estado de um export assíncrono (POST cria; GET /{exportId} atualiza).

    ``fetched_at`` (relógio monotônico) marca quando este snapshot foi lido —
    é a base para tratar a downloadUrl como efêmera (TTL de 1h).
    """

    export_id: str
    status: str
    status_url: str = ""
    download_url: Optional[str] = None
    format: str = ""
    row_count: Optional[int] = None
    file_size_bytes: Optional[int] = None
    progress: Optional[float] = None
    fetched_at: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_active(self) -> bool:
        return self.status in _ACTIVE_STATUSES

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def download_url_stale(self, ttl_seconds: float, now: float) -> bool:
        """True se a URL pré-assinada provavelmente já expirou (TTL 1h)."""
        return (now - self.fetched_at) >= ttl_seconds

    @classmethod
    def from_api(cls, raw: Dict[str, Any], fetched_at: float = 0.0) -> "ExportJob":
        return cls(
            export_id=to_str(pick(raw, "exportId", "id")),
            status=to_str(pick(raw, "status")).upper(),
            status_url=to_str(pick(raw, "statusUrl")),
            download_url=pick(raw, "downloadUrl"),
            format=to_str(pick(raw, "format")),
            row_count=to_int(pick(raw, "rowCount")),
            file_size_bytes=to_int(pick(raw, "fileSizeBytes")),
            progress=to_float(pick(raw, "progress")),
            fetched_at=fetched_at,
            raw=dict(raw),
        )


def record_id(raw: Dict[str, Any]) -> str:
    """Chave de deduplicação de um registro cru: o ``id`` da oferta/frete.

    Registros sem ``id`` (nunca deveriam existir) recebem uma chave sintética
    determinística baseada no conteúdo — dedup de linhas idênticas sem jamais
    descartar dados distintos.
    """
    rid = pick(raw, "id")
    if rid is not None and to_str(rid):
        return to_str(rid)
    import hashlib
    import json
    canonical = json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str)
    return "synthetic-" + hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def iter_ndjson_records(lines: Iterable[str]):
    """Itera registros de um NDJSON, ignorando linhas vazias/corrompidas.

    Yields:
        Tuplas ``(raw_dict, None)`` para linhas válidas e ``(None, line)``
        para linhas inválidas (o chamador contabiliza como inválidas).
    """
    import json
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line), None
        except json.JSONDecodeError:
            yield None, line
