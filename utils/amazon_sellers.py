"""
utils/amazon_sellers.py — resolução de buy box da Amazon via PDP + cache.

Contexto do problema (diagnóstico Ago/2026)
-------------------------------------------
A SERP da Amazon **não** mostra "Vendido por": esse dado só existe no PDP.
Por isso `scrapers/amazon.py` deixa `buy_box_seller` como None quando o card
não revela o vendedor — decisão correta, porque marcar "Amazon/1P" por omissão
transformaria 100% dos registros em vitória 1P fantasma e destruiria o share
of buy box. O efeito colateral é o campo medido em **0% de preenchimento** no
Supabase em 10/08/2026, sobre 8.058 registros.

Diferença importante em relação ao Leroy
----------------------------------------
No Leroy a chave de resolução é o *seller ID*: 1.707 registros colapsam em
algumas dezenas de IDs, e o custo de rede tende a zero na segunda coleta. Aqui
a chave é o **ASIN** (o produto), então o cache colapsa muito menos e cada ASIN
novo custa um PDP. Como a Amazon é a plataforma mais agressiva em anti-bot do
conjunto — e hoje entrega 8.058 linhas de forma estável —, a resolução é:

  1. **Desligada por padrão.** Só roda com ``RAC_AMAZON_PDP_BUYBOX=1``.
  2. **Com teto por execução** (``RAC_AMAZON_PDP_BUDGET``, default 40). Batendo
     o teto, os demais registros seguem sem buy box em vez de arriscar o
     bloqueio da coleta inteira — degradar é melhor que derrubar.
  3. **Com quarentena** para ASIN que falhou, para não gastar o orçamento no
     mesmo produto morto a cada execução.

Este módulo é puro (sem Playwright/rede): recebe o HTML do PDP e devolve o
nome do vendedor. O scraper cuida do fetch. Isso mantém a extração testável
offline, no mesmo desenho de ``utils/leroy_sellers.py``.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from bs4 import BeautifulSoup
from loguru import logger

# Raiz do repositório — o cache vive em data/ para sobreviver entre execuções.
_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_PATH = _REPO_ROOT / "data" / "amazon_sellers.json"

#: Env que liga a resolução. Ausente/0 → o scraper nem chama este módulo.
ENV_ENABLED = "RAC_AMAZON_PDP_BUYBOX"
#: Env que limita PDPs por execução.
ENV_BUDGET = "RAC_AMAZON_PDP_BUDGET"
DEFAULT_BUDGET = 40

#: Nomes que representam a própria Amazon (1P), em PT-BR e EN.
_AMAZON_SELF = {
    "amazon", "amazon.com.br", "amazon com br", "amazon br",
    "vendido pela amazon", "amazon.com",
}

# "Vendido por X" / "Sold by X" — o PDP usa ambos conforme o A/B em vigor.
_RE_VENDIDO_POR = re.compile(
    r"(?:vendido\s+(?:e\s+entregue\s+)?por|sold\s+by)\s*[:\-]?\s*(.+)",
    re.IGNORECASE,
)

#: Seletores do bloco de compra do PDP, do mais específico ao mais genérico.
_PDP_SELLER_SELECTORS = (
    "#sellerProfileTriggerId",
    "#merchant-info a",
    "#merchant-info",
    "#tabular-buybox .tabular-buybox-text[tabular-attribute-name='Vendido por']",
    "#tabular-buybox .tabular-buybox-text",
    "[data-feature-name='bylineInfo'] a",
    "#bylineInfo",
)


def is_amazon_self(name: Optional[str]) -> bool:
    """True se o nome representa a própria Amazon (venda 1P)."""
    return (name or "").strip().lower() in _AMAZON_SELF


def clean_seller_name(raw: Any) -> Optional[str]:
    """
    Normaliza o nome do vendedor extraído do PDP.

    Remove o prefixo "Vendido por"/"Sold by", colapsa espaços e descarta
    ruídos comuns do bloco de compra (preço, frete, avaliação).

    Args:
        raw: texto bruto do elemento, ou None.

    Returns:
        Nome limpo, ou None se não sobrou nada utilizável.

    Example:
        >>> clean_seller_name("Vendido por: KARZEN ELETRO ")
        'KARZEN ELETRO'
    """
    if raw is None:
        return None
    text = " ".join(str(raw).split())
    if not text:
        return None

    match = _RE_VENDIDO_POR.search(text)
    if match:
        text = match.group(1).strip()

    # Corta em separadores que iniciam outro campo do bloco de compra.
    for sep in (" e enviado por", " and Fulfilled by", "|", " • ", "  "):
        if sep in text:
            text = text.split(sep)[0].strip()

    text = text.strip(" .:-—‎‏")

    # Restos óbvios de outro campo — não são nome de vendedor.
    if not text or len(text) > 80:
        return None
    if re.fullmatch(r"[\d\s.,R$%]+", text):
        return None
    return text


def extract_seller_from_pdp(html: str) -> Optional[str]:
    """
    Extrai o vendedor da buy box a partir do HTML do PDP da Amazon.

    Tenta, em ordem: seletores conhecidos do bloco de compra → varredura de
    texto por "Vendido por"/"Sold by". A busca por texto é o fallback que
    sobrevive a redesign de CSS, que é o modo como a Amazon quebra parsers.

    Args:
        html: HTML completo do PDP.

    Returns:
        Nome do vendedor, ou None se o PDP não revelou (página de erro,
        challenge de bot, ou produto indisponível).

    Example:
        >>> extract_seller_from_pdp('<div id="merchant-info">Vendido por LojaX</div>')
        'LojaX'
    """
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    for selector in _PDP_SELLER_SELECTORS:
        try:
            element = soup.select_one(selector)
        except Exception:  # seletor inválido em versão antiga do soupsieve
            continue
        if element is None:
            continue
        name = clean_seller_name(element.get_text(" ", strip=True))
        if name:
            return name

    # Fallback por texto: o rótulo PT-BR é mais estável que o id/classe.
    hit = soup.find(string=_RE_VENDIDO_POR)
    if hit:
        name = clean_seller_name(str(hit))
        if name:
            return name

    return None


def resolution_enabled() -> bool:
    """True se a resolução via PDP está ligada por env (default: desligada)."""
    return os.getenv(ENV_ENABLED, "").strip().lower() in ("1", "true", "yes", "on")


def pdp_budget() -> int:
    """Teto de PDPs por execução (``RAC_AMAZON_PDP_BUDGET``, default 40)."""
    raw = os.getenv(ENV_BUDGET, "").strip()
    if raw.isdigit() and int(raw) >= 0:
        return int(raw)
    return DEFAULT_BUDGET


class AmazonSellerCache:
    """
    Cache persistente ASIN → nome do vendedor da buy box.

    Vive em ``data/amazon_sellers.json`` (versionável, editável à mão). ASINs
    que falharam ficam em quarentena por ``retry_days``, para não gastar o
    orçamento de PDPs no mesmo produto morto a cada execução.

    Attributes:
        path: caminho do arquivo JSON.
        retry_days: dias de quarentena antes de retentar um ASIN falhado.
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        retry_days: int = 7,
    ) -> None:
        self.path = Path(path) if path else DEFAULT_CACHE_PATH
        self.retry_days = retry_days
        self._sellers: Dict[str, str] = {}
        self._unresolved: Dict[str, Dict[str, Any]] = {}
        self._dirty = False
        self._load()

    # -- persistência ------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"[AmazonSellers] Cache ilegível ({self.path}): {exc}")
            return

        if not isinstance(data, dict):
            logger.warning(
                f"[AmazonSellers] Cache com raiz inesperada "
                f"({type(data).__name__}, esperado objeto): {self.path}"
            )
            return

        sellers = data.get("sellers")
        if isinstance(sellers, dict):
            self._sellers = {
                str(k): str(v) for k, v in sellers.items() if k and v
            }
        unresolved = data.get("unresolved")
        if isinstance(unresolved, dict):
            self._unresolved = {
                str(k): v for k, v in unresolved.items() if isinstance(v, dict)
            }
        logger.debug(
            f"[AmazonSellers] Cache carregado: {len(self._sellers)} ASINs, "
            f"{len(self._unresolved)} em quarentena"
        )

    def save(self) -> bool:
        """Grava o cache em disco. Retorna True se escreveu algo."""
        if not self._dirty:
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "_comment": (
                    "ASIN → vendedor da buy box, resolvido via PDP da Amazon. "
                    "Gerado por scrapers/amazon.py; editável à mão e seguro "
                    "para versionar."
                ),
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "sellers": dict(sorted(self._sellers.items())),
                "unresolved": self._unresolved,
            }
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self._dirty = False
            return True
        except OSError as exc:
            logger.warning(f"[AmazonSellers] Não foi possível salvar o cache: {exc}")
            return False

    # -- API ---------------------------------------------------------

    def get(self, asin: str) -> Optional[str]:
        """Vendedor já conhecido para o ASIN, ou None."""
        return self._sellers.get(asin)

    def put(self, asin: str, name: str) -> None:
        """Registra um ASIN resolvido (e o tira da quarentena)."""
        clean = clean_seller_name(name)
        if not asin or not clean:
            return
        if self._sellers.get(asin) != clean:
            self._sellers[asin] = clean
            self._dirty = True
        if asin in self._unresolved:
            del self._unresolved[asin]
            self._dirty = True

    def mark_failed(self, asin: str, reason: str = "") -> None:
        """Põe o ASIN em quarentena após um PDP que não revelou o vendedor."""
        if not asin:
            return
        self._unresolved[asin] = {
            "last_try": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "reason": reason[:120],
        }
        self._dirty = True

    def should_retry(self, asin: str) -> bool:
        """
        True se vale gastar um PDP com este ASIN agora.

        False quando já está resolvido, ou quando falhou há menos de
        ``retry_days`` — é o que impede o orçamento de ser consumido pelos
        mesmos produtos mortos a cada execução.
        """
        if not asin or asin in self._sellers:
            return False
        entry = self._unresolved.get(asin)
        if not entry:
            return True
        try:
            last = datetime.fromisoformat(str(entry.get("last_try")))
        except (TypeError, ValueError):
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - last >= timedelta(days=self.retry_days)

    def __len__(self) -> int:
        return len(self._sellers)
