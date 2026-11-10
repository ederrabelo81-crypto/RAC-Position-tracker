"""
bestsellers/sources/casas_bahia.py — Lista "Mais Vendidos" da Casas Bahia.

Site:  /ar-condicionado/b?ordenacao=maisvendidos
API:   VTEX Intelligent Search com `sort=orders_desc` (é o que a UI envia por
       trás de `ordenacao=maisvendidos`)

A coleta usa a API porque o `sellers[]` do payload VTEX expõe o vencedor da
buy box (`sellerDefault`) e a competição de ofertas diretamente — o DOM não
traz nada disso. O warm-up de cookies Akamai (session curl_cffi persistente do
`CasasBahiaScraper`) é o que mantém o endpoint acessível de IP de datacenter.

ARMADILHA PRINCIPAL — CONTAMINAÇÃO. Em 10/08/2026, 6 de 20 itens da lista eram
umidificador, depurador de ar e afins, ocupando as posições #3, #4 e #5. Os
itens continuam na base (com `tipo=FORA_ESCOPO`) para que o portão de validação
consiga medir a contaminação; o KPI os ignora. Preços "de" desta fonte são
lixo (desconto fantasma de -79%) e por isso `preco_de` não é coletado aqui.
"""

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup, Tag
from loguru import logger

from bestsellers.base import BestSellerSource
from bestsellers.models import BestSellerItem
from scrapers.casas_bahia import _SELECTORS, CasasBahiaScraper
from scrapers.local_browser import is_local_chrome_enabled
from utils.text import parse_price, parse_rating, parse_review_count

_VTEX_IS_URL = (
    "https://www.casasbahia.com.br/_v/api/intelligent-search"
    "/product_search/pt/pt-BR/search"
)
_TERMO = "ar condicionado"
_ORDENACAO = "orders_desc"
_ITENS_POR_PAGINA = 24
_TIMEOUT = 10

# A Casas Bahia saiu da VTEX: as URLs de produto viraram `/<slug>/p/<id>` e a
# coleta de oferta em produção só funciona pelo DOM (0 buy box em 254–310
# linhas/turno, 09–10/11/2026). A API IS responde 200 com HTML (a vitrine nova)
# para qualquer IP — não é Akamai e não volta com sessão. O caminho que funciona
# é a VITRINE no Chrome real, ordenada por "Mais vendidos".
_RE_MAIS_VENDIDOS = re.compile(r"mais\s+vendid", re.I)

# URL que prova a ordenação por vendas (parâmetro na query, em qualquer grafia
# conhecida). Sem um destes, a ordenação só vale com o rótulo selecionado na
# tela — ver `_ordenacao_confirmada`.
_RE_URL_ORDENADA = re.compile(
    r"mais.?vendid|orders_desc|best.?sell|top.?sell|sold|vendas", re.I
)

# Marcador do endpoint quando a ordenação foi confirmada pelo rótulo da tela
# (a URL não carrega parâmetro). Está em `parametros_ordenacao` da fonte, então
# o portão de validação aceita a lista como de vendas — e só ela.
MARCADOR_ORDENACAO_UI = "ordenacao_ui=mais_vendidos"

# Gatilhos do controle de ordenação (dropdown custom) — do mais explícito ao
# mais genérico.
_SORT_TRIGGERS = (
    "[data-testid*='sort' i]",
    "[data-testid*='order' i]",
    "button[aria-haspopup]",
    "[class*='sort' i] button",
    "[class*='Sort'] button",
    "[class*='order' i] button",
    "button:has-text('Ordenar')",
    "button:has-text('Relevância')",
    "button:has-text('Mais relevantes')",
)

# Onde a opção "Mais vendidos" aparece depois de abrir o dropdown.
_SORT_OPTIONS = (
    "[role='option']",
    "[role='menuitem']",
    "[role='radio']",
    "li",
    "button",
    "label",
    "a",
)


class CasasBahiaBestSellers(BestSellerSource):
    """Coletor do ranking de mais vendidos da Casas Bahia (VTEX IS)."""

    key = "casasbahia"

    def __init__(self, headless: bool = True) -> None:
        super().__init__(headless=headless)
        self._cb: Optional[CasasBahiaScraper] = None
        # True quando um Chrome real (local logado ou CDP) subiu — é o
        # fingerprint que o Akamai aceita e a via que destrava a coleta.
        self._browser_ativo: bool = False
        # True quando `CasasBahiaScraper.__enter__` chegou a rodar — ou seja, um
        # browser (real OU próprio) pode ter sido lançado. O cleanup é gated por
        # ISTO, não por `_browser_ativo`: o `_launch` cai num Chromium headless
        # próprio quando o Chrome real não sobe, e esse browser precisa ser
        # fechado mesmo com `_browser_ativo=False`, senão vaza o handle
        # compartilhado do Playwright pelo resto da coleta (as 6 fontes).
        self._cb_entered: bool = False

    def _abrir(self) -> None:
        self._cb = CasasBahiaScraper(headless=self.headless)
        # Sobe o browser SÓ quando há um Chrome real disponível (perfil local
        # logado via RAC_LOCAL_CHROME, ou CDP). É o caminho que passa pelo
        # Akamai: o fetch same-origin carrega o _abck validado que o replay
        # curl_cffi de IP datacenter não tem — sem isso a API VTEX responde
        # text/html (desafio Akamai) e a plataforma sai ausente. Sem Chrome
        # real, NÃO lançamos um browser próprio (headless): mantém-se o caminho
        # leve curl_cffi, que é o comportamento herdado.
        tem_chrome_real = bool(
            is_local_chrome_enabled()
            or os.getenv("RAC_CDP_URL", "").strip()
            or os.getenv("MAGALU_CDP_URL", "").strip()
        )
        if not tem_chrome_real:
            return  # curl_cffi puro — nenhum browser é lançado.

        try:
            # Marcado ANTES do `__enter__`: se ele estourar DEPOIS de já ter
            # lançado o browser (falha de contexto/página no meio), o
            # `_fechar_cb` do handler precisa saber que há algo a fechar —
            # senão o browser e o handle compartilhado do Playwright vazam.
            # `__exit__`/`_close` é no-op seguro se nada chegou a subir.
            self._cb_entered = True
            self._cb.__enter__()
            self._browser_ativo = self._cb._real_browser_active
        except Exception as exc:
            logger.warning(
                f"[{self.nome}] Chrome real não subiu ({exc}) — seguindo "
                "pela API VTEX (curl_cffi)."
            )
            # `__enter__` pode ter lançado um browser antes de estourar; garante
            # o fechamento em vez de vazá-lo.
            self._fechar_cb()
            return

        # `__enter__` pode ter caído no `super()._launch()` (Chromium headless
        # próprio) porque o Chrome real não subiu. Na coleta de mais vendidos
        # esse browser não serve (o Akamai bloqueia o headless de IP datacenter)
        # e a promessa é seguir por curl_cffi — então fecha já, para não arrastar
        # um browser inútil e o handle do Playwright pelo resto da execução.
        if not self._browser_ativo:
            logger.warning(
                f"[{self.nome}] Chrome real indisponível — fechando o browser "
                "próprio e seguindo pela API VTEX (curl_cffi)."
            )
            self._fechar_cb()

    def _fechar_cb(self) -> None:
        """Fecha o scraper filho quando algum `__enter__` chegou a rodar.

        Fecha SÓ a aba dedicada no modo Chrome real (a janela é compartilhada e
        encerrada no fim do processo via ``close_local_browser``/atexit); no
        fallback headless, o ``_close`` do BaseScraper encerra o browser e
        devolve o handle do Playwright. É um no-op seguro se nada foi lançado.
        """
        if self._cb is None or not self._cb_entered:
            return
        try:
            self._cb.__exit__()
        except Exception as exc:
            logger.debug(f"[{self.nome}] Erro ao fechar o browser: {exc}")
        self._cb_entered = False
        self._browser_ativo = False

    def _fechar(self) -> None:
        self._fechar_cb()
        self._cb = None
        self._browser_ativo = False

    def _coletar(self, paginas: int) -> List[BestSellerItem]:
        # O endpoint carrega o parâmetro de ordenação — é ele que o portão de
        # validação inspeciona para provar que a lista é de vendas, não de
        # relevância. Registrado igual nos dois caminhos (browser e curl_cffi).
        params = {
            "query": _TERMO,
            "page": 1,
            "count": _ITENS_POR_PAGINA,
            "sort": _ORDENACAO,
            "hideUnavailableItems": "false",
        }
        self.registrar_endpoint(f"{_VTEX_IS_URL}?{urlencode(params)}")

        # Caminho preferido: Chrome real logado. O warm-up valida o _abck na
        # home; tenta a IS (legado VTEX) e, sem ela, a vitrine ordenada.
        if self._browser_ativo and self._cb._real_browser_active:
            itens = self._coletar_via_browser(paginas)
            if itens:
                return itens
            # Sem curl_cffi aqui: a API VTEX não existe mais na Casas Bahia
            # (responde a vitrine em HTML), e cair nela só trocava o motivo
            # real pela mensagem enganosa de "bloqueio Akamai".
            itens, motivo = self._coletar_via_vitrine()
            if itens:
                return itens
            raise RuntimeError(f"vitrine de mais vendidos: {motivo}")

        return self._coletar_via_api(paginas)

    def _coletar_via_browser(self, paginas: int) -> List[BestSellerItem]:
        """Coleta a lista de mais vendidos DENTRO do Chrome real logado.

        Aquece a home (o sensor.js do Akamai valida o ``_abck``) e faz o fetch
        same-origin da VTEX intelligent-search com ``sort=orders_desc`` — a
        MESMA ordenação que ``ordenacao=maisvendidos`` dispara na UI.
        """
        cb = self._cb
        # Home + emulação humana + espera do _abck (idempotente: 1x por execução).
        cb._warmup_cdp_session()

        itens: List[BestSellerItem] = []
        for pagina in range(1, max(1, paginas) + 1):
            produtos = cb.vtex_is_in_page(_TERMO, pagina, sort=_ORDENACAO)
            if not produtos:
                break
            itens.extend(self._parse(produtos, offset=len(itens)))
            if len(produtos) < _ITENS_POR_PAGINA:
                break
        return itens

    # ------------------------------------------------------------------
    # Vitrine (DOM) — a Casas Bahia pós-VTEX
    # ------------------------------------------------------------------

    def _coletar_via_vitrine(self) -> tuple:
        """
        Abre a vitrine de ar condicionado no Chrome real, ordena por
        "Mais vendidos" e lê os cards do DOM.

        Ordem de navegação:
          1. a URL pública da fonte (``?ordenacao=maisvendidos``);
          2. se a ordenação não se confirmar, a busca orgânica (digitar no
             campo — o caminho que a coleta de oferta usa todo turno) e o
             controle "Ordenar por" da página.

        **Regra dura:** sem ordenação CONFIRMADA (parâmetro na URL ou rótulo
        "Mais vendidos" selecionado), nada é gravado — uma lista por relevância
        com o rótulo de mais vendidos misturaria as duas referências.

        Returns:
            (itens, motivo) — ``motivo`` explica a lista vazia.
        """
        cb = self._cb
        page = cb._page
        if page is None:
            return [], "aba do Chrome indisponível"

        url = self.spec.url_publica
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=40_000)
        except Exception as exc:
            logger.warning(f"[{self.nome}] goto da vitrine falhou: {exc}")
        cb._wait_for_products(timeout_ms=10_000)

        html = cb._page_html() or ""
        if html and cb._check_blocked(html):
            caminho = self._dump("bloqueio", html)
            return [], f"bloqueio anti-bot na vitrine (evidência: {caminho})"

        ordenada = self._ordenacao_confirmada(page, aceitar_url=False)
        if not ordenada:
            logger.info(
                f"[{self.nome}] URL pública sem ordenação confirmada "
                f"({(page.url or '')[:90]}) — indo pela busca + 'Ordenar por'."
            )
            if not cb._organic_search(_TERMO):
                try:
                    page.goto(
                        cb._build_url(_TERMO, 1),
                        wait_until="domcontentloaded",
                        timeout=40_000,
                    )
                except Exception as exc:
                    return [], f"busca não abriu ({exc})"
            cb._wait_for_products(timeout_ms=10_000)
            ordenada = self._aplicar_mais_vendidos(page)

        if not ordenada:
            caminho = self._dump("sem_ordenacao", cb._page_html() or "")
            return [], (
                "não consegui aplicar/confirmar a ordenação 'Mais vendidos' "
                f"(URL {(page.url or '')[:90]}; evidência: {caminho}). "
                "Nada gravado para não misturar relevância com vendas."
            )

        cb._wait_for_products(timeout_ms=10_000)
        try:
            cb._human_scroll(steps=8, step_px=400)
        except Exception as exc:
            logger.debug(f"[{self.nome}] scroll interrompido: {exc}")
        time.sleep(1.5)
        html = cb._page_html() or ""

        final = page.url or url
        # A ordenação foi confirmada acima; o endpoint carrega a prova num
        # formato que o portão de validação reconhece.
        self.endpoint = (
            final if self.spec.ordenacao_comprovada(final)
            else f"{final} [{MARCADOR_ORDENACAO_UI}]"
        )

        itens = self._parse_vitrine(html)
        if not itens:
            caminho = self._dump("sem_cards", html)
            return [], f"vitrine ordenada sem cards de produto (evidência: {caminho})"
        logger.info(
            f"[{self.nome}] {len(itens)} produtos via vitrine 'Mais vendidos' "
            f"(DOM) — {final[:90]}"
        )
        return itens, ""

    @staticmethod
    def _ordenacao_confirmada(page: Any, aceitar_url: bool = True) -> bool:
        """
        A página atual está ordenada por "Mais vendidos"?

        Evidência aceita: ``<select>`` com a opção selecionada, elemento
        ``aria-selected``/``aria-checked`` com o rótulo, o gatilho do controle
        exibindo SÓ o rótulo, ou — com ``aceitar_url`` — parâmetro de vendas
        na query. Texto "Mais vendidos" solto NÃO conta (o menu do site tem um
        link com esse nome), nem um contêiner que lista todas as opções.

        Args:
            aceitar_url: False para a URL que NÓS digitamos — um parâmetro que
                o site ignora continuaria na barra e "provaria" uma ordenação
                que não aconteceu. Só vale a URL que o site escreveu.
        """
        if aceitar_url:
            try:
                url = page.url or ""
            except Exception:
                url = ""
            if "?" in url and _RE_URL_ORDENADA.search(url.split("?", 1)[1]):
                return True
        try:
            return bool(page.evaluate(
                """() => {
                    const solto = /mais\\s+vendid/i;
                    const so = /^\\s*(ordenar( por)?:?\\s*)?mais\\s+vendidos?\\s*$/i;
                    for (const s of document.querySelectorAll('select')) {
                        const o = s.options[s.selectedIndex];
                        if (o && solto.test(o.textContent || '')) return true;
                    }
                    for (const el of document.querySelectorAll(
                            "[aria-selected='true'], [aria-checked='true']")) {
                        const t = (el.innerText || '').trim();
                        if (t.length < 40 && solto.test(t)) return true;
                    }
                    const gat = [
                        "[data-testid*='sort' i]", "[data-testid*='order' i]",
                        "[class*='sort' i] button", "[class*='Sort'] button",
                        "button[aria-haspopup]",
                    ].join(',');
                    for (const el of document.querySelectorAll(gat)) {
                        if (so.test((el.innerText || '').trim())) return true;
                    }
                    return false;
                }"""
            ))
        except Exception:
            return False

    def _aplicar_mais_vendidos(self, page: Any) -> bool:
        """
        Escolhe "Mais vendidos" no controle de ordenação da página.

        Tenta ``<select>`` nativo e, depois, dropdown custom (abre o gatilho e
        clica na opção). Devolve True só com a ordenação CONFIRMADA.
        """
        # 1) <select> nativo
        try:
            selects = page.locator("select")
            for i in range(min(selects.count(), 6)):
                sel = selects.nth(i)
                rotulos = sel.locator("option").all_inner_texts()
                alvo = next((r for r in rotulos if _RE_MAIS_VENDIDOS.search(r)), None)
                if alvo:
                    sel.select_option(label=alvo.strip())
                    self._esperar_recarga(page)
                    if self._ordenacao_confirmada(page):
                        return True
        except Exception as exc:
            logger.debug(f"[{self.nome}] select de ordenação: {exc}")

        # 2) dropdown custom
        for gatilho in _SORT_TRIGGERS:
            try:
                loc = page.locator(gatilho).first
                if not loc.is_visible(timeout=1_500):
                    continue
                loc.click(timeout=4_000)
                page.wait_for_timeout(800)
            except Exception:
                continue
            for opcao in _SORT_OPTIONS:
                try:
                    alvo = page.locator(opcao).filter(has_text=_RE_MAIS_VENDIDOS)
                    for j in range(min(alvo.count(), 5)):
                        el = alvo.nth(j)
                        if not el.is_visible():
                            continue
                        # Link que sai da busca (menu "Mais vendidos" do site)
                        # não é a ordenação — só aceita link na mesma rota.
                        href = el.get_attribute("href") or ""
                        if href and not (
                            _RE_URL_ORDENADA.search(href) and "?" in href
                        ):
                            continue
                        el.click(timeout=4_000)
                        self._esperar_recarga(page)
                        if self._ordenacao_confirmada(page):
                            logger.info(
                                f"[{self.nome}] Ordenação 'Mais vendidos' aplicada "
                                f"(gatilho {gatilho!r}, opção {opcao!r})."
                            )
                            return True
                        break
                except Exception:
                    continue
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
        return False

    @staticmethod
    def _esperar_recarga(page: Any) -> None:
        """Espera a lista recarregar depois de mudar a ordenação."""
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:
            pass
        try:
            page.wait_for_timeout(2_500)
        except Exception:
            pass

    def _parse_vitrine(self, html: str) -> List[BestSellerItem]:
        """
        Cards da vitrine → itens, na ordem da página.

        Usa os seletores do ``CasasBahiaScraper`` (mesma vitrine da coleta de
        oferta), mas sem o ``_build_record``: o ranking precisa do título CRU
        (o nome canônico é decidido em ``BestSellerItem``). O DOM não expõe
        ``sellers[]`` — ``seller`` fica vazio, nunca "Casas Bahia" por padrão.
        """
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        cards, _ = CasasBahiaScraper._detect_items(soup)
        itens: List[BestSellerItem] = []
        vistos = set()
        for card in cards:
            titulo = self._titulo_card(card)
            if not titulo:
                continue
            url = CasasBahiaScraper._extract_dom_url(card)
            chave = url or titulo
            if chave in vistos:
                continue
            vistos.add(chave)
            preco_el = CasasBahiaScraper._first_match(card, _SELECTORS["price_candidates"])
            rating_el = card.select_one(_SELECTORS["rating"])
            reviews_el = card.select_one(_SELECTORS["review_count"])
            itens.append(BestSellerItem(
                rank=len(itens) + 1,
                titulo=titulo,
                preco=parse_price(preco_el.get_text(" ", strip=True)) if preco_el else None,
                rating=parse_rating(rating_el.get_text() if rating_el else None),
                reviews=parse_review_count(reviews_el.get_text() if reviews_el else None),
                seller=None,
                sku_plataforma=self._sku_da_url(url),
                url_produto=url,
                patrocinado=bool(card.select_one(_SELECTORS["sponsored"])),
            ))
        return itens

    @staticmethod
    def _titulo_card(card: Tag) -> Optional[str]:
        el = CasasBahiaScraper._first_match(card, _SELECTORS["title_candidates"])
        titulo = el.get_text(" ", strip=True) if el else None
        if not titulo:
            img = card.select_one("img[alt]")
            titulo = (img.get("alt") or "").strip() if img else None
        return titulo or None

    @staticmethod
    def _sku_da_url(url: Optional[str]) -> Optional[str]:
        """Id do produto na URL nova (``/<slug>/p/<id>``)."""
        if not url:
            return None
        achado = re.search(r"/p/(\d+)", url)
        return achado.group(1) if achado else None

    def _dump(self, rotulo: str, html: str) -> str:
        """Salva o HTML em logs/ para conferência e devolve o caminho."""
        if not html:
            return "não salva"
        try:
            pasta = Path("logs")
            pasta.mkdir(parents=True, exist_ok=True)
            caminho = pasta / f"cb_maisvendidos_{rotulo}_{time.strftime('%Y%m%d_%H%M%S')}.html"
            caminho.write_text(html[:2_000_000], encoding="utf-8")
            return str(caminho)
        except Exception:
            return "não salva"

    def _coletar_via_api(self, paginas: int) -> List[BestSellerItem]:
        """Replay da API VTEX via curl_cffi + warm-up de cookies Akamai."""
        sessao = self._cb._get_warmed_session()
        if sessao is None:
            raise RuntimeError(
                "curl_cffi indisponível — sem ele o Akamai bloqueia a API VTEX. "
                "Instale: pip install curl-cffi>=0.6.0"
            )

        itens: List[BestSellerItem] = []
        for pagina in range(1, max(1, paginas) + 1):
            params = {
                "query": _TERMO,
                "page": pagina,
                "count": _ITENS_POR_PAGINA,
                "sort": _ORDENACAO,
                "hideUnavailableItems": "false",
            }
            produtos = self._consultar(sessao, params, pagina)
            if not produtos:
                break
            itens.extend(self._parse(produtos, offset=len(itens)))
            if len(produtos) < _ITENS_POR_PAGINA:
                break
        return itens

    def _consultar(self, sessao, params: Dict[str, Any], pagina: int) -> List[Dict]:
        try:
            resposta = sessao.get(
                _VTEX_IS_URL,
                headers=self._cb._vtex_headers(),
                params=params,
                impersonate="chrome124",
                timeout=_TIMEOUT,
            )
        except Exception as exc:
            raise RuntimeError(f"API VTEX inacessível: {exc}") from exc

        tipo = resposta.headers.get("content-type", "")
        if resposta.status_code != 200 or "application/json" not in tipo:
            # HTML aqui = página de desafio do Akamai, não lista curta.
            raise RuntimeError(
                f"API VTEX respondeu HTTP {resposta.status_code} (CT={tipo[:40]}) "
                "— bloqueio Akamai. Sem proxy BR, re-capture a sessão: "
                "python utils/session_grabber.py --site casasbahia"
            )

        dados = resposta.json()
        produtos = (
            dados.get("products")
            or (dados.get("productSearch") or {}).get("products")
            or []
        )
        if produtos:
            logger.info(
                f"[{self.nome}] VTEX {_ORDENACAO}: {len(produtos)} produtos (p{pagina})"
            )
        return produtos

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse(self, produtos: List[Dict], offset: int) -> List[BestSellerItem]:
        itens: List[BestSellerItem] = []
        for produto in produtos:
            titulo = (
                produto.get("productName")
                or produto.get("name")
                or produto.get("productTitle")
            )
            if not titulo:
                continue

            sellers = self._cb._extract_vtex_sellers(produto)
            rating, reviews = self._cb._extract_vtex_rating(produto)

            itens.append(BestSellerItem(
                rank=offset + len(itens) + 1,
                titulo=titulo,
                # Preço da oferta que vence a buy box — não o menor do array,
                # que pode ser de um seller sem estoque.
                preco=sellers.get("price_float"),
                rating=rating,
                reviews=reviews,
                seller=sellers.get("buy_box_seller"),
                sku_plataforma=str(
                    produto.get("productId") or produto.get("productReference") or ""
                ) or None,
                url_produto=self._url(produto),
            ))
        return itens

    @staticmethod
    def _url(produto: Dict[str, Any]) -> Optional[str]:
        link = produto.get("link") or produto.get("linkText")
        if not isinstance(link, str) or not link.strip():
            return None
        link = link.strip()
        if link.startswith("http"):
            return link
        if link.startswith("/"):
            return f"https://www.casasbahia.com.br{link}"
        return f"https://www.casasbahia.com.br/{link}/p"
