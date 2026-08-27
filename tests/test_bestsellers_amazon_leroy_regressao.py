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


def _contar_atribuicoes(fonte: str):
    """
    Contagem de atribuições de nome no nível do módulo.

    Conta as DUAS sintaxes que ligam um valor a um nome: `X = ...` (`ast.Assign`)
    e `X: str = ...` (`ast.AnnAssign` com valor). Contar só a primeira deixaria
    o guard cego justamente ao caso misto — original anotado, duplicata anexada
    sem anotação — em que o sombreamento acontece e a contagem daria 1.

    A anotação SEM valor (`X: str`) fica de fora de propósito: ela declara um
    tipo e não liga valor nenhum, então não sombreia ninguém e contá-la seria
    falso positivo.
    """
    arvore = ast.parse(fonte)
    nomes = [
        alvo.id
        for no in arvore.body if isinstance(no, ast.Assign)
        for alvo in no.targets if isinstance(alvo, ast.Name)
    ]
    nomes += [
        no.target.id
        for no in arvore.body
        if isinstance(no, ast.AnnAssign)
        and no.value is not None
        and isinstance(no.target, ast.Name)
    ]
    return Counter(nomes)


def _atribuicoes_modulo(caminho: Path):
    """Contagem de atribuições de nome no nível do módulo, a partir do arquivo."""
    return _contar_atribuicoes(caminho.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# O guard de duplicata precisa enxergar as duas sintaxes de atribuição
# ---------------------------------------------------------------------------

class TestGuardDeAtribuicao:
    """
    Achado do cubic na revisão desta PR: contar só `ast.Assign` deixava o guard
    cego ao caso misto (original anotado + duplicata anexada sem anotação), que
    é exatamente o sombreamento que estes testes existem para pegar.
    """

    def test_pega_duplicata_entre_anotada_e_simples(self):
        contagem = _contar_atribuicoes('_X: str = "bom"\n_X = "sombreado"\n')
        assert contagem["_X"] == 2

    def test_pega_duplicata_entre_simples_e_anotada(self):
        contagem = _contar_atribuicoes('_X = "bom"\n_X: str = "sombreado"\n')
        assert contagem["_X"] == 2

    def test_pega_duplicata_entre_duas_anotadas(self):
        contagem = _contar_atribuicoes('_X: str = "bom"\n_X: str = "sombreado"\n')
        assert contagem["_X"] == 2

    def test_anotacao_sem_valor_nao_conta(self):
        """`_X: str` declara tipo e não liga valor — não sombreia ninguém."""
        contagem = _contar_atribuicoes('_X: str\n_X = "unico"\n')
        assert contagem["_X"] == 1

    def test_definicao_unica_continua_valendo_um(self):
        assert _contar_atribuicoes('_X = "unico"\n')["_X"] == 1


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



class TestLeroySemMetodoDuplicado:
    def test_classe_nao_tem_metodo_definido_duas_vezes(self):
        duplicados = _metodos_duplicados(
            _RAIZ / "bestsellers" / "sources" / "leroy_merlin.py",
            "LeroyMerlinBestSellers",
        )
        assert duplicados == []


# ---------------------------------------------------------------------------
# Amazon — buy box lida do PDP a cada execução
# ---------------------------------------------------------------------------

def _fonte_amazon(monkeypatch, respostas):
    """
    Uma `AmazonBestSellers` com `_fetch_pdp_seller` trocado por um dublê.

    `respostas` é um dict ASIN → nome (ou None para "PDP não revelou").
    Devolve a fonte e a lista de ASINs efetivamente abertos, na ordem.
    """
    from bestsellers.sources.amazon import AmazonBestSellers

    fonte = AmazonBestSellers()
    abertos = []

    def _dublê(asin):
        abertos.append(asin)
        return respostas.get(asin)

    monkeypatch.setattr(fonte, "_fetch_pdp_seller", _dublê)
    return fonte, abertos


def _item(rank, asin):
    from bestsellers.models import BestSellerItem

    return BestSellerItem(
        rank=rank, titulo=f"Ar Condicionado Midea {rank}", sku_plataforma=asin
    )


class TestAmazonEnvDoPdp:
    def test_ligado_por_padrao(self, monkeypatch):
        from bestsellers.sources.amazon import _ENV_PDP, pdp_habilitado

        monkeypatch.delenv(_ENV_PDP, raising=False)
        assert pdp_habilitado()

    @pytest.mark.parametrize("valor", ["0", "false", "no", "off", "OFF", " 0 "])
    def test_desliga_com_valor_explicito(self, monkeypatch, valor):
        from bestsellers.sources.amazon import _ENV_PDP, pdp_habilitado

        monkeypatch.setenv(_ENV_PDP, valor)
        assert not pdp_habilitado()

    def test_valor_invalido_mantem_ligado(self, monkeypatch):
        """Lixo na variável não pode desligar a leitura da buy box em silêncio."""
        from bestsellers.sources.amazon import _ENV_PDP, pdp_habilitado

        monkeypatch.setenv(_ENV_PDP, "talvez")
        assert pdp_habilitado()

    def test_sem_teto_por_padrao(self, monkeypatch):
        from bestsellers.sources.amazon import _ENV_PDP_BUDGET, pdp_teto

        monkeypatch.delenv(_ENV_PDP_BUDGET, raising=False)
        assert pdp_teto() is None

    def test_teto_explicito(self, monkeypatch):
        from bestsellers.sources.amazon import _ENV_PDP_BUDGET, pdp_teto

        monkeypatch.setenv(_ENV_PDP_BUDGET, "12")
        assert pdp_teto() == 12


class TestAmazonBuyBoxPorExecucao:
    """
    `seller` é a OBSERVAÇÃO DO DIA, não um atributo fixo do produto.

    A lista é diária e o que se quer medir é a buy box mudando de dono, então
    cada execução tem de reabrir os PDPs. Ler de um cache entre execuções
    congelaria o vencedor da primeira leitura e a série passaria a registrar,
    do 2º dia em diante, um vencedor que ninguém observou.
    """

    def test_resolve_todos_os_itens_da_lista(self, monkeypatch):
        respostas = {f"B0AAAA{i:04d}": f"Loja {i}" for i in range(1, 31)}
        fonte, abertos = _fonte_amazon(monkeypatch, respostas)
        itens = [_item(i, asin) for i, asin in enumerate(respostas, start=1)]

        fonte._resolve_sellers_via_pdp(itens)

        assert len(abertos) == 30, "todo item do ranking precisa de um PDP"
        assert all(i.seller for i in itens)

    def test_nao_le_cache_entre_execucoes(self, monkeypatch):
        """
        O requisito da recorrência: a 2ª execução reabre os mesmos PDPs.
        """
        respostas = {"B0AAAA0001": "Loja A"}
        fonte, abertos = _fonte_amazon(monkeypatch, respostas)

        primeira = [_item(1, "B0AAAA0001")]
        fonte._resolve_sellers_via_pdp(primeira)

        respostas["B0AAAA0001"] = "Loja B"  # a buy box trocou de dono
        segunda = [_item(1, "B0AAAA0001")]
        fonte._resolve_sellers_via_pdp(segunda)

        assert abertos == ["B0AAAA0001", "B0AAAA0001"]
        assert primeira[0].seller == "Loja A"
        assert segunda[0].seller == "Loja B", (
            "a 2ª execução devolveu o seller da 1ª — a buy box do dia estaria "
            "congelada na primeira leitura"
        )

    def test_fonte_nao_usa_o_cache_persistente(self):
        """
        `AmazonSellerCache.get()` não tem validade: uma vez resolvido, o ASIN
        devolve o mesmo vendedor para sempre. Serve ao scraper principal, não
        a uma série diária de buy box.
        """
        arvore = ast.parse(
            (_RAIZ / "bestsellers" / "sources" / "amazon.py").read_text(
                encoding="utf-8"
            )
        )
        importados = {
            alias.name
            for no in ast.walk(arvore)
            if isinstance(no, ast.ImportFrom) and no.module == "utils.amazon_sellers"
            for alias in no.names
        }
        assert importados == {"extract_seller_from_pdp"}, (
            "a fonte só pode importar o EXTRATOR de HTML; o cache persistente "
            f"e seus helpers de orçamento não entram aqui: {importados}"
        )

    def test_asin_repetido_custa_um_pdp_so(self, monkeypatch):
        """Mesma execução, mesmo ASIN = mesma observação; um PDP basta."""
        fonte, abertos = _fonte_amazon(monkeypatch, {"B0AAAA0001": "Loja A"})
        itens = [_item(1, "B0AAAA0001"), _item(2, "B0AAAA0001")]

        fonte._resolve_sellers_via_pdp(itens)

        assert abertos == ["B0AAAA0001"]
        assert [i.seller for i in itens] == ["Loja A", "Loja A"]

    def test_pdp_sem_seller_deixa_o_campo_vazio(self, monkeypatch):
        """Campo sem dado fica vazio — nunca herdado de outro item ou dia."""
        fonte, _ = _fonte_amazon(
            monkeypatch, {"B0AAAA0001": "Loja A", "B0AAAA0002": None}
        )
        itens = [_item(1, "B0AAAA0001"), _item(2, "B0AAAA0002")]

        fonte._resolve_sellers_via_pdp(itens)

        assert itens[0].seller == "Loja A"
        assert itens[1].seller is None

    def test_desligado_nao_abre_nenhum_pdp(self, monkeypatch):
        from bestsellers.sources.amazon import _ENV_PDP

        monkeypatch.setenv(_ENV_PDP, "0")
        fonte, abertos = _fonte_amazon(monkeypatch, {"B0AAAA0001": "Loja A"})
        itens = [_item(1, "B0AAAA0001")]

        fonte._resolve_sellers_via_pdp(itens)

        assert abertos == []
        assert itens[0].seller is None

    def test_teto_trunca_e_o_resto_fica_sem_seller(self, monkeypatch):
        from bestsellers.sources.amazon import _ENV_PDP_BUDGET

        monkeypatch.setenv(_ENV_PDP_BUDGET, "2")
        respostas = {f"B0AAAA{i:04d}": f"Loja {i}" for i in range(1, 6)}
        fonte, abertos = _fonte_amazon(monkeypatch, respostas)
        itens = [_item(i, asin) for i, asin in enumerate(respostas, start=1)]

        fonte._resolve_sellers_via_pdp(itens)

        assert len(abertos) == 2
        assert [bool(i.seller) for i in itens] == [True, True, False, False, False]

    def test_aborta_apos_falhas_seguidas(self, monkeypatch):
        """
        PDP após PDP sem "Vendido por" é layout novo ou bloqueio, não produto
        fora do ar — varrer a lista inteira só gasta requisição à toa.
        """
        from bestsellers.sources.amazon import _MAX_FALHAS_SEGUIDAS

        respostas = {f"B0AAAA{i:04d}": None for i in range(1, 21)}
        fonte, abertos = _fonte_amazon(monkeypatch, respostas)
        itens = [_item(i, asin) for i, asin in enumerate(respostas, start=1)]

        fonte._resolve_sellers_via_pdp(itens)

        assert len(abertos) == _MAX_FALHAS_SEGUIDAS
        assert all(i.seller is None for i in itens)

    def test_falha_isolada_nao_aborta(self, monkeypatch):
        """Um produto sem seller no meio da lista não pode derrubar o resto."""
        respostas = {f"B0AAAA{i:04d}": (None if i == 2 else f"Loja {i}")
                     for i in range(1, 11)}
        fonte, abertos = _fonte_amazon(monkeypatch, respostas)
        itens = [_item(i, asin) for i, asin in enumerate(respostas, start=1)]

        fonte._resolve_sellers_via_pdp(itens)

        assert len(abertos) == 10
        assert sum(1 for i in itens if i.seller) == 9

    def test_coletar_dispara_a_resolucao(self, monkeypatch):
        """A resolução tem de estar no caminho da coleta, não só disponível."""
        from bestsellers.sources.amazon import AmazonBestSellers

        chamou = []
        fonte = AmazonBestSellers()
        monkeypatch.setattr(fonte, "_baixar", lambda url, pagina: "<html></html>")
        monkeypatch.setattr(fonte, "_parse", lambda html, offset: [_item(1, "B0AAAA0001")])
        monkeypatch.setattr(
            fonte, "_resolve_sellers_via_pdp", lambda itens: chamou.append(len(itens))
        )

        fonte._coletar(paginas=1)

        assert chamou == [1]
