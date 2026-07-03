"""
Infra de teste do pricetrack_api: sessão HTTP fake e relógio determinístico.

Nenhum teste toca a rede — o ``FakeSession`` substitui ``requests.Session``
e devolve respostas roteirizadas (sequenciais ou por handler), permitindo
simular paginação, códigos de erro e o fluxo assíncrono de export
(pending → processing → DONE/FAILED) sem I/O real.
"""
from __future__ import annotations

import json as jsonlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

import pytest

from pricetrack_api.config import PriceTrackSettings


@dataclass
class FakeResponse:
    """Réplica mínima de requests.Response para o transporte."""

    status_code: int = 200
    json_data: Optional[Any] = None
    content: bytes = b""
    headers: Dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        if self.json_data is not None:
            return jsonlib.dumps(self.json_data)
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        if self.json_data is None:
            raise ValueError("sem JSON")
        return self.json_data

    def iter_content(self, chunk_size: int = 8192):
        data = self.content
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]

    def close(self) -> None:
        pass


@dataclass
class RecordedCall:
    method: str
    url: str
    params: Optional[Dict] = None
    json: Optional[Dict] = None
    headers: Optional[Dict] = None
    stream: bool = False


ScriptItem = Union[FakeResponse, Exception, Callable[[RecordedCall], FakeResponse]]


class FakeSession:
    """Sessão roteirizada.

    * ``responses``: lista consumida em ordem — itens podem ser FakeResponse,
      Exception (levantada) ou callable(call) → FakeResponse.
    * ``handler``: alternativa por função única (útil para fluxos com estado,
      como polling de vários exports intercalados).
    """

    def __init__(
        self,
        responses: Optional[List[ScriptItem]] = None,
        handler: Optional[Callable[[RecordedCall], ScriptItem]] = None,
    ):
        assert (responses is None) != (handler is None), \
            "use responses OU handler"
        self._responses = list(responses or [])
        self._handler = handler
        self.calls: List[RecordedCall] = []

    def request(self, method: str, url: str, headers=None, params=None,
                json=None, timeout=None, stream=False) -> FakeResponse:
        call = RecordedCall(method=method, url=url, params=params, json=json,
                            headers=headers, stream=stream)
        self.calls.append(call)
        if self._handler is not None:
            item = self._handler(call)
        else:
            assert self._responses, f"FakeSession sem resposta para {method} {url}"
            item = self._responses.pop(0)
        if callable(item) and not isinstance(item, FakeResponse):
            item = item(call)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClock:
    """Relógio monotônico controlável; o sleep fake avança o tempo."""

    def __init__(self, start: float = 1000.0):
        self.now = start
        self.sleeps: List[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def settings(tmp_path) -> PriceTrackSettings:
    """Settings de teste: retries e tempos mínimos, dados no tmp do pytest."""
    return PriceTrackSettings(
        api_key="test-key-nunca-logar",
        max_retries=2,
        backoff_base_seconds=0.5,
        backoff_max_seconds=4.0,
        poll_interval_seconds=1.0,
        poll_timeout_seconds=300.0,
        page_take=2,
        export_threshold_rows=100,
        data_dir=tmp_path / "pricetrack",
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def paged_payload(items: List[Dict], page: int, take: int, total: int) -> Dict:
    """Monta uma resposta paginada fiel ao formato {data, meta} da API."""
    import math
    page_count = math.ceil(total / take) if take else 0
    return {
        "data": items,
        "meta": {
            "page": page,
            "take": take,
            "pageCount": page_count,
            "hasNextPage": page < page_count,
            "hasPreviousPage": page > 1,
        },
    }


def offer_payload(oid: str = "of-1", **overrides) -> Dict:
    """Oferta camelCase fiel ao schema Offer da OpenAPI."""
    base = {
        "id": oid,
        "sku": "42MACA09S5",
        "title": "ar condicionado split 9000 btus",
        "productName": "Ar Condicionado Split Midea Xtreme 9000 BTUs Frio",
        "brand": "MIDEA",
        "category": "AR CONDICIONADO",
        "subcategory": "SPLIT",
        "family": "XTREME",
        "color": None,
        "marketplace": "MERCADO LIVRE",
        "seller": "WEBCONTINENTAL",
        "spotPrice": 1994.91,
        "forwardPrice": 2099.90,
        "pixPrice": None,
        "priceFrom": 2599.00,
        "installmentNumber": 10,
        "installmentValue": 209.99,
        "status": "AVAILABLE",
        "collectionDate": "2026-07-01",
        "collectionHour": "09",
        "imageUrl": "https://cdn.example/img.jpg",
        "screenshotUrl": None,
        "url": "https://loja.example/produto",
    }
    base.update(overrides)
    return base
