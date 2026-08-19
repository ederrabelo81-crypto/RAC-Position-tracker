"""
bestsellers/sources/leroy_merlin.py — Lista "Mais Vendidos" da Leroy Merlin.

Índice Algolia: `production_products_most_sales` (o mesmo que a UI usa quando
o usuário escolhe "Mais vendidos"). A coleta bate direto no índice — sem
browser, sem Akamai no caminho.

⚠️ MECÂNICA SOB SUSPEITA. A Leroy declara a ordenação como mais vendidos, mas
o comportamento estatístico não confirma: em 48h, 41% dos itens não mudaram de
posição e o deslocamento mediano foi de 1 posição, contra 12–19% parados e 3–4
posições nas demais plataformas. Hipótese aberta: a ordenação pondera curadoria
1P, margem ou disponibilidade. Enquanto isso não for esclarecido com o
varejista, o número do Leroy NÃO sustenta sozinho decisão de corte de verba —
o relatório imprime esse aviso junto do resultado (`veredito`).

Seller: o índice devolve o lojista 3P como ObjectId opaco. A resolução
(mapa estático → cache em disco → PDP) é do `LeroyMerlinScraper`, reaproveitada
aqui — 1 PDP por seller novo, não por produto.
"""

from typing import Any, Dict, List, Optional

import requests
from loguru import logger

from bestsellers.base import BestSellerSource
from bestsellers.models import BestSellerItem, TIPO_FORA_ESCOPO, classificar_tipo
from scrapers.leroy_merlin import (
    _ALGOLIA_API_KEY,
    _ALGOLIA_APP_ID,
    _ALGOLIA_HEADERS,
    LeroyMerlinScraper,
)

_INDICE_MAIS_VENDIDOS = "production_products_most_sales"
_URL_ALGOLIA = (
    f"https://{_ALGOLIA_APP_ID}-dsn.algolia.net"
    f"/1/indexes/{_INDICE_MAIS_VENDIDOS}/query"
    f"?x-algolia-application-id={_ALGOLIA_APP_ID}"
    f"&x-algolia-api-key={_ALGOLIA_API_KEY}"
)
_TERMO = "ar condicionado"
_HITS_POR_PAGINA = 36
_TIMEOUT = 8
# Teto de páginas varridas. A busca por texto arrasta muito acessório; depois
# do recorte de escopo pode sobrar pouca coisa numa página só. Este teto deixa
# a coleta ir atrás de mais páginas para repor os itens filtrados sem virar um
# loop infinito (8 × 36 = até 288 hits varridos).
_MAX_PAGINAS = 8


class LeroyMerlinBestSellers(BestSellerSource):
    """Coletor do índice de mais vendidos da Leroy Merlin (Algolia)."""

    key = "leroymerlin"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        # Instanciado só pela lógica de seller/URL — não abre browser.
        self._leroy: Optional[LeroyMerlinScraper] = None

    def _abrir(self) -> None:
        self._leroy = LeroyMerlinScraper(headless=self.headless)

    def _fechar(self) -> None:
        self._leroy = None

    def _coletar(self, paginas: int) -> List[BestSellerItem]:
        self.registrar_endpoint(_URL_ALGOLIA)
        itens: List[BestSellerItem] = []

        alvo = self.spec.itens_esperados
        minimo_paginas = max(1, paginas)

        # Vai além do mínimo de páginas SÓ para repor o que o recorte de escopo
        # tirou: a busca por texto traz acessório demais, e parar na página 1
        # deixaria a lista curta o suficiente para tropeçar no piso de itens da
        # validação. Para assim que juntar `itens_esperados` itens in-scope.
        for pagina in range(_MAX_PAGINAS):
            hits = self._consultar(pagina)
            if not hits:
                break
            itens.extend(self._parse(hits, offset=len(itens)))
            if len(hits) < _HITS_POR_PAGINA:
                break  # última página do índice
            if pagina + 1 >= minimo_paginas and len(itens) >= alvo:
                break
        return itens

    def _consultar(self, pagina: int) -> List[Dict[str, Any]]:
        """Uma página do índice de mais vendidos."""
        payload = {
            "query": _TERMO,
            "hitsPerPage": _HITS_POR_PAGINA,
            "page": pagina,  # Algolia é 0-indexed
        }
        try:
            resposta = requests.post(
                _URL_ALGOLIA, headers=_ALGOLIA_HEADERS, json=payload, timeout=_TIMEOUT
            )
        except Exception as exc:
            raise RuntimeError(f"Algolia inacessível: {exc}") from exc

        if resposta.status_code == 404:
            # Índice renomeado é a falha silenciosa clássica desta fonte: a
            # coleta continuaria "funcionando" com a ordenação errada se
            # caíssemos no índice padrão.
            raise RuntimeError(
                f"índice '{_INDICE_MAIS_VENDIDOS}' não existe mais (HTTP 404). "
                "A Leroy renomeou a ordenação — confira o parâmetro sortBy na "
                "UI antes de trocar a constante."
            )
        resposta.raise_for_status()
        dados = resposta.json()

        hits = dados.get("hits") or []
        if hits:
            logger.info(
                f"[{self.nome}] Algolia mais-vendidos: {len(hits)} hits "
                f"(página {pagina + 1}/{dados.get('nbPages', '?')})"
            )
        return hits

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _titulo(hit: Dict[str, Any]) -> Optional[str]:
        """
        Título do hit, normalizado — ou None quando não há título de verdade.

        A normalização não é cosmética: `"   "` e `"\\xa0"` são TRUTHY em
        Python, então um título só de espaço passaria pelo filtro de descarte,
        gastaria orçamento de PDP com um produto que não entra na série e
        ainda gravaria uma linha sem título nela. O espaço inquebrável vem do
        HTML e sobrevive ao `strip()` padrão em algumas origens, por isso é
        trocado antes.
        """
        for chave in ("name", "shortName", "title", "productName"):
            valor = hit.get(chave)
            if not isinstance(valor, str):
                continue
            valor = valor.replace("\xa0", " ").strip()
            if valor:
                return valor
        return None

    def _parse(self, hits: List[Dict[str, Any]], offset: int) -> List[BestSellerItem]:
        # Hits sem título são descartados ANTES da classificação: o orçamento
        # de PDP é por execução, e gastá-lo com um produto que nem vai entrar
        # na série deixaria produtos válidos mais adiante sem seller.
        aproveitaveis = [(hit, self._titulo(hit)) for hit in hits]
        aproveitaveis = [(hit, titulo) for hit, titulo in aproveitaveis if titulo]

        # Recorte de escopo. Diferente das outras plataformas — que coletam de
        # uma PÁGINA DE CATEGORIA já recortada pelo varejista — o Leroy só expõe
        # o ranking por vendas como uma BUSCA POR TEXTO ("ar condicionado") no
        # índice Algolia. Sem recorte, o acessório barato de alto giro (suporte,
        # capa, controle) e o correlato (ventilador, climatizador) empurram o
        # primeiro split de verdade para o fim da lista — a Midea foi observada
        # em #33/36. Descartar aqui, com o MESMO classificador do resto do
        # pipeline, corrige o ranking e ainda poupa PDP de seller de acessório.
        antes = len(aproveitaveis)
        aproveitaveis = [
            (hit, titulo)
            for hit, titulo in aproveitaveis
            if classificar_tipo(titulo) != TIPO_FORA_ESCOPO
        ]
        fora = antes - len(aproveitaveis)
        if fora:
            logger.debug(
                f"[{self.nome}] {fora} hit(s) fora do escopo RAC descartados "
                "(acessório/correlato) antes de rankear."
            )

        # Passada 1: classifica sem tocar na rede e junta os IDs pendentes.
        classificados = [
            (hit, titulo, self._leroy._classify_hit_seller(hit))
            for hit, titulo in aproveitaveis
        ]
        self._resolver_pendentes([(hit, info) for hit, _, info in classificados])

        itens: List[BestSellerItem] = []
        for hit, titulo, info in classificados:
            itens.append(BestSellerItem(
                rank=offset + len(itens) + 1,
                titulo=titulo,
                preco=self._preco(hit),
                rating=self._numero(hit, ("rating", "ratingAverage", "averageRating")),
                reviews=self._inteiro(
                    hit, ("reviewCount", "totalReviews", "numberOfReviews", "reviewsCount")
                ),
                seller=info.get("seller"),
                sku_plataforma=str(hit.get("objectID") or hit.get("id") or "") or None,
                url_produto=self._leroy._extract_algolia_url(hit),
            ))
        return itens

    def _resolver_pendentes(self, classificados) -> None:
        """
        Resolve os sellers 3P que o cache não conhece, abrindo 1 PDP por ID.

        O índice devolve o lojista 3P como ObjectId opaco. Sem esta passada, o
        seller de todo parceiro novo entraria vazio na série — e o cache
        persistente nunca aprenderia o nome, repetindo a lacuna todo dia.
        O custo é por SELLER novo, não por produto: dezenas de hits colapsam
        em poucos IDs.
        """
        pendentes: Dict[str, str] = {}
        for hit, info in classificados:
            sid = info.get("seller_id")
            if info.get("seller") is None and sid and sid not in pendentes:
                url = self._leroy._extract_algolia_url(hit)
                if url:
                    pendentes[sid] = url
        if not pendentes:
            return

        logger.debug(f"[{self.nome}] {len(pendentes)} seller(s) 3P a resolver via PDP")
        try:
            resolvidos = self._leroy._resolve_pending_sellers(pendentes)
        except Exception as exc:
            logger.warning(f"[{self.nome}] Resolução de sellers falhou: {exc}")
            return

        for _, info in classificados:
            sid = info.get("seller_id")
            if info.get("seller") is None and sid in resolvidos:
                info["seller"] = resolvidos[sid]

    @staticmethod
    def _preco(hit: Dict[str, Any]) -> Optional[float]:
        """
        Preço praticado.

        `averagePromotionalPrice`/`medianPromotionalPrice` são os campos que o
        índice realmente devolve (confirmado em produção); os demais ficam como
        rede de segurança para variações do índice.
        """
        bruto = (
            hit.get("averagePromotionalPrice")
            or hit.get("medianPromotionalPrice")
            or hit.get("price")
            or hit.get("sellingPrice")
            or hit.get("bestPrice")
        )
        if isinstance(bruto, dict):
            bruto = bruto.get("value") or bruto.get("amount")
        try:
            valor = float(str(bruto).replace(",", ".")) if bruto else None
        except (TypeError, ValueError):
            return None
        return valor if valor and valor > 0 else None

    @staticmethod
    def _numero(hit: Dict[str, Any], chaves) -> Optional[float]:
        for chave in chaves:
            valor = hit.get(chave)
            if isinstance(valor, (int, float)) and 0 < float(valor) <= 5:
                return round(float(valor), 2)
        return None

    @staticmethod
    def _inteiro(hit: Dict[str, Any], chaves) -> Optional[int]:
        for chave in chaves:
            valor = hit.get(chave)
            if isinstance(valor, bool):
                continue
            if isinstance(valor, int) and valor >= 0:
                return valor
        return None
