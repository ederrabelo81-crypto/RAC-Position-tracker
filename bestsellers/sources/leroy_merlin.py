"""
bestsellers/sources/leroy_merlin.py — Lista "Mais Vendidos" da Leroy Merlin.

Índice Algolia: `production_products_most_sales` (a mesma ordenação por vendas
que a UI aplica). A coleta bate direto no índice — sem browser, sem Akamai no
caminho.

Âncora de categoria (Ago/2026)
------------------------------
A lista deixou de ser uma BUSCA POR TEXTO ("ar condicionado") e passou a ser
ancorada na PRATELEIRA DE CATEGORIA que o cliente de fato navega —
Split Inverter (`_CATEGORIA_URL`). Motivo: a busca por texto arrastava
acessório e correlato (a Midea era observada em #33/36) e, pior, rankeava uma
população — "tudo que casa com 'ar condicionado'" — que ninguém acessa numa
tela; não era auditável. Agora a consulta usa `facetFilters` sobre o índice de
vendas, recortando à categoria Split Inverter. A ordenação continua sendo por
VENDAS (regra dura preservada); o que muda é QUEM disputa o topo do ranking:
só o que está na prateleira Split Inverter, o mesmo recorte da URL pública.

O nome do atributo de facet não é hardcoded: ele é DESCOBERTO em runtime a
partir dos próprios hits (`_descobrir_facet`), porque a Leroy renomeia
atributos de índice sem aviso e um nome fixo viraria falha silenciosa. Se a
descoberta não achar um atributo de categoria compatível, a coleta CAI para o
caminho antigo (busca por texto + recorte pelo classificador de tipo) e AVISA
— degradar é melhor que gravar lixo ou zerar a lista.

⚠️ MECÂNICA AINDA SOB SUSPEITA. Mesmo ancorada na categoria, a Leroy declara a
ordenação como mais vendidos e o comportamento estatístico não confirma: em
48h, 41% dos itens não mudaram de posição (contra 12–19% nas demais). Hipótese
aberta: a ordenação pondera curadoria 1P, margem ou disponibilidade — o que
também explica a prateleira Midea desproporcional. Enquanto isso não for
esclarecido com o varejista, o número do Leroy NÃO sustenta sozinho decisão de
corte de verba — o relatório imprime esse aviso junto do resultado (`veredito`).

Seller: o índice devolve o lojista 3P como ObjectId opaco. A resolução
(mapa estático → cache em disco → PDP) é do `LeroyMerlinScraper`, reaproveitada
aqui — 1 PDP por seller novo, não por produto.
"""

import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests
from loguru import logger

from bestsellers.base import BestSellerSource
from bestsellers.config import LEROY_CATEGORIA_URL
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
# Teto de páginas varridas. Mesmo ancorada na categoria pode sobrar pouca coisa
# numa página só depois do recorte de escopo (rede de segurança), então a
# coleta pode ir atrás de mais páginas sem virar loop infinito (8 × 36 = até
# 288 hits varridos).
_MAX_PAGINAS = 8

# Prateleira de categoria NAVEGÁVEL que ancora o ranking — a mesma que o cliente
# abre e que o config grava como `url_publica`. Definida uma única vez em
# `bestsellers.config` (importada acima) para o link auditável e a referência da
# descoberta de facet não divergirem.
_CATEGORIA_URL = LEROY_CATEGORIA_URL
# Tokens que identificam a categoria dentro dos hits (do segmento `Split_Inverter`
# da URL). O par split+inverter é o discriminador: um atributo de categoria cujo
# valor contém AMBOS é a prateleira que queremos filtrar.
_CATEGORIA_TOKENS: Tuple[str, ...] = ("split", "inverter")
# Só atributos cujo NOME parece de categoria entram na descoberta de facet —
# sem isto o campo `name` ("Ar Condicionado Split Inverter Midea...") casaria os
# tokens e viraria um facet por título, retornando um único produto.
_FACET_KEY_RE = re.compile(r"categ|tipo|type|famil|hierarch|departa|classe|segment", re.I)
# Um rótulo de categoria é curto; uma descrição não. Corta campos de texto longo
# que porventura tenham nome parecido com categoria mas carreguem prosa.
_FACET_VALOR_MAX = 120


class _IndiceInexistente(RuntimeError):
    """
    O índice de mais vendidos não existe mais (HTTP 404).

    Tipo próprio para separar a falha ESTRUTURAL (índice renomeado — que quebra
    os dois caminhos, texto e categoria) das transitórias (timeout, conexão,
    HTTP 400 de facet não-facetável). Só esta sobe; as demais degradam para a
    busca por texto.
    """


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

        alvo = self.spec.itens_esperados
        minimo_paginas = max(1, paginas)

        # Passo 0 — sondagem: a primeira página (busca por texto) serve para
        # DESCOBRIR o atributo de facet de categoria a partir dos hits reais, e
        # ainda vale como semente do fallback se a âncora não pegar. Uma chamada
        # Algolia a mais, barata.
        sonda = self._consultar(0, facet=None)
        facet = self._descobrir_facet(sonda)

        if facet:
            # A descoberta acerta o NOME e o VALOR do atributo, mas não prova que
            # ele é FACETÁVEL no índice (`attributesForFaceting`). Se não for, a
            # query com `facetFilters` volta HTTP 400 — ou zero hits, quando o
            # valor cru não bate; e todo hit da categoria pode cair no recorte de
            # tipo, deixando a lista vazia. Em qualquer desses casos a coleta NÃO
            # pode abortar nem sair vazia: `if itens` (não `is not None`) manda o
            # resultado vazio para o texto também, reaproveitando a sondagem.
            itens = self._coletar_ancorado(facet, minimo_paginas, alvo)
            if itens:
                return itens
        else:
            logger.warning(
                f"[{self.nome}] Não foi possível ancorar na categoria Split "
                "Inverter (nenhum atributo de facet compatível nos hits) — "
                "caindo para busca por texto + recorte pelo classificador de "
                f"tipo. Prateleira de referência: {_CATEGORIA_URL}"
            )

        return self._coletar_texto(sonda, minimo_paginas, alvo)

    def _coletar_ancorado(
        self, facet: Tuple[str, str], minimo_paginas: int, alvo: int
    ) -> Optional[List[BestSellerItem]]:
        """
        Coleta ancorada na categoria via `facetFilters`, ou None se não pegar.

        Devolver None (em vez de levantar) é o contrato que permite ao chamador
        degradar para a busca por texto. TODA a coleta ancorada — página 0 E as
        páginas seguintes — roda sob o mesmo tratamento, porque uma falha
        transitória (timeout, conexão, HTTP 400 de atributo não-facetável) pode
        cair em qualquer página, não só na primeira; deixá-la escapar pelo
        `_paginar` derrubaria a fonte inteira. A única exceção é o índice
        inexistente (404, `_IndiceInexistente`): esse sobe, porque o caminho de
        texto usa o mesmo índice e falharia igual.
        """
        attr, valor = facet
        try:
            primeira = self._consultar(0, facet=facet)
            if not primeira:
                logger.warning(
                    f"[{self.nome}] Facet `{attr}` = \"{valor}\" não retornou "
                    "hits — o valor cru não bate a categoria; caindo para texto."
                )
                return None
            logger.info(
                f"[{self.nome}] Âncora de categoria aplicada: `{attr}` = "
                f"\"{valor}\" (prateleira Split Inverter) — ranking por vendas "
                "recortado à categoria navegável."
            )
            return self._paginar(
                lambda pagina: self._consultar(pagina, facet=facet),
                primeira,
                minimo_paginas,
                alvo,
            )
        except _IndiceInexistente:
            raise  # índice sumiu — o texto também não salva
        except Exception as exc:
            logger.warning(
                f"[{self.nome}] Coleta ancorada em `{attr}` falhou ({exc}) — "
                "atributo provavelmente fora de `attributesForFaceting` ou falha "
                "transitória; caindo para busca por texto + recorte de tipo."
            )
            return None

    def _coletar_texto(
        self, sonda: List[Dict[str, Any]], minimo_paginas: int, alvo: int
    ) -> List[BestSellerItem]:
        """
        Caminho histórico: busca por texto recortada pelo classificador de tipo.

        A página 0 já veio na sondagem (`sonda`) e é reaproveitada, evitando uma
        chamada Algolia duplicada.
        """
        return self._paginar(
            lambda pagina: self._consultar(pagina, facet=None),
            sonda,
            minimo_paginas,
            alvo,
        )

    def _paginar(
        self,
        buscar_pagina,
        primeira_pagina: Optional[List[Dict[str, Any]]],
        minimo_paginas: int,
        alvo: int,
    ) -> List[BestSellerItem]:
        """
        Percorre as páginas do índice, parseando e recortando o escopo.

        `primeira_pagina`, quando dado, são os hits da página 0 já baixados
        (sondagem ou primeira chamada ancorada) — evita refazer a chamada.

        Vai além do mínimo de páginas SÓ para repor o que o recorte de escopo
        tirou (rede de segurança do classificador de tipo, mantida mesmo com a
        âncora de categoria). Para assim que juntar `itens_esperados` itens
        in-scope. Aqui `paginas` é um PISO, não um teto — o custo extra (até
        `_MAX_PAGINAS` chamadas Algolia) é registrado no log para não ficar
        silencioso atrás do parâmetro de páginas.
        """
        itens: List[BestSellerItem] = []
        paginas_varridas = 0
        for pagina in range(_MAX_PAGINAS):
            if pagina == 0 and primeira_pagina is not None:
                hits = primeira_pagina
            else:
                hits = buscar_pagina(pagina)
            if not hits:
                break
            paginas_varridas += 1
            itens.extend(self._parse(hits, offset=len(itens)))
            if len(hits) < _HITS_POR_PAGINA:
                break  # última página do índice / da categoria
            if pagina + 1 >= minimo_paginas and len(itens) >= alvo:
                break

        if paginas_varridas > minimo_paginas:
            logger.info(
                f"[{self.nome}] {paginas_varridas} páginas Algolia varridas "
                f"(piso={minimo_paginas}) para repor o recorte de escopo — "
                f"{len(itens)}/{alvo} itens in-scope."
            )
        return itens

    def _consultar(
        self, pagina: int, facet: Optional[Tuple[str, str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Uma página do índice de mais vendidos.

        Com `facet` (attr, valor), a consulta é ancorada na categoria via
        `facetFilters` e a `query` vai vazia: o índice devolve a prateleira
        inteira na ordem de vendas. Sem `facet`, cai na busca por texto
        histórica (`_TERMO`), recortada depois pelo classificador de tipo.
        """
        payload: Dict[str, Any] = {
            "hitsPerPage": _HITS_POR_PAGINA,
            "page": pagina,  # Algolia é 0-indexed
        }
        if facet is not None:
            attr, valor = facet
            payload["query"] = ""
            payload["facetFilters"] = [[f"{attr}:{valor}"]]
        else:
            payload["query"] = _TERMO
        try:
            resposta = requests.post(
                _URL_ALGOLIA, headers=_ALGOLIA_HEADERS, json=payload, timeout=_TIMEOUT
            )
        except Exception as exc:
            raise RuntimeError(f"Algolia inacessível: {exc}") from exc

        if resposta.status_code == 404:
            # Índice renomeado é a falha silenciosa clássica desta fonte: a
            # coleta continuaria "funcionando" com a ordenação errada se
            # caíssemos no índice padrão. Tipo próprio: é a única falha que a
            # coleta ancorada NÃO degrada para texto (o texto usa o mesmo índice).
            raise _IndiceInexistente(
                f"índice '{_INDICE_MAIS_VENDIDOS}' não existe mais (HTTP 404). "
                "A Leroy renomeou a ordenação — confira o parâmetro sortBy na "
                "UI antes de trocar a constante."
            )
        resposta.raise_for_status()
        dados = resposta.json()

        hits = dados.get("hits") or []
        if hits:
            origem = "categoria" if facet is not None else "texto"
            logger.info(
                f"[{self.nome}] Algolia mais-vendidos ({origem}): {len(hits)} "
                f"hits (página {pagina + 1}/{dados.get('nbPages', '?')})"
            )
        return hits

    # ------------------------------------------------------------------
    # Descoberta de facet de categoria
    # ------------------------------------------------------------------

    @staticmethod
    def _valores_texto(valor: Any) -> Iterator[str]:
        """Achata um valor de hit nas strings candidatas a rótulo de categoria."""
        if isinstance(valor, str):
            yield valor
        elif isinstance(valor, (list, tuple)):
            for item in valor:
                if isinstance(item, str):
                    yield item
        elif isinstance(valor, dict):
            # hierarchicalCategories = {"lvl0": "...", "lvl1": "...", ...}
            for item in valor.values():
                if isinstance(item, str):
                    yield item

    @classmethod
    def _descobrir_facet(
        cls, hits: List[Dict[str, Any]]
    ) -> Optional[Tuple[str, str]]:
        """
        Descobre `(atributo, valor)` de facet que recorta os hits à categoria.

        Percorre os hits e retorna o primeiro atributo cujo NOME parece de
        categoria (`_FACET_KEY_RE`) e cujo VALOR contém todos os tokens da
        categoria-alvo (`_CATEGORIA_TOKENS`). O valor é devolvido verbatim para
        entrar cru no `facetFilters` — o Algolia casa o valor exato armazenado.

        Restringir pelo NOME do atributo é o que impede casar o título do
        produto (que também contém "split inverter") e transformar o facet num
        filtro de um produto só. Retorna None quando nada compatível aparece —
        o chamador então degrada para a busca por texto.
        """
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            for attr, valor in hit.items():
                if not _FACET_KEY_RE.search(attr):
                    continue
                for texto in cls._valores_texto(valor):
                    if len(texto) > _FACET_VALOR_MAX:
                        continue
                    baixo = texto.lower()
                    if all(tok in baixo for tok in _CATEGORIA_TOKENS):
                        return attr, texto
        return None

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
