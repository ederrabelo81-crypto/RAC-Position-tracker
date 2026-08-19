"""
bestsellers/base.py — Contrato de uma fonte de lista "Mais Vendidos".

Cada plataforma implementa `_coletar()` devolvendo os itens crus na ordem em
que aparecem na lista. A classe base cuida do que é igual para todas:

  * ranking sequencial e sem duplicata (posição duplicada é ERRO no portão
    de validação — melhor reparar aqui e avisar do que gravar lixo);
  * deduplicação por anúncio, mantendo a MELHOR posição;
  * captura da URL/endpoint efetivamente usado (prova de que a lista está
    ordenada por vendas);
  * tratamento de falha — uma plataforma que cai não derruba a coleta das
    outras, ela apenas não produz linhas e é reportada como ausente.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from loguru import logger

from config import PAGE_TIMEOUT
from bestsellers.config import SOURCES, SourceSpec
from bestsellers.models import BestSellerItem
from scrapers.base import BaseScraper
from scrapers.local_browser import get_local_browser, is_local_chrome_enabled


class PlainBrowser(BaseScraper):
    """
    Host de browser sem lógica de busca.

    Existe para reaproveitar o ciclo de vida do Playwright do
    `BaseScraper` (stealth, rotação de UA, locale pt-BR, timezone de São
    Paulo, dump de HTML de debug) nas listas que só precisam de um `goto`
    numa URL fixa. `search()` é intencionalmente vazio: esta classe navega,
    quem interpreta é a fonte.

    Chrome local (``RAC_LOCAL_CHROME``): quando ligado — o caso do notebook
    coletor agendado — reusa o mesmo Chrome real e LOGADO (perfil dedicado,
    IP residencial) compartilhado por ML/Magalu/Shopee/Casas Bahia, em vez de
    subir um Chromium headless próprio. Um browser lançado do zero é flagado
    pelo antibot da Amazon (CAPTCHA) e a lista de mais vendidos saía vazia; o
    Chrome comum atacado via CDP passa com o mesmo fingerprint que as demais
    listas já usam. Sem a env (VM / GitHub Actions) nada muda — cai no
    ``_launch`` padrão do ``BaseScraper``.
    """

    def __init__(self, platform_name: str, headless: bool = True) -> None:
        self.platform_name = platform_name
        super().__init__(headless=headless)
        # Ligado quando estamos usando a aba do Chrome compartilhado — nesse
        # caso o ``_close`` fecha SÓ a aba, nunca o Chrome (que é dos outros
        # coletores e é encerrado uma vez só, no fim da coleta).
        self._local_active: bool = False

    def _launch(self) -> None:
        """Preferência: aba do Chrome real local (perfil dedicado) via CDP."""
        if is_local_chrome_enabled():
            lb = get_local_browser()
            if lb is not None:
                page = lb.new_page()
                if page is not None:
                    self._context = lb.context
                    self._page = page
                    self._page.set_default_timeout(PAGE_TIMEOUT)
                    self._local_active = True
                    logger.info(
                        f"[{self.platform_name}] Chrome real local (perfil "
                        "compartilhado) — fingerprint nativo, sem CAPTCHA"
                    )
                    return
            logger.warning(
                f"[{self.platform_name}] RAC_LOCAL_CHROME ligado mas o Chrome "
                "local não abriu — caindo para launch próprio (Playwright)"
            )
        super()._launch()

    def _close(self) -> None:
        # Modo Chrome local: fecha SÓ a aba dedicada — o Chrome é compartilhado
        # e desconectado uma única vez no fim da coleta (close_local_browser).
        if self._local_active:
            try:
                if self._page is not None and not self._page.is_closed():
                    self._page.close()
            except Exception:
                pass
            self._page = None
            self._context = None
            self._local_active = False
            return
        super()._close()

    def search(self, *_args, **_kwargs) -> List[Dict[str, Any]]:
        """Não usado — a coleta de ranking não é dirigida por keyword."""
        return []


class BestSellerSource(ABC):
    """
    Fonte de uma lista de mais vendidos.

    Subclasses definem `key` (deve existir em `config.SOURCES`) e implementam
    `_coletar`. Recursos pesados (browser, sessão HTTP) devem ser abertos em
    `_abrir` e liberados em `_fechar` — a base garante o par via context
    manager mesmo quando `_coletar` levanta exceção.
    """

    key: str = ""

    # True quando a plataforma imprime a posição na página (a Amazon mostra
    # "#7"). Nesse caso o rank vindo da fonte é a verdade e não é reescrito:
    # se um item for descartado por falta de título, o buraco em 1..N é
    # informação real, não defeito do parser.
    ranks_explicitos: bool = False

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        # Endpoint realmente chamado. Preenchido via `registrar_endpoint`; é o
        # que o portão de validação inspeciona para provar a ordenação por
        # vendas.
        self.endpoint: str = ""
        self.falha: Optional[str] = None

    def registrar_endpoint(self, url: str) -> None:
        """
        Grava o endpoint da PRIMEIRA página, ignorando as seguintes.

        Todas as linhas da coleta recebem o mesmo `endpoint`, então deixar
        cada página sobrescrevê-lo carimbaria a URL da página 2 (`?pg=2`) nos
        registros da página 1. `comparar_endpoints` leria isso como "endpoint
        mudou" sempre que o nº de páginas variasse, e derrubaria a plataforma
        dos deltas de preço e estabilidade sem nada ter mudado de verdade.

        A paginação também não é evidência de ordenação — o que prova a lista
        é o parâmetro de sort, presente já na primeira chamada.
        """
        if not self.endpoint and url:
            self.endpoint = url

    # ------------------------------------------------------------------
    # Metadados
    # ------------------------------------------------------------------

    @property
    def spec(self) -> SourceSpec:
        return SOURCES[self.key]

    @property
    def nome(self) -> str:
        return self.spec.nome

    def url_coleta(self) -> str:
        """URL pública que um humano abre para conferir a lista."""
        return self.spec.url_publica

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def _abrir(self) -> None:
        """Abre recursos (browser, sessão). Sobrescrever quando necessário."""

    def _fechar(self) -> None:
        """Libera recursos. Sobrescrever quando necessário."""

    def __enter__(self) -> "BestSellerSource":
        try:
            self._abrir()
        except Exception:
            # Aquisição parcial (o Chromium subiu e o `new_context` estourou)
            # deixaria o browser e o driver do Playwright vivos pelo resto da
            # coleta das outras cinco plataformas. `__exit__` não roda quando
            # `__enter__` levanta — a liberação tem que acontecer aqui.
            self._fechar_seguro()
            raise
        return self

    def __exit__(self, *_) -> None:
        self._fechar_seguro()

    def _fechar_seguro(self) -> None:
        """`_fechar()` que nunca levanta — cleanup não derruba a coleta."""
        try:
            self._fechar()
        except Exception as exc:
            logger.warning(f"[{self.nome}] Erro ao liberar recursos: {exc}")

    # ------------------------------------------------------------------
    # Coleta
    # ------------------------------------------------------------------

    @abstractmethod
    def _coletar(self, paginas: int) -> List[BestSellerItem]:
        """
        Baixa a lista e devolve os itens na ordem da página.

        A subclasse deve preencher `self.endpoint` com a URL/endpoint que
        realmente respondeu, e numerar `rank` a partir de 1 quando a
        plataforma não expõe a posição explicitamente.
        """

    def coletar(self, paginas: int = 1) -> List[BestSellerItem]:
        """
        Executa a coleta com normalização e tratamento de falha.

        Uma exceção aqui vira lista vazia + `self.falha` preenchida: o
        relatório registra a AUSÊNCIA da plataforma em vez de comparar bases
        desiguais silenciosamente.

        Args:
            paginas: nº de páginas da lista a percorrer (1 = só a primeira).

        Returns:
            Itens ordenados por rank, sem duplicata de anúncio.
        """
        try:
            itens = self._coletar(paginas) or []
        except Exception as exc:
            self.falha = f"{type(exc).__name__}: {exc}"
            logger.error(f"[{self.nome}] Coleta falhou: {self.falha}")
            return []

        itens = self._dedup(itens)
        itens = self._normalizar_ranks(itens)

        if itens:
            midea = sum(1 for i in itens if i.grupo_midea)
            logger.success(
                f"[{self.nome}] {len(itens)} posições coletadas "
                f"({midea} do grupo Midea)"
            )
        else:
            # 0 itens sem exceção é indistinguível de bloqueio silencioso —
            # sobe para WARNING para o grep de monitoramento capturar.
            logger.warning(f"[{self.nome}] 0 posições coletadas")
            if not self.falha:
                self.falha = "lista vazia (possível bloqueio ou mudança de layout)"
        return itens

    # ------------------------------------------------------------------
    # Normalização
    # ------------------------------------------------------------------

    @staticmethod
    def _dedup(itens: List[BestSellerItem]) -> List[BestSellerItem]:
        """
        Remove o mesmo anúncio repetido, mantendo a melhor (menor) posição.

        Carrosséis e blocos patrocinados repetem o mesmo anúncio em posições
        diferentes; contar duas vezes infla o KPI da marca que aparece no
        carrossel. A chave é o SKU da plataforma quando existe — o título
        sozinho colapsaria variantes legítimas (mesmo modelo, BTU diferente,
        quando o BTU não está no título).

        Duas coisas ao mesmo tempo:

        * a POSIÇÃO na lista de saída é a da primeira aparição — reordenar
          pelo rank, quando dois blocos independentes da página numeram cada
          um a partir de 1, promoveria um card do segundo bloco à frente de
          um card que veio antes dele;
        * o REGISTRO mantido é o da melhor (menor) posição, inteiro. Copiar
          só o `rank` para o card de primeira aparição atribuiria à melhor
          posição o preço, o `vendidos` e o `patrocinado` do card pior — a
          leitura do topo do ranking sairia do card errado.
        """
        vistos: Dict[str, BestSellerItem] = {}
        ordem: List[str] = []
        for item in itens:
            chave = item.sku_plataforma or (item.titulo or "").strip().lower()
            if not chave:
                continue
            anterior = vistos.get(chave)
            if anterior is None:
                ordem.append(chave)
                vistos[chave] = item
            elif item.rank < anterior.rank:
                vistos[chave] = item
        return [vistos[chave] for chave in ordem]

    def _normalizar_ranks(self, itens: List[BestSellerItem]) -> List[BestSellerItem]:
        """
        Garante ranking 1..N sem buracos nem repetição.

        Posição duplicada é ERRO no portão de validação. Quando ela aparece
        aqui — dois blocos da página com numeração própria, por exemplo — a
        ordem de encontro é preservada e o rank é reescrito, com aviso: a
        série continua utilizável e o problema fica registrado no log.

        Fontes com `ranks_explicitos` (a Amazon estampa "#7" no card) mantêm
        a numeração original enquanto ela for única — reescrever ali criaria
        uma posição que a página nunca mostrou.
        """
        if not itens:
            return itens

        ranks = [i.rank for i in itens]
        duplicadas = len(ranks) - len(set(ranks))
        if duplicadas:
            logger.warning(
                f"[{self.nome}] {duplicadas} posição(ões) duplicada(s) na "
                "lista — ranking reescrito pela ordem de aparição."
            )
        elif self.ranks_explicitos:
            # A numeração impressa É o ranking: ordena por ela (estável, então
            # empates mantêm a ordem de aparição) e devolve como veio.
            return sorted(itens, key=lambda i: i.rank)

        # Sem numeração impressa, a ordem de aparição é o ranking.
        for novo, item in enumerate(itens, start=1):
            item.rank = novo
        return itens
