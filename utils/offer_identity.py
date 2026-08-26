"""
utils/offer_identity.py — Identidade estável de oferta (Fase 1 da auditoria).

Antes deste módulo, todo identificador de marketplace era extraído pelos
coletores, usado para montar a URL e **descartado** (ver
`reports/AUDITORIA_COLETA_2026-08.md` §2). Sem ele não existe série histórica
de oferta: não dá para dizer se um preço mudou, se o anúncio saiu ou se quem
ganhou a buy box foi outro seller.

Três conceitos distintos, deliberadamente separados:

  ``marketplace_product_id``
      O identificador de PRODUTO do marketplace (ASIN, catálogo MLB, id da
      Casas Bahia…). Um produto pode ter N ofertas de N sellers.

  ``marketplace_offer_id``
      O identificador da OFERTA individual — só preenchido quando o
      marketplace de fato expõe um. **Nunca é sintetizado.** Um id inventado
      pareceria autoridade que o dado não tem; quem precisa de uma chave
      sempre-presente usa ``offer_key``.

  ``offer_key``
      Chave DERIVADA e VERSIONADA (prefixo ``v1|``), sempre preenchida. É o
      fallback explícito que a especificação pede quando não há offer id
      nativo. Muda de versão se a regra de derivação mudar, para que séries
      antigas não sejam comparadas com novas por acidente.

Funções puras, sem rede e sem estado: recebem string, devolvem string.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import parse_qsl, urlsplit, urlunsplit

# Versão da regra de derivação de `offer_key`. Bump obrigatório sempre que a
# composição da chave mudar — séries de versões diferentes não se comparam.
OFFER_KEY_VERSION = "v1"

# Parâmetros de query que são rastreamento/navegação, nunca identidade.
# Removidos da URL canônica para que a mesma oferta não vire duas.
_TRACKING_PARAMS = frozenset({
    "ref", "ref_", "tag", "utm_source", "utm_medium", "utm_campaign",
    "utm_term", "utm_content", "utm_id", "gclid", "fbclid", "msclkid",
    "pdp_filters", "search_layout", "position", "type", "tracking_id",
    "wid", "sid", "psc", "th", "linkcode", "creative", "creativeasin",
    "ascsubtag", "smid", "qid", "sr", "keywords", "source", "srsltid",
    "cor", "sellerid", "idlojista", "seller_id", "partner_id",
})

# Segmentos de path que a Amazon anexa para rastreio (…/dp/ASIN/ref=sr_1_93).
_AMAZON_REF_SEGMENT = re.compile(r"/ref=[^/]*$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Padrões de identidade por plataforma — validados contra URLs reais da base
# de produção (amostra de 2026-08-20+; ver PR da Fase 1).
# ---------------------------------------------------------------------------

# Amazon: /dp/B0CPTGF6HY/... ou /gp/product/B0CPTGF6HY
_RE_AMAZON_ASIN = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:[/?]|$)")

# Mercado Livre — DOIS namespaces distintos, e a diferença importa:
#   /p/MLB54211169     → produto de CATÁLOGO (buy box disputada por N sellers)
#   /MLB-1234567890-…  → ANÚNCIO individual de um seller
#   /up/MLBU1175327411 → anúncio "user product"
_RE_ML_CATALOG = re.compile(r"/p/(MLB\d+)(?:[/?#]|$)", re.IGNORECASE)
_RE_ML_ITEM = re.compile(r"/(MLB-?\d+|up/MLBU\d+)(?:[-/?#]|$)", re.IGNORECASE)

# Magalu: /p/de2449bfe8/ar/arpr/
_RE_MAGALU_PRODUCT = re.compile(r"/p/([a-z0-9]{6,})(?:[/?#]|$)", re.IGNORECASE)

# Casas Bahia: /p/1581081565
_RE_CB_PRODUCT = re.compile(r"/p/(\d{5,})(?:[/?#]|$)")

# Shopee: /product/<shopid>/<itemid>
_RE_SHOPEE = re.compile(r"/product/(\d+)/(\d+)(?:[/?#]|$)")

# Leroy Merlin: slug terminado em _<digits> — …-tcl_92311464
_RE_LEROY_PRODUCT = re.compile(r"_(\d{6,})(?:[/?#]|$)")

# Normalização de host: a mesma oferta do Magalu aparece em m. e www.
_HOST_ALIASES = {
    "m.magazineluiza.com.br": "www.magazineluiza.com.br",
    "magazineluiza.com.br": "www.magazineluiza.com.br",
    "produto.mercadolivre.com.br": "www.mercadolivre.com.br",
    "mercadolivre.com.br": "www.mercadolivre.com.br",
    "casasbahia.com.br": "www.casasbahia.com.br",
    "amazon.com.br": "www.amazon.com.br",
    "leroymerlin.com.br": "www.leroymerlin.com.br",
}


@dataclass(frozen=True)
class OfferIdentity:
    """Identidade derivada de uma oferta observada na SERP.

    Todos os campos são opcionais menos `offer_key`, que é sempre derivável
    (no pior caso, do hash da URL ou do título).
    """
    marketplace_product_id: Optional[str] = None
    marketplace_offer_id: Optional[str] = None
    seller_id: Optional[str] = None
    canonical_url: Optional[str] = None
    offer_key: Optional[str] = None


def _platform_slug(platform: Optional[str]) -> str:
    """Chave canônica de plataforma — estável a acento, caixa e pontuação."""
    if not platform:
        return "desconhecida"
    s = str(platform).strip().upper()
    acentos = "ÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ"
    planos = "AAAAAEEEEIIIIOOOOOUUUUC"
    s = s.translate(str.maketrans(acentos, planos))
    return re.sub(r"[^A-Z0-9]", "", s) or "desconhecida"


def canonicalize_url(url: Optional[str], platform: Optional[str] = None) -> Optional[str]:
    """Normaliza a URL para que a MESMA oferta produza SEMPRE a mesma string.

    Remove o que é rastreamento e mantém o que é identidade:
      - fragmento (`#...`) e segmento `/ref=...` da Amazon;
      - parâmetros de tracking (`utm_*`, `ref`, `position`, …);
      - `idLojista` / `seller_id`, que identificam o SELLER, não o produto —
        eles viram campo próprio (`seller_id`), senão a mesma página vira duas
        ofertas diferentes só porque o lojista mudou de posição na vitrine;
      - host alternativo (`m.magazineluiza` → `www.magazineluiza`);
      - barra final e caixa do host.

    Args:
        url:      URL crua vinda do card/payload.
        platform: nome da plataforma (usado só para regras específicas).

    Returns:
        URL canônica, ou None se a entrada for vazia/inválida.

    Example:
        >>> canonicalize_url(
        ...     "https://www.casasbahia.com.br/x/p/158?idLojista=19937", "Casas Bahia")
        'https://www.casasbahia.com.br/x/p/158'
    """
    if not url or not str(url).strip():
        return None

    raw = str(url).strip()
    if not raw.lower().startswith(("http://", "https://")):
        return None

    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if not parts.netloc:
        return None

    host = parts.netloc.lower()
    if host.startswith("www.www."):
        host = host[4:]
    host = _HOST_ALIASES.get(host, host)

    path = parts.path or "/"
    if "amazon" in host:
        path = _AMAZON_REF_SEGMENT.sub("", path)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"

    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS
    ]
    query = "&".join(f"{k}={v}" for k, v in sorted(kept))

    return urlunsplit(("https", host, path, query, ""))


def _first_group(pattern: re.Pattern, text: str) -> Optional[str]:
    m = pattern.search(text)
    return m.group(1) if m else None


def derive_from_url(
    url: Optional[str], platform: Optional[str] = None
) -> Dict[str, Optional[str]]:
    """Extrai product_id / offer_id / seller_id da URL, por plataforma.

    É a rede de segurança: mesmo quando o coletor não passa os ids
    explicitamente, a URL quase sempre os carrega. Coletores que têm o dado
    melhor (ASIN do `data-asin`, `sellerId` da Algolia) passam direto e
    **têm precedência** — ver `build_identity`.

    Returns:
        Dict com as chaves marketplace_product_id, marketplace_offer_id e
        seller_id (valores podem ser None).
    """
    out: Dict[str, Optional[str]] = {
        "marketplace_product_id": None,
        "marketplace_offer_id": None,
        "seller_id": None,
    }
    if not url or not str(url).strip():
        return out

    raw = str(url).strip()
    slug = _platform_slug(platform)
    params = {}
    try:
        params = {
            k.lower(): v for k, v in parse_qsl(urlsplit(raw).query, keep_blank_values=False)
        }
    except ValueError:
        params = {}

    if slug == "AMAZON":
        out["marketplace_product_id"] = _first_group(_RE_AMAZON_ASIN, raw)

    elif slug == "MERCADOLIVRE":
        # Catálogo e anúncio são namespaces diferentes: o catálogo é o produto
        # (buy box disputada), o MLB-… é a oferta de um seller específico.
        out["marketplace_product_id"] = _first_group(_RE_ML_CATALOG, raw)
        item = _first_group(_RE_ML_ITEM, raw)
        if item:
            item = item.replace("-", "").replace("up/", "").upper()
            # /p/MLB… já foi capturado como produto; não repetir como oferta.
            if item != (out["marketplace_product_id"] or "").upper():
                out["marketplace_offer_id"] = item

    elif slug == "MAGALU":
        out["marketplace_product_id"] = (
            (_first_group(_RE_MAGALU_PRODUCT, raw) or "").upper() or None
        )
        out["seller_id"] = params.get("seller_id") or None

    elif slug == "CASASBAHIA":
        out["marketplace_product_id"] = _first_group(_RE_CB_PRODUCT, raw)
        out["seller_id"] = params.get("idlojista") or None

    elif slug == "SHOPEE":
        m = _RE_SHOPEE.search(raw)
        if m:
            # A Shopee expõe id de oferta de verdade: o par (shopid, itemid)
            # identifica o anúncio, e o itemid sozinho identifica o produto.
            out["seller_id"] = m.group(1)
            out["marketplace_product_id"] = m.group(2)
            out["marketplace_offer_id"] = f"{m.group(1)}_{m.group(2)}"

    elif slug == "LEROYMERLIN":
        out["marketplace_product_id"] = _first_group(_RE_LEROY_PRODUCT, raw)

    return out


def build_offer_key(
    platform: Optional[str],
    *,
    marketplace_offer_id: Optional[str] = None,
    marketplace_product_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    canonical_url: Optional[str] = None,
    fallback: Optional[str] = None,
) -> Optional[str]:
    """Monta a chave versionada de oferta, do sinal mais forte para o mais fraco.

    Precedência:
      1. offer id nativo do marketplace (o mais forte que existe);
      2. product id + seller id (identifica a oferta daquele lojista);
      3. product id sozinho (produto, sem distinguir lojista);
      4. hash da URL canônica;
      5. hash do `fallback` (título normalizado), último recurso.

    O prefixo ``v1|`` é obrigatório: se a regra acima mudar, a versão sobe e
    as séries antigas param de casar com as novas — em vez de casarem errado
    e produzirem uma "mudança de preço" que é só mudança de chave.

    Returns:
        String ``v1|<plataforma>|<escopo>:<valor>``, ou None se não houver
        sinal algum.
    """
    slug = _platform_slug(platform)

    if marketplace_offer_id:
        return f"{OFFER_KEY_VERSION}|{slug}|offer:{marketplace_offer_id}"
    if marketplace_product_id and seller_id:
        return f"{OFFER_KEY_VERSION}|{slug}|prod:{marketplace_product_id}@{seller_id}"
    if marketplace_product_id:
        return f"{OFFER_KEY_VERSION}|{slug}|prod:{marketplace_product_id}"

    # Sem id de produto, o seller ainda distingue ofertas: a MESMA página de
    # produto vendida por dois lojistas são duas ofertas. Descartar o seller
    # aqui colapsaria as duas numa série só — o erro que esta fase existe para
    # evitar. Por isso ele é anexado também aos degraus derivados.
    sufixo = f"@{seller_id}" if seller_id else ""

    if canonical_url:
        digest = hashlib.sha1(canonical_url.encode("utf-8")).hexdigest()[:16]
        return f"{OFFER_KEY_VERSION}|{slug}|url:{digest}{sufixo}"
    if fallback and str(fallback).strip():
        norm = re.sub(r"\s+", " ", str(fallback).strip().casefold())
        digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]
        return f"{OFFER_KEY_VERSION}|{slug}|txt:{digest}{sufixo}"
    return None


def build_identity(
    platform: Optional[str],
    url: Optional[str],
    *,
    marketplace_product_id: Optional[str] = None,
    marketplace_offer_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    title: Optional[str] = None,
) -> OfferIdentity:
    """Resolve a identidade completa de uma oferta.

    Os ids passados pelo coletor **têm precedência** sobre os derivados da
    URL: quem leu `data-asin` ou o `sellerId` da Algolia tem o dado de
    primeira mão; a URL é reconstrução.

    Args:
        platform: nome da plataforma (``self.platform_name``).
        url:      URL do produto como veio do card/payload.
        marketplace_product_id: id de produto lido direto da fonte, se houver.
        marketplace_offer_id:   id de oferta lido direto da fonte, se houver.
        seller_id:              id do seller lido direto da fonte, se houver.
        title:    título do produto — último recurso para a `offer_key`.

    Returns:
        `OfferIdentity` com os campos resolvidos.
    """
    derived = derive_from_url(url, platform)

    def _clean(value) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    product_id = _clean(marketplace_product_id) or derived["marketplace_product_id"]
    offer_id = _clean(marketplace_offer_id) or derived["marketplace_offer_id"]
    seller = _clean(seller_id) or derived["seller_id"]
    canonical = canonicalize_url(url, platform)

    return OfferIdentity(
        marketplace_product_id=product_id,
        marketplace_offer_id=offer_id,
        seller_id=seller,
        canonical_url=canonical,
        offer_key=build_offer_key(
            platform,
            marketplace_offer_id=offer_id,
            marketplace_product_id=product_id,
            seller_id=seller,
            canonical_url=canonical,
            fallback=title,
        ),
    )
