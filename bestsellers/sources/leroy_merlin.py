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

A UI EXPÕE, SIM, a ordenação (Ago/2026)
--------------------------------------
Este módulo foi escrito sob a premissa de que "a UI da Leroy não expõe sort por
vendas" — daí a prova de ordenação ter ido morar num nome de índice Algolia. O
mantenedor confirmou em 27/08/2026 que a premissa é FALSA: a prateleira tem um
controle "Ordenar por" que imprime a ordenação vigente em texto. A leitura
dessa prova está em `bestsellers/sources/leroy_ordenacao.py`, testada contra o
markup real do controle. Migrar a COLETA para a página (e não só a prova)
depende do markup do grid de produtos, que ainda não foi levantado.

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
from bestsellers.sources.leroy_ordenacao import ordenacao_da_pagina, ordenado_por_vendas
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


def _url_indice(indice: str) -> str:
    """URL de query Algolia para um índice (réplica) dado."""
    return (
        f"https://{_ALGOLIA_APP_ID}-dsn.algolia.net"
        f"/1/indexes/{indice}/query"
        f"?x-algolia-application-id={_ALGOLIA_APP_ID}"
        f"&x-algolia-api-key={_ALGOLIA_API_KEY}"
    )


# Índice PADRÃO (relevância). Nunca serve de série de mais vendidos — ver o
# aviso em `bestsellers/config.py` (regressão de 26/08/2026).
_INDICE_RELEVANCIA = "production_products"

# Nomes de réplica Algolia citados pela página (URL/corpo das queries do JS).
_RE_INDICE = re.compile(r"production_products[A-Za-z0-9_]*")

# Marcador do endpoint quando a réplica foi DESCOBERTA na página com a
# ordenação "Mais vendidos" confirmada no controle da tela (Nov/2026). Está em
# `parametros_ordenacao` da fonte.
MARCADOR_ORDENACAO_UI = "ordenacao_ui=mais_vendidos"

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
        # Réplica de vendas em uso. Começa na conhecida; troca só pela
        # descoberta na página com "Mais vendidos" confirmado na tela.
        self._indice: str = _INDICE_MAIS_VENDIDOS

    def _abrir(self) -> None:
        self._leroy = LeroyMerlinScraper(headless=self.headless)

    def _fechar(self) -> None:
        # Sem context manager não há `_close()`: a aba de PDP que a resolução de
        # seller abre no Chrome local (RAC_LOCAL_CHROME=1) é fechada aqui.
        if self._leroy is not None:
            self._leroy.fechar_pdp_local()
        self._leroy = None

    def _coletar(self, paginas: int) -> List[BestSellerItem]:
        self.registrar_endpoint(_url_indice(self._indice))

        alvo = self.spec.itens_esperados
        minimo_paginas = max(1, paginas)

        # Passo 0 — sondagem: a primeira página (busca por texto) serve para
        # DESCOBRIR o atributo de facet de categoria a partir dos hits reais, e
        # ainda vale como semente do fallback se a âncora não pegar. Uma chamada
        # Algolia a mais, barata.
        sonda = self._consultar(0, facet=None)
        if not sonda:
            # 07/10–10/11/2026: a réplica `production_products_most_sales`
            # passou a responder 200 com ZERO hits (sem 404) enquanto a coleta
            # de oferta seguia lendo `production_products` normalmente. A Leroy
            # trocou a réplica de vendas; a página sabe qual é.
            logger.warning(
                f"[{self.nome}] Réplica '{self._indice}' devolveu 0 hits — "
                "procurando a réplica de 'Mais vendidos' na própria página."
            )
            novo = self._descobrir_indice_pela_pagina()
            if not novo:
                raise RuntimeError(
                    f"réplica Algolia '{self._indice}' devolveu 0 hits e a "
                    "réplica de 'Mais vendidos' não foi descoberta na página "
                    f"({_CATEGORIA_URL}). Rode com RAC_LOCAL_CHROME=1 (a "
                    "descoberta usa o Chrome local) e veja logs/leroy_maisvendidos_*.html"
                )
            self._indice = novo
            self.endpoint = f"{_url_indice(novo)} [{MARCADOR_ORDENACAO_UI}]"
            sonda = self._consultar(0, facet=None)
            if not sonda:
                raise RuntimeError(
                    f"réplica descoberta '{novo}' também devolveu 0 hits para "
                    f"'{_TERMO}'."
                )
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
                _url_indice(self._indice),
                headers=_ALGOLIA_HEADERS,
                json=payload,
                timeout=_TIMEOUT,
            )
        except Exception as exc:
            raise RuntimeError(f"Algolia inacessível: {exc}") from exc

        if resposta.status_code == 404:
            # Índice renomeado é a falha silenciosa clássica desta fonte: a
            # coleta continuaria "funcionando" com a ordenação errada se
            # caíssemos no índice padrão. Tipo próprio: é a única falha que a
            # coleta ancorada NÃO degrada para texto (o texto usa o mesmo índice).
            raise _IndiceInexistente(
                f"índice '{self._indice}' não existe mais (HTTP 404). "
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
    # Descoberta da réplica de vendas pela página (Nov/2026)
    # ------------------------------------------------------------------

    def _descobrir_indice_pela_pagina(self) -> Optional[str]:
        """
        Abre a prateleira, escolhe "Ordenar por: Mais vendidos" e lê qual
        réplica Algolia a própria página consulta.

        A prova de ordenação vem da TELA (`ordenado_por_vendas`, o controle
        Radix "Ordenar por"); o nome da réplica vem das requisições que o JS
        da página faz à Algolia DEPOIS da escolha. Sem a ordenação confirmada
        na tela, nada é devolvido — e `production_products` (relevância) nunca
        é aceita.

        Returns:
            Nome da réplica de vendas, ou None.
        """
        if self._leroy is None:
            return None
        try:
            page = self._leroy._pagina_pdp()
        except Exception as exc:
            logger.warning(f"[{self.nome}] Sem aba de browser para a descoberta: {exc}")
            return None
        if page is None:
            return None

        vistos: List[str] = []

        def _na_requisicao(req) -> None:
            try:
                if "algolia" not in (req.url or ""):
                    return
                vistos.extend(_RE_INDICE.findall(f"{req.url} {req.post_data or ''}"))
            except Exception:
                pass

        page.on("request", _na_requisicao)
        try:
            try:
                page.goto(_CATEGORIA_URL, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception as exc:
                logger.debug(f"[{self.nome}] prateleira: {exc}")

            if not ordenado_por_vendas(self._html(page)):
                antes = len(vistos)
                if not self._escolher_mais_vendidos(page):
                    self._dump_leroy("sem_controle", self._html(page))
                    logger.warning(
                        f"[{self.nome}] Não achei o 'Ordenar por' → 'Mais "
                        "vendidos' na prateleira."
                    )
                    return None
                depois = vistos[antes:]
            else:
                depois = list(vistos)

            html = self._html(page)
            lida = ordenacao_da_pagina(html)
            if not ordenado_por_vendas(html):
                self._dump_leroy("sem_ordenacao", html)
                logger.warning(
                    f"[{self.nome}] A tela não confirma 'Mais vendidos' "
                    f"(lido: {lida!r}) — réplica não descoberta."
                )
                return None

            # Também vale o nome citado no HTML (config do InstantSearch).
            candidatos = depois + _RE_INDICE.findall(html)
            escolhido = self._escolher_indice(candidatos)
            if escolhido:
                logger.success(
                    f"[{self.nome}] Réplica de 'Mais vendidos' descoberta na "
                    f"página: '{escolhido}' (tela: {lida!r})."
                )
            else:
                self._dump_leroy("sem_indice", html)
                logger.warning(
                    f"[{self.nome}] Tela em 'Mais vendidos', mas nenhuma réplica "
                    f"diferente da de relevância nas queries ({sorted(set(candidatos))})."
                )
            return escolhido
        finally:
            try:
                page.remove_listener("request", _na_requisicao)
            except Exception:
                pass

    @staticmethod
    def _escolher_indice(candidatos: List[str]) -> Optional[str]:
        """
        A réplica de vendas entre os nomes vistos.

        Descarta a de relevância e prefere nome com semântica de venda; entre
        empates, a MAIS RECENTE (a última query depois da escolha na tela).
        """
        validos = [c for c in candidatos if c and c != _INDICE_RELEVANCIA]
        if not validos:
            return None
        de_venda = [c for c in validos if re.search(r"sale|vend|sold|best|popular|top", c, re.I)]
        return (de_venda or validos)[-1]

    def _escolher_mais_vendidos(self, page: Any) -> bool:
        """Abre o combobox "Ordenar por" e clica em "Mais vendidos"."""
        rotulo = re.compile(r"mais\s+vendid", re.I)
        gatilhos = (
            page.locator("[role='combobox']").filter(
                has_text=re.compile(r"relev|vendid|pre[cç]o|recentes|avalia|desconto", re.I)
            ),
            page.locator("[role='combobox']"),
        )
        for gatilho in gatilhos:
            try:
                n = min(gatilho.count(), 4)
            except Exception:
                continue
            for i in range(n):
                try:
                    gatilho.nth(i).click(timeout=5_000)
                    try:
                        vistas = page.get_by_role("option").all_inner_texts()
                        logger.info(
                            f"[{self.nome}] 'Ordenar por' aberto — opções: {vistas}"
                        )
                    except Exception:
                        pass
                    opcao = page.get_by_role("option", name=rotulo).first
                    opcao.click(timeout=5_000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=15_000)
                    except Exception:
                        pass
                    page.wait_for_timeout(2_000)
                    if ordenado_por_vendas(self._html(page)):
                        return True
                except Exception:
                    try:
                        page.keyboard.press("Escape")
                    except Exception:
                        pass
        return False

    @staticmethod
    def _html(page: Any) -> str:
        try:
            return page.content() or ""
        except Exception:
            return ""

    @staticmethod
    def _dump_leroy(rotulo: str, html: str) -> None:
        """Evidência para a próxima conferência (logs/leroy_maisvendidos_*.html)."""
        if not html:
            return
        try:
            from pathlib import Path
            import time as _time

            pasta = Path("logs")
            pasta.mkdir(parents=True, exist_ok=True)
            caminho = pasta / f"leroy_maisvendidos_{rotulo}_{_time.strftime('%Y%m%d_%H%M%S')}.html"
            caminho.write_text(html[:2_000_000], encoding="utf-8")
            logger.info(f"[Leroy Merlin] HTML salvo em {caminho}")
        except Exception:
            pass

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


class LeroyMerlinRelevancia(LeroyMerlinBestSellers):
    """
    Prateleira Split Inverter da Leroy na ordem "Mais relevantes" (Nov/2026).

    Mesma coleta (Algolia + âncora de categoria + sellers), outro índice: a
    réplica PADRÃO `production_products`. Referência RELEVANCIA — série
    própria (`leroymerlin_relevancia`), exibida como "Leroy Merlin*".
    """

    key = "leroymerlin_relevancia"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._indice = _INDICE_RELEVANCIA

    def _descobrir_indice_pela_pagina(self) -> Optional[str]:
        # Relevância é o índice padrão: não há réplica a descobrir.
        return None
