"""
utils/leroy_sellers.py — resolução de seller da Leroy Merlin via PDP + cache.

Contexto do problema (diagnóstico Jul/2026)
-------------------------------------------
O índice Algolia `production_products` da Leroy Merlin expõe o seller apenas
como *ObjectId* opaco em ``marketplaceSellers`` (ex: ``"5e6fd1d90a8aa474fe271e83"``).
O índice **não** carrega o nome do seller em nenhum campo: não há ``sellers[]``,
``installmentsBySeller``, ``sellerName`` nem similares — comprovado pelas
métricas de produção (``resolved_via_inline_hit`` e ``resolved_via_scalar_fields``
ficaram em 0 sobre 1.707 registros).

A tentativa anterior de resolver pela API de catálogo VTEX
(``/api/catalog_system/pub/seller/{id}``) também é estruturalmente inválida:
a Leroy Merlin BR **não roda VTEX** (Next.js + Algolia + API interna), então o
endpoint nunca responde um nome — ``resolved_via_vtex_api`` também ficou em 0.

Solução
-------
A única fonte que expõe o nome do seller é o **PDP** ("Vendido e entregue por
X"). Abrir PDP é barato porque a chave de resolução é o *seller ID*, não o
produto: 1.707 registros colapsam em algumas dezenas de IDs únicos, e cada ID
resolvido é persistido em ``data/leroy_sellers.json`` — a partir da segunda
coleta o custo de rede tende a zero.

Este módulo é puro (sem Playwright/rede): recebe o HTML do PDP e devolve o
nome. O scraper cuida do fetch. Isso mantém a extração testável offline.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from bs4 import BeautifulSoup
from loguru import logger

# Raiz do repositório — o cache vive em data/ para sobreviver entre execuções
# e poder ser versionado (o .gitignore só exclui artefatos de sessão).
_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_PATH = _REPO_ROOT / "data" / "leroy_sellers.json"

# Nomes normalizados que identificam o próprio sortimento 1P da Leroy.
LEROY_SELF_NAMES: frozenset[str] = frozenset({
    "leroymerlin", "leroy merlin", "leroy merlin brasil", "leroy_merlin",
    "leroy", "leroymerlin.com.br", "1",
})

# Chaves que carregam nome de seller dentro do __NEXT_DATA__ do PDP.
_NAME_KEYS = (
    "sellerName", "seller_name", "storeName", "store_name",
    "fantasyName", "tradeName", "name", "nome", "displayName",
)
# Chaves que carregam o ID do seller.
_ID_KEYS = ("sellerId", "seller_id", "id", "_id", "objectID", "sellerObjectId")

# Rótulos do PDP que antecedem o nome do lojista.
_PDP_LABEL_RE = re.compile(
    r"(?:vendido\s+e\s+entregue\s+por|vendido\s+por|entregue\s+por|"
    r"vendido\s+e\s+entregue\s*:|loja\s+parceira\s*:?)\s*"
    r"([^\n\r|•·]{2,60})",
    re.IGNORECASE,
)

# Fallback direto sobre o HTML bruto, para o caso do nome estar em JSON
# embutido que o parser de __NEXT_DATA__ não alcançou.
_RAW_JSON_NAME_RE = re.compile(
    r'"(?:sellerName|storeName|fantasyName|tradeName)"\s*:\s*"([^"]{2,60})"'
)

# Chunks do App Router: self.__next_f.push([1,"…json escapado…"])
_FLIGHT_PUSH_RE = re.compile(
    r'self\.__next_f\.push\(\[\d+\s*,\s*("(?:[^"\\]|\\.)*")\s*\]\)'
)

# Chaves *específicas de seller* para a busca ancorada no payload do App Router.
# A chave genérica "name" fica de fora de propósito: numa janela ao redor do
# seller ID cabe também o nome do produto (`{"name":"Ar Condicionado Portátil
# Philco", …, "sellerName":"Loja X"}`), e o título do produto seria devolvido
# como lojista — e persistido no cache, corrompendo o seller para sempre.
_SELLER_KEY_NAME_RE = re.compile(
    r'"(?:sellerName|seller_name|storeName|store_name|fantasyName|tradeName)"'
    r'\s*:\s*"([^"]{2,60})"'
)

# Único caso em que "name" é aceito: aninhado sob a chave "seller", onde a
# intenção é inequívoca — ex. {"seller":{"id":"…","name":"Loja X"}}.
_SELLER_NESTED_NAME_RE = re.compile(
    r'"seller"\s*:\s*\{[^{}]{0,300}?"name"\s*:\s*"([^"]{2,60})"'
)

# Palavras que denunciam que o texto capturado não é um nome de loja
# (marketing/atributo de produto colado no rótulo).
_NAME_BLOCKLIST_RE = re.compile(
    r"\b(btus?|volts?|220v|127v|inverter|split|frete|garantia|parcel|"
    r"desconto|pix|cart[aã]o|r\$)\b",
    re.IGNORECASE,
)

_MAX_NAME_LEN = 60


# ----------------------------------------------------------------------
# Normalização / validação de nome
# ----------------------------------------------------------------------

def clean_seller_name(raw: Any) -> Optional[str]:
    """
    Normaliza e valida um candidato a nome de seller.

    Args:
        raw: valor bruto extraído do PDP (qualquer tipo).

    Returns:
        Nome limpo, ou None se o candidato for inválido (vazio, longo demais,
        numérico, ObjectId ou contaminado por texto de produto/marketing).

    Example:
        >>> clean_seller_name("  Refri  Center Ltda\\n")
        'Refri Center Ltda'
        >>> clean_seller_name("Split 12000 BTUs") is None
        True
    """
    if not raw or not isinstance(raw, str):
        return None

    name = re.sub(r"\s+", " ", raw).strip(" \t\n\r-–—:;,.|")
    if not name or len(name) > _MAX_NAME_LEN or len(name) < 2:
        return None
    # ObjectId de 24 hex ou string puramente numérica não é nome.
    if re.fullmatch(r"[0-9a-f]{24}", name, re.IGNORECASE) or name.isdigit():
        return None
    if _NAME_BLOCKLIST_RE.search(name):
        return None
    return name


def is_leroy_self(name: Optional[str]) -> bool:
    """Indica se o nome corresponde ao sortimento próprio (1P) da Leroy."""
    return bool(name) and name.strip().lower() in LEROY_SELF_NAMES


# ----------------------------------------------------------------------
# Extração a partir do HTML do PDP
# ----------------------------------------------------------------------

def _walk(node: Any, depth: int = 0, max_depth: int = 14):
    """Percorre recursivamente dicts/lists de um JSON, emitindo os dicts."""
    if depth > max_depth:
        return
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value, depth + 1, max_depth)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, depth + 1, max_depth)


def _next_data_payloads(html: str) -> list:
    """Extrai os blobs JSON embutidos no PDP (__NEXT_DATA__ e JSON-LD)."""
    payloads = []
    soup = BeautifulSoup(html, "html.parser")

    for script in soup.find_all("script"):
        script_id = (script.get("id") or "").strip()
        script_type = (script.get("type") or "").strip().lower()
        is_next = script_id == "__NEXT_DATA__"
        is_ldjson = script_type == "application/ld+json"
        if not (is_next or is_ldjson):
            continue
        text = script.string or script.get_text() or ""
        text = text.strip()
        if not text:
            continue
        try:
            payloads.append(json.loads(text))
        except (json.JSONDecodeError, ValueError):
            continue

    return payloads


def next_flight_text(html: str) -> str:
    """
    Reconstrói o payload de streaming do Next.js App Router.

    PDPs em App Router (React Server Components) **não têm** ``__NEXT_DATA__``:
    os dados chegam em chunks ``self.__next_f.push([1,"…json escapado…"])``.
    Esta função decodifica e concatena esses chunks num único texto, onde os
    campos de seller podem então ser procurados.

    Args:
        html: HTML completo do PDP.

    Returns:
        Texto concatenado dos chunks, ou string vazia se não houver nenhum.
    """
    chunks: list[str] = []
    for match in _FLIGHT_PUSH_RE.finditer(html):
        try:
            chunks.append(json.loads(match.group(1)))
        except (json.JSONDecodeError, ValueError):
            continue
    return "".join(chunks)


def _enclosing_object(text: str, idx: int, span: int = 2000) -> str:
    """
    Recorta o objeto JSON mais interno que contém a posição ``idx``.

    Serve para amarrar o nome ao seller certo: num array de ofertas, o nome do
    lojista anterior fica *fisicamente mais perto* do ID do que o nome que de
    fato lhe pertence, então janela por distância escolhe errado. Dentro do
    mesmo objeto não há essa ambiguidade.

    Args:
        text: payload completo.
        idx: posição de referência (onde o seller ID foi encontrado).
        span: limite de varredura para cada lado, em caracteres.

    Returns:
        Substring do objeto delimitado. Cai para uma janela simples quando as
        chaves não fecham dentro do limite.
    """
    inicio = max(0, idx - span)
    depth = 0
    start = None
    for i in range(idx, inicio - 1, -1):
        ch = text[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth == 0:
                start = i
                break
            depth -= 1

    fim = min(len(text), idx + span)
    depth = 0
    end = None
    for i in range(idx, fim):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth == 0:
                end = i + 1
                break
            depth -= 1

    if start is None or end is None:
        return text[inicio:fim]
    return text[start:end]


def _names_in(fragment: str) -> list[str]:
    """Nomes de seller válidos num fragmento, em ordem de aparição."""
    nomes: list[str] = []
    for regex in (_SELLER_KEY_NAME_RE, _SELLER_NESTED_NAME_RE):
        for match in regex.finditer(fragment):
            name = clean_seller_name(match.group(1))
            if name and not is_leroy_self(name):
                nomes.append(name)
    return nomes


def _from_flight_text(text: str, seller_id: Optional[str]) -> Optional[str]:
    """
    Procura o nome do seller no payload do App Router.

    Duas passadas, como no ``__NEXT_DATA__``: primeiro ancorada no ``seller_id``
    (janela de texto ao redor da ocorrência do ID), depois genérica por chave
    explícita de seller.

    Args:
        text: payload concatenado devolvido por `next_flight_text`.
        seller_id: ObjectId usado como âncora.

    Returns:
        Nome do seller, ou None.
    """
    if not text:
        return None

    # Passada 1 — objeto JSON que contém o ID. Só chaves específicas de seller:
    # a chave genérica "name" traria o título do produto.
    if seller_id:
        idx = text.find(seller_id)
        while idx != -1:
            nomes = _names_in(_enclosing_object(text, idx))
            if len(nomes) == 1:
                return nomes[0]
            if nomes:
                # Mais de um nome no mesmo objeto (ex. objeto-pai englobando
                # várias ofertas): ambíguo demais para arriscar o cache.
                logger.debug(
                    f"[LeroySellers] {len(nomes)} nomes no objeto do seller "
                    f"{seller_id[:8]}… — ambíguo, ignorando a âncora"
                )
                break
            idx = text.find(seller_id, idx + 1)

    # Passada 2 — chave explícita de seller em qualquer lugar do payload.
    for match in _RAW_JSON_NAME_RE.finditer(text):
        name = clean_seller_name(match.group(1))
        if name and not is_leroy_self(name):
            return name

    # Passada 3 — rótulo textual dentro do payload.
    match = _PDP_LABEL_RE.search(text)
    if match:
        return clean_seller_name(match.group(1))

    return None


def _from_json_payloads(payloads: list, seller_id: Optional[str]) -> Optional[str]:
    """
    Procura o nome do seller nos JSONs do PDP, em duas passadas:

    1. **Ancorada no ID** — dict que contenha ``seller_id`` em alguma chave de
       ID e um nome junto. Máxima confiança.
    2. **Genérica** — qualquer dict com chave de nome de seller explícita
       (``sellerName``/``storeName``/…), descartando a entrada da própria Leroy.
    """
    generic: Optional[str] = None

    for payload in payloads:
        for node in _walk(payload):
            # JSON-LD: offers.seller = {"@type": "Organization", "name": "X"}.
            # O nome está sob a chave genérica "name", mas o *pai* é "seller",
            # então a intenção é inequívoca.
            nested = node.get("seller")
            if isinstance(nested, dict) and generic is None:
                nested_name = clean_seller_name(
                    nested.get("name") or nested.get("sellerName")
                )
                if nested_name and not is_leroy_self(nested_name):
                    generic = nested_name

            names = [
                clean_seller_name(node.get(key))
                for key in _NAME_KEYS
                if key in node
            ]
            names = [n for n in names if n]
            if not names:
                continue

            # Passada 1 — ancorada no seller ID
            if seller_id:
                ids = {str(node.get(key)) for key in _ID_KEYS if node.get(key)}
                if seller_id in ids:
                    return names[0]

            # Passada 2 — candidato genérico (guardado, não retornado ainda)
            if generic is None:
                explicit = any(
                    key in node
                    for key in ("sellerName", "seller_name", "storeName", "store_name")
                )
                if explicit and not is_leroy_self(names[0]):
                    generic = names[0]

    return generic


def _from_text(html: str) -> Optional[str]:
    """Extrai o nome pelo rótulo textual do PDP ("Vendido e entregue por X")."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)

    match = _PDP_LABEL_RE.search(text)
    if match:
        name = clean_seller_name(match.group(1))
        if name:
            return name
    return None


def extract_seller_from_pdp(html: str, seller_id: Optional[str] = None) -> Optional[str]:
    """
    Extrai o nome do seller a partir do HTML de um PDP da Leroy Merlin.

    Ordem de tentativas (da mais confiável para a mais frágil):
      1. JSON embutido (``__NEXT_DATA__`` / JSON-LD) ancorado no ``seller_id``
      2. JSON embutido com chave explícita de seller (sem âncora de ID)
      3. Payload de streaming do App Router (``self.__next_f.push``), ancorado
         no ``seller_id`` por proximidade — PDPs em App Router não têm
         ``__NEXT_DATA__``, então sem isto a camada 1-2 não vê nada
      4. Rótulo textual do PDP — "Vendido e entregue por X" (exige HTML já
         hidratado; o shell servido no `domcontentloaded` ainda não tem)
      5. Regex sobre o HTML bruto — ``"sellerName": "X"``

    Args:
        html: HTML completo do PDP.
        seller_id: ObjectId vindo de ``marketplaceSellers``, usado como âncora.

    Returns:
        Nome do seller, ou None se nenhuma estratégia encontrou algo válido.

    Note:
        Produto 1P devolve "Leroy Merlin" (ou variação) — o caller decide se
        isso é aceitável para o ID consultado.
    """
    if not html:
        return None

    try:
        payloads = _next_data_payloads(html)
    except Exception as exc:  # HTML corrompido/truncado não deve derrubar a coleta
        logger.debug(f"[LeroySellers] Falha ao ler JSON do PDP: {exc}")
        payloads = []

    name = _from_json_payloads(payloads, seller_id)
    if name:
        return name

    try:
        name = _from_flight_text(next_flight_text(html), seller_id)
    except Exception as exc:
        logger.debug(f"[LeroySellers] Falha ao ler payload do App Router: {exc}")
        name = None
    if name:
        return name

    try:
        name = _from_text(html)
    except Exception as exc:
        logger.debug(f"[LeroySellers] Falha ao ler texto do PDP: {exc}")
        name = None
    if name:
        return name

    match = _RAW_JSON_NAME_RE.search(html)
    if match:
        candidate = clean_seller_name(match.group(1))
        if candidate and not is_leroy_self(candidate):
            return candidate

    return None


# ----------------------------------------------------------------------
# Cache persistente
# ----------------------------------------------------------------------

class LeroySellerCache:
    """
    Cache persistente de ``seller_id → nome`` da Leroy Merlin.

    Grava em ``data/leroy_sellers.json`` para que IDs resolvidos numa coleta
    sirvam a todas as seguintes (inclusive na VM Oracle, quando o arquivo é
    versionado). IDs que falharam ficam em quarentena por ``retry_days`` para
    não gastar um PDP por execução com o mesmo ID morto.

    Attributes:
        path: caminho do arquivo JSON.
        retry_days: dias de quarentena antes de tentar um ID falhado de novo.
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
            logger.warning(f"[LeroySellers] Cache ilegível ({self.path}): {exc}")
            return

        # JSON válido mas não-objeto (lista, número, string) faria `.get`
        # estourar AttributeError e impediria o scraper de nem iniciar.
        if not isinstance(data, dict):
            logger.warning(
                f"[LeroySellers] Cache com raiz inesperada "
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
            f"[LeroySellers] Cache carregado: {len(self._sellers)} sellers, "
            f"{len(self._unresolved)} IDs em quarentena"
        )

    def save(self) -> bool:
        """Grava o cache em disco. Retorna True se escreveu algo."""
        if not self._dirty:
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "_comment": (
                    "seller_id (ObjectId do Algolia) → nome do lojista, resolvido "
                    "via PDP da Leroy Merlin. Gerado por scrapers/leroy_merlin.py; "
                    "editável à mão e seguro para versionar."
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
            logger.debug(
                f"[LeroySellers] Cache salvo: {len(self._sellers)} sellers → {self.path}"
            )
            return True
        except OSError as exc:
            logger.warning(f"[LeroySellers] Não foi possível salvar o cache: {exc}")
            return False

    # -- API ---------------------------------------------------------

    def get(self, seller_id: str) -> Optional[str]:
        """Nome já conhecido para o ID, ou None."""
        return self._sellers.get(seller_id)

    def put(self, seller_id: str, name: str) -> None:
        """Registra um ID resolvido (e o tira da quarentena)."""
        clean = clean_seller_name(name)
        if not seller_id or not clean:
            return
        if self._sellers.get(seller_id) == clean:
            return
        self._sellers[seller_id] = clean
        self._unresolved.pop(seller_id, None)
        self._dirty = True

    def mark_failed(self, seller_id: str, sample_url: Optional[str] = None) -> None:
        """Coloca o ID em quarentena após uma tentativa de PDP sem sucesso."""
        if not seller_id:
            return
        entry = self._unresolved.get(seller_id) or {"attempts": 0}
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        entry["last_try"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if sample_url:
            entry["sample_url"] = sample_url
        self._unresolved[seller_id] = entry
        self._dirty = True

    def should_retry(self, seller_id: str) -> bool:
        """
        Indica se vale gastar um PDP com este ID agora.

        False quando o ID já falhou dentro da janela de quarentena
        (``retry_days``) — evita repetir a mesma busca infrutífera a cada run.
        """
        entry = self._unresolved.get(seller_id)
        if not entry:
            return True
        last_try = entry.get("last_try")
        if not last_try:
            return True
        try:
            when = datetime.fromisoformat(last_try)
        except ValueError:
            return True
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - when > timedelta(days=self.retry_days)

    def clear_quarantine(self) -> int:
        """
        Esvazia a quarentena, liberando todos os IDs para nova tentativa.

        Necessário depois de corrigir a extração: sem isso, IDs que falharam por
        um motivo já resolvido continuariam bloqueados até o fim da janela de
        ``retry_days``.

        Returns:
            Quantidade de IDs liberados.
        """
        n = len(self._unresolved)
        if n:
            self._unresolved = {}
            self._dirty = True
        return n

    @property
    def quarantined(self) -> Dict[str, Any]:
        """Cópia das entradas em quarentena (leitura)."""
        return dict(self._unresolved)

    def __len__(self) -> int:
        return len(self._sellers)

    @property
    def sellers(self) -> Dict[str, str]:
        """Cópia do mapa id → nome (leitura)."""
        return dict(self._sellers)
