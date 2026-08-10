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

from bestsellers.config import SOURCES, SourceSpec
from bestsellers.models import BestSellerItem
from scrapers.base import BaseScraper


class PlainBrowser(BaseScraper):
    """
    Host de browser sem lógica de busca.

    Existe para reaproveitar o ciclo de vida do Playwright do
    `BaseScraper` (stealth, rotação de UA, locale pt-BR, timezone de São
    Paulo, dump de HTML de debug) nas listas que só precisam de um `goto`
    numa URL fixa. `search()` é intencionalmente vazio: esta classe navega,
    quem interpreta é a fonte.
    """

    def __init__(self, platform_name: str, headless: bool = True) -> None:
        self.platform_name = platform_name
        super().__init__(headless=headless)

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
        """
        vistos: Dict[str, BestSellerItem] = {}
        ordenados = sorted(itens, key=lambda i: i.rank)
        for item in ordenados:
            chave = item.sku_plataforma or (item.titulo or "").strip().lower()
            if not chave:
                continue
            if chave not in vistos:
                vistos[chave] = item
        return sorted(vistos.values(), key=lambda i: i.rank)

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
            return itens

        for novo, item in enumerate(itens, start=1):
            item.rank = novo
        return itens
