"""
tests/test_bestsellers_amazon_leroy_regressao.py — trava as duas regressões
introduzidas nas PRs #322/#323 (26/08/2026) nas fontes Amazon e Leroy Merlin.

As duas falharam do MESMO jeito: código novo foi ANEXADO ao fim do módulo/classe
em vez de substituir o que já existia, e em Python a última definição vence em
silêncio. Nenhum teste, lint ou review pegou — por isso os portões aqui são
estruturais (AST e identidade de constante), não de comportamento: um parser
correto continua passando enquanto a definição duplicada existir.

1. **Leroy — a lista deixou de ser "mais vendidos".** Um segundo
   `_INDICE_MAIS_VENDIDOS = "production_products"` no fim do módulo sombreava a
   réplica de vendas. A Algolia ordena por RÉPLICA de índice, não por parâmetro
   de sort: `production_products` é a réplica PADRÃO (relevância). A coleta
   passaria a gravar relevância dentro da série `mais_vendidos` — a regra dura
   do CLAUDE.md ("as duas referências nunca se misturam") quebrada em silêncio.

2. **Leroy — o portão de ordenação foi desarmado junto.**
   `parametros_ordenacao` virou `("production_products",)`, que é SUBSTRING do
   nome da réplica de vendas. Como `ordenacao_comprovada` faz `in`, o portão
   passava a aceitar os dois índices — perdia justamente a capacidade de
   detectar a troca descrita em (1).

3. **Amazon — `_parse` duplicado.** Uma segunda cópia idêntica no fim da classe
   sombreava a original. Inerte hoje, mas qualquer correção futura na primeira
   nasceria morta.
"""

import ast
from collections import Counter
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parent.parent


def _metodos_duplicados(caminho: Path, classe: str):
    """Nomes de método definidos mais de uma vez no corpo da classe."""
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    corpo = next(
        n for n in ast.walk(arvore)
        if isinstance(n, ast.ClassDef) and n.name == classe
    ).body
    nomes = [
        n.name for n in corpo
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    return sorted(nome for nome, qtd in Counter(nomes).items() if qtd > 1)


def _atribuicoes_modulo(caminho: Path):
    """Contagem de atribuições de nome no nível do módulo."""
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    nomes = [
        alvo.id
        for no in arvore.body if isinstance(no, ast.Assign)
        for alvo in no.targets if isinstance(alvo, ast.Name)
    ]
    return Counter(nomes)


# ---------------------------------------------------------------------------
# Leroy Merlin — a lista precisa continuar sendo a de VENDAS
# ---------------------------------------------------------------------------

class TestLeroyIndiceDeVendas:
    def test_indice_e_a_replica_de_vendas(self):
        from bestsellers.sources import leroy_merlin as lm

        assert lm._INDICE_MAIS_VENDIDOS == "production_products_most_sales"

    def test_indice_nao_e_a_replica_padrao_de_relevancia(self):
        """`production_products` (sem sufixo) é relevância, não venda."""
        from bestsellers.sources import leroy_merlin as lm

        assert lm._INDICE_MAIS_VENDIDOS != "production_products"

    def test_url_algolia_aponta_para_a_replica_de_vendas(self):
        from bestsellers.sources import leroy_merlin as lm

        assert f"/1/indexes/{lm._INDICE_MAIS_VENDIDOS}/query" in lm._URL_ALGOLIA

    @pytest.mark.parametrize("nome", ["_INDICE_MAIS_VENDIDOS", "_URL_ALGOLIA"])
    def test_constante_definida_uma_unica_vez(self, nome):
        """
        Uma segunda atribuição no fim do módulo sombreia a primeira sem erro
        nenhum — foi assim que a coleta trocou de índice em 26/08/2026.
        """
        contagem = _atribuicoes_modulo(
            _RAIZ / "bestsellers" / "sources" / "leroy_merlin.py"
        )
        assert contagem[nome] == 1, (
            f"{nome} definido {contagem[nome]}x no módulo — a última definição "
            "vence em silêncio e troca o índice coletado."
        )


class TestLeroyPortaoDeOrdenacao:
    """O portão de `validate.py` precisa distinguir os dois índices."""

    @property
    def _spec(self):
        from bestsellers.config import SOURCES

        return SOURCES["leroymerlin"]

    def test_endpoint_real_comprova_a_ordenacao(self):
        from bestsellers.sources import leroy_merlin as lm

        assert self._spec.ordenacao_comprovada(lm._URL_ALGOLIA)

    def test_replica_de_relevancia_nao_comprova_ordenacao(self):
        """
        A regressão que este teste existe para pegar: com
        `parametros_ordenacao=("production_products",)` o portão aceitava
        também a réplica de relevância, porque a checagem é `in`.
        """
        url_relevancia = (
            "https://1CF3ZT43ZU-dsn.algolia.net"
            "/1/indexes/production_products/query"
            "?x-algolia-application-id=1CF3ZT43ZU"
        )
        assert not self._spec.ordenacao_comprovada(url_relevancia)

    def test_nenhum_parametro_e_prefixo_generico_demais(self):
        """
        Nenhuma das grafias aceitas pode casar a réplica de relevância — senão
        o portão volta a passar para os dois índices.
        """
        for parametro in self._spec.parametros_ordenacao:
            assert parametro not in "/1/indexes/production_products/query", (
                f'"{parametro}" também casa a réplica de relevância'
            )

    def test_referencia_segue_sendo_mais_vendidos(self):
        from bestsellers.config import REFERENCIA_MAIS_VENDIDOS

        assert self._spec.referencia == REFERENCIA_MAIS_VENDIDOS
        assert self._spec.eh_mais_vendidos


# ---------------------------------------------------------------------------
# Amazon — sem método sombreado na classe
# ---------------------------------------------------------------------------

class TestAmazonSemMetodoDuplicado:
    def test_classe_nao_tem_metodo_definido_duas_vezes(self):
        duplicados = _metodos_duplicados(
            _RAIZ / "bestsellers" / "sources" / "amazon.py", "AmazonBestSellers"
        )
        assert duplicados == [], (
            f"métodos duplicados em AmazonBestSellers: {duplicados} — a última "
            "definição vence e a primeira vira código morto."
        )

    def test_parse_continua_extraindo_a_lista(self):
        """A remoção da cópia não pode ter levado o `_parse` bom junto."""
        from bestsellers.sources.amazon import AmazonBestSellers

        html = "".join(
            f'<div id="gridItemRoot">'
            f'<span class="zg-bdg-text">#{i}</span>'
            f'<a class="a-link-normal" href="/dp/B0TEST{i:04d}">'
            f'<span><div class="p13n-sc-truncated">Ar Condicionado Midea {i}</div></span></a>'
            f'</div>'
            for i in range(1, 7)
        )
        itens = AmazonBestSellers()._parse(f"<html><body>{html}</body></html>", offset=0)
        assert len(itens) == 6
        assert itens[0].rank == 1
        assert itens[0].sku_plataforma == "B0TEST0001"

    def test_pdp_de_seller_continua_desligado_por_padrao(self, monkeypatch):
        """
        A resolução via PDP (#322) é opt-in: ligada por engano ela multiplica
        as requisições na plataforma mais agressiva em anti-bot.
        """
        from utils.amazon_sellers import ENV_ENABLED, resolution_enabled

        monkeypatch.delenv(ENV_ENABLED, raising=False)
        assert not resolution_enabled()


class TestLeroySemMetodoDuplicado:
    def test_classe_nao_tem_metodo_definido_duas_vezes(self):
        duplicados = _metodos_duplicados(
            _RAIZ / "bestsellers" / "sources" / "leroy_merlin.py",
            "LeroyMerlinBestSellers",
        )
        assert duplicados == []
