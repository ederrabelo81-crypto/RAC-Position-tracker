"""
bestsellers/sources/shopee.py — Lista ordenada por vendas da Shopee Brasil.

URL: https://shopee.com.br/search?keyword=ar%20condicionado&sortBy=sales
API: /api/v4/search/search_items com `by=sales`

Mecânica: ordenação por vendas — e a ÚNICA fonte do conjunto que declara
unidades por mês por anúncio ("948 Vendido/Mês"). Esse campo é a única variável
de velocidade real disponível a custo zero, e é o motivo de a Shopee continuar
na rotina mesmo sendo a coleta mais instável.

ARMADILHA REGISTRADA (08/08/2026): sem `sortBy=sales` a lista é RELEVÂNCIA —
mistura de patrocinado e engajamento. Pela relevância a Midea aparecia em 17 de
55 posições, melhor rank #4; pela ordenação de vendas, 1 de 35 e rank #14. São
dois universos, não duas leituras do mesmo. Por isso `by="sales"` é passado
explicitamente e o portão de validação confere o parâmetro.

BEST-EFFORT sem proxy residencial BR: o IP de datacenter é marcado antes do
fingerprint. Sessão expira em horas — re-capturar com
`python utils/session_grabber.py --site shopee`.
"""

import random
import re
import time
from typing import Any, List, Optional
from urllib.parse import quote_plus

from loguru import logger

from bestsellers.base import BestSellerSource
from bestsellers.config import BASE_VENDIDOS_MES
from bestsellers.models import BestSellerItem
from scrapers.shopee import ShopeeScraper

_KEYWORD = "ar condicionado"
_ORDENACAO = "sales"
_ITENS_POR_PAGINA = 60
_DELAY_ENTRE_PAGINAS = (3.0, 7.0)

# "948 Vendido/Mês", "1,2mil vendidos", "948 vendidos"
_RE_VENDIDOS = re.compile(r"([\d.,]+)\s*(mil|k)?\s*vendid", re.I)
_RE_POR_MES = re.compile(r"/\s*m[êe]s|por\s+m[êe]s", re.I)


class ShopeeBestSellers(BestSellerSource):
    """Coletor da lista ordenada por vendas da Shopee."""

    key = "shopee"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._shopee: Optional[ShopeeScraper] = None

    def _abrir(self) -> None:
        self._shopee = ShopeeScraper(headless=self.headless)
        self._shopee.__enter__()

    def _fechar(self) -> None:
        if self._shopee is not None:
            self._shopee.__exit__()
            self._shopee = None

    def _coletar(self, paginas: int) -> List[BestSellerItem]:
        # O endpoint carrega o parâmetro de ordenação: é ele que o portão de
        # validação inspeciona para provar que a lista não é relevância.
        self.registrar_endpoint(
            f"https://shopee.com.br/api/v4/search/search_items"
            f"?by={_ORDENACAO}&keyword={_KEYWORD.replace(' ', '%20')}&order=desc"
        )
        # Com RAC_LOCAL_CHROME o `ShopeeScraper` abre o Chrome logado e NÃO
        # cria sessão HTTP — chamar `_fetch_page` ali estoura em `None.get`.
        # O caminho do browser também é o melhor: a própria página dispara o
        # `search_items` com o header anti-fraude que o replay não consegue
        # forjar.
        if self._shopee._local_active:
            return self._coletar_via_browser(paginas)
        return self._coletar_via_api(paginas)

    def _coletar_via_api(self, paginas: int) -> List[BestSellerItem]:
        """Replay da API v4 via curl_cffi + sessão capturada."""
        shopee = self._shopee
        itens: List[BestSellerItem] = []

        for pagina in range(max(1, paginas)):
            dados = shopee._fetch_page(_KEYWORD, pagina, by=_ORDENACAO)
            if dados is None:
                if shopee.captcha_hit:
                    raise RuntimeError(
                        "bloqueio da API v4 (403 / anti-fraude). Re-capture a "
                        "sessão: python utils/session_grabber.py --site shopee"
                    )
                break

            brutos = dados.get("items") or []
            if not brutos:
                logger.info(f"[{self.nome}] Sem mais resultados (pág {pagina + 1}).")
                break

            novos = self._parse(brutos, offset=len(itens))
            if not novos:
                shopee._dump_debug_response(_KEYWORD, pagina, dados)
                raise RuntimeError(
                    "API respondeu com itens mas nenhum parseou — a Shopee "
                    "trocou a estrutura do wrapper (dump salvo em logs/)."
                )
            itens.extend(novos)

            if len(brutos) < _ITENS_POR_PAGINA:
                break
            time.sleep(random.uniform(*_DELAY_ENTRE_PAGINAS))
        return itens

    def _coletar_via_browser(self, paginas: int) -> List[BestSellerItem]:
        """
        Coleta dentro do Chrome logado, interceptando a API v4 nativa.

        A URL navegada carrega `sortBy=sales` — é a MESMA ordenação da rota
        HTTP, então as duas vias produzem a mesma população. Sem o parâmetro,
        a página serviria relevância e a coleta mediria outra coisa.
        """
        shopee = self._shopee
        if shopee._local_browser is not None and shopee._page is not None:
            shopee._local_browser.warmup(
                shopee._page, "https://shopee.com.br/", host_key="shopee"
            )

        itens: List[BestSellerItem] = []
        for pagina in range(max(1, paginas)):
            url = (
                f"https://shopee.com.br/search?keyword={quote_plus(_KEYWORD)}"
                f"&sortBy={_ORDENACAO}&page={pagina}"
            )
            shopee._captured_search = []
            try:
                shopee._page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            except Exception as exc:
                logger.warning(f"[{self.nome}] goto p{pagina + 1} falhou: {exc}")
                break

            capturadas = shopee._await_captured(timeout_s=12.0)
            if not capturadas:
                # O scroll força a SERP a disparar a chamada quando o load
                # sozinho não disparou.
                try:
                    for _ in range(4):
                        shopee._page.mouse.wheel(0, 700)
                        time.sleep(random.uniform(0.4, 0.9))
                except Exception:
                    pass
                capturadas = shopee._await_captured(timeout_s=8.0)

            if not capturadas:
                raise RuntimeError(
                    "nenhuma resposta de search_items capturada no Chrome local "
                    "(a página pode estar exigindo login ou CAPTCHA)."
                )

            # A SERP dispara várias chamadas (ads, prefetch, resultados): fica
            # com a que rende MAIS itens, em vez de assumir que a 1ª é a boa.
            melhor: List[BestSellerItem] = []
            brutos_melhor: List[dict] = []
            alguma_com_itens = False
            for dados in capturadas:
                brutos = dados.get("items") or []
                if not brutos:
                    continue
                alguma_com_itens = True
                novos = self._parse(brutos, offset=len(itens))
                if len(novos) > len(melhor):
                    melhor, brutos_melhor = novos, brutos

            if not alguma_com_itens:
                # Nenhuma resposta trouxe item: a lista acabou de verdade.
                logger.info(f"[{self.nome}] Sem mais resultados (pág {pagina + 1}).")
                break

            if not melhor:
                # Itens presentes e nada parseou = a Shopee trocou o invólucro.
                # Este é o caminho PRINCIPAL de produção; tratar isso como
                # "acabou a lista" gravaria uma série curta em silêncio — a
                # perda de dado exata que os portões existem para pegar. Mesmo
                # tratamento do caminho de API.
                primeira = next(
                    (d for d in capturadas if d.get("items")), capturadas[0]
                )
                shopee._dump_debug_response(_KEYWORD, pagina, primeira)
                raise RuntimeError(
                    "search_items respondeu com itens mas nenhum parseou — a "
                    "Shopee trocou a estrutura do wrapper (dump em logs/)."
                )
            itens.extend(melhor)
            logger.info(
                f"[{self.nome}] Pág {pagina + 1}: {len(melhor)} produtos "
                "(Chrome local)"
            )

            if len(brutos_melhor) < _ITENS_POR_PAGINA:
                break
            time.sleep(random.uniform(*_DELAY_ENTRE_PAGINAS))
        return itens

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse(self, brutos: List[dict], offset: int) -> List[BestSellerItem]:
        shopee = self._shopee
        itens: List[BestSellerItem] = []

        for bruto in brutos:
            payload = shopee._extract_item_payload(bruto)
            if not payload:
                continue
            asset = payload.get("item_card_displayed_asset") or {}

            titulo = shopee._extract_name(payload, asset)
            if not titulo:
                continue

            vendidos, base = self._extrair_vendidos(
                self._texto_de_vendas(payload, asset)
            )
            itemid = payload.get("itemid") or payload.get("item_id")
            shopid = payload.get("shopid") or payload.get("shop_id")

            itens.append(BestSellerItem(
                rank=offset + len(itens) + 1,
                titulo=titulo,
                preco=shopee._normalize_price(shopee._extract_raw_price(payload, asset)),
                rating=self._extrair_rating(payload),
                reviews=self._extrair_reviews(payload),
                vendidos=vendidos,
                base_vendidos=base,
                seller=self._extrair_seller(payload, asset),
                sku_plataforma=f"{shopid}_{itemid}" if itemid and shopid else None,
                url_produto=(
                    f"https://shopee.com.br/product/{shopid}/{itemid}"
                    if itemid and shopid else None
                ),
                patrocinado=shopee._is_sponsored(payload, bruto, asset),
            ))
        return itens

    @staticmethod
    def _extrair_seller(payload: dict, asset: dict) -> Optional[str]:
        """
        Nome da loja.

        NUNCA cai para `shop_location`: aquele campo é a CIDADE de origem do
        envio ("São Paulo"), e gravá-lo no campo de seller corromperia a
        leitura de quem vende — o `data4` que a rotina manual já confundiu.
        Sem nome de loja, o campo fica vazio: desconhecido não é um seller.
        """
        # No item e no asset, só chaves prefixadas por `shop_`: `name` ali é o
        # nome do PRODUTO, e aceitá-lo colocaria o título no campo de seller.
        for origem in (payload, asset):
            if not isinstance(origem, dict):
                continue
            for chave in ("shop_name", "shop_username"):
                valor = origem.get(chave)
                if isinstance(valor, str) and valor.strip():
                    return valor.strip()

        # Dentro do bloco da loja, `name`/`username` já são da loja.
        for chave_bloco in ("shop_data", "shop_info", "shop"):
            bloco = payload.get(chave_bloco) or asset.get(chave_bloco)
            if not isinstance(bloco, dict):
                continue
            for chave in ("shop_name", "name", "username"):
                valor = bloco.get(chave)
                if isinstance(valor, str) and valor.strip():
                    return valor.strip()
        return None

    @staticmethod
    def _texto_de_vendas(payload: dict, asset: dict) -> Optional[str]:
        """
        Texto de volume do card, PRESERVANDO o qualificador "/Mês".

        `ShopeeScraper._extract_sold` devolve `f"{historical_sold_count}
        vendidos"` sempre que o campo numérico existe — e ele existe quase
        sempre. Passar por lá apagaria o "/Mês" do texto de exibição e a
        Shopee inteira entraria como 'acumulado', descartando justamente a
        única medida de velocidade real do conjunto.

        Por isso os campos de TEXTO são consultados primeiro; o numérico
        (`historical_sold_count`, acumulado por definição) só entra quando não
        há texto nenhum.
        """
        blocos = [
            payload.get("item_card_display_sold_count"),
            asset.get("sold_count"),
        ]
        for bloco in blocos:
            if not isinstance(bloco, dict):
                continue
            for chave in (
                "historical_sold_count_text", "display_sold_count_text", "text",
            ):
                valor = bloco.get(chave)
                if isinstance(valor, str) and valor.strip():
                    return valor.strip()

        for bloco in blocos:
            if isinstance(bloco, dict):
                numero = bloco.get("historical_sold_count")
                if numero is not None:
                    return f"{numero} vendidos"

        numero = payload.get("historical_sold") or payload.get("sold")
        return f"{numero} vendidos" if numero else None

    @staticmethod
    def _extrair_vendidos(texto: Optional[str]) -> tuple:
        """
        (quantidade, base) a partir do texto de vendas do card.

        A base sai como 'mes' apenas quando o texto diz explicitamente
        "Vendido/Mês". Um "948 vendidos" sem qualificação é acumulado — marcar
        como mensal misturaria as duas unidades na soma de velocidade, que é
        exatamente o erro que a regra dura proíbe.
        """
        if not texto:
            return None, None
        achado = _RE_VENDIDOS.search(texto)
        if not achado:
            return None, None
        bruto = achado.group(1).replace(".", "").replace(",", ".")
        try:
            valor = float(bruto)
        except ValueError:
            return None, None
        if achado.group(2):
            valor *= 1000
        base = BASE_VENDIDOS_MES if _RE_POR_MES.search(texto) else "acumulado"
        return valor, base

    @staticmethod
    def _extrair_rating(payload: dict) -> Optional[float]:
        estrelas = payload.get("item_rating") or {}
        if isinstance(estrelas, dict):
            valor = estrelas.get("rating_star")
            if isinstance(valor, (int, float)) and 0 < valor <= 5:
                return round(float(valor), 2)
        valor = payload.get("shop_rating")
        if isinstance(valor, (int, float)) and 0 < valor <= 5:
            return round(float(valor), 2)
        return None

    @staticmethod
    def _extrair_reviews(payload: dict) -> Optional[int]:
        estrelas = payload.get("item_rating") or {}
        if isinstance(estrelas, dict):
            for chave in ("rating_count_total", "rcount_with_context", "rcount_with_image"):
                valor = estrelas.get(chave)
                if isinstance(valor, int) and valor >= 0:
                    return valor
            contagens = estrelas.get("rating_count")
            # rating_count[0] é o total; os demais são por nº de estrelas.
            if isinstance(contagens, list) and contagens:
                primeiro = contagens[0]
                if isinstance(primeiro, int) and primeiro >= 0:
                    return primeiro
        return None
