"""
tests/test_leroy_ordenacao.py — prova de ordenação da Leroy lida DA TELA.

O fixture `tests/fixtures/leroy_ordenar_por.html` é o markup REAL do controle
"Ordenar por", copiado da prateleira Split Inverter pelo mantenedor em
27/08/2026. Testar contra markup inventado é o erro que este repositório já
documenta — as classes da Leroy são Tailwind de valor arbitrário com hash
(`radix-:R10mqcvffff4qdkq:`) e mudam a cada build.

O que estes testes travam: a leitura ancora em SEMÂNTICA (o rótulo visível e o
`role="combobox"`), nunca em classe; e "não consegui ler" jamais vira "está
ordenado por vendas".
"""

from pathlib import Path

import pytest

from bestsellers.sources.leroy_ordenacao import (
    ORDENACAO_MAIS_VENDIDOS,
    normalizar,
    ordenacao_da_pagina,
    ordenado_por_vendas,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "leroy_ordenar_por.html"


def _combobox(valor, rotulo="Ordenar por", extra=""):
    """Um cartão de ordenação mínimo, com o valor pedido."""
    return (
        f"<div><div><span>{rotulo}</span></div>"
        f'<div><button type="button" role="combobox" {extra}>'
        f"<span>{valor}</span><svg><path d=\"M1 2\"></path></svg>"
        f"</button></div></div>"
    )


class TestMarkupReal:
    """Contra o HTML que o mantenedor copiou da página."""

    @pytest.fixture
    def html(self):
        return _FIXTURE.read_text(encoding="utf-8")

    def test_le_mais_vendidos_do_markup_real(self, html):
        assert ordenacao_da_pagina(html) == "Mais vendidos"

    def test_markup_real_prova_ordenacao_por_vendas(self, html):
        assert ordenado_por_vendas(html)

    def test_nao_depende_de_classe(self, html):
        """
        Removidas TODAS as classes, a leitura tem de continuar funcionando —
        é a diferença entre âncora semântica e âncora de estilo.
        """
        import re

        sem_classe = re.sub(r'\sclass="[^"]*"', "", html)
        assert ordenacao_da_pagina(sem_classe) == "Mais vendidos"

    def test_nao_depende_do_id_gerado_do_radix(self, html):
        sem_id = html.replace("radix-:R10mqcvffff4qdkq:", "radix-:OUTRO:")
        assert ordenacao_da_pagina(sem_id) == "Mais vendidos"


class TestOutrasOrdenacoes:
    """A leitura tem de distinguir "Mais vendidos" das demais."""

    @pytest.mark.parametrize(
        "valor,esperado",
        [
            ("Mais vendidos", "Mais vendidos"),
            ("Relevância", "Relevância"),
            ("Menor preço", "Menor preço"),
            ("Maior preço", "Maior preço"),
            ("Mais recentes", "Mais recentes"),
            ("Maior desconto", "Maior desconto"),
        ],
    )
    def test_le_o_rotulo_selecionado(self, valor, esperado):
        assert ordenacao_da_pagina(_combobox(valor)) == esperado

    @pytest.mark.parametrize(
        "valor", ["Relevância", "Menor preço", "Maior preço", "Mais recentes"]
    )
    def test_ordenacao_que_nao_e_venda_nao_prova_nada(self, valor):
        """
        O caso que importa: a página ordenada por relevância NÃO pode passar
        pela prova de vendas. Foi misturar essas duas referências que quebrou
        a coleta em 26/08/2026.
        """
        assert not ordenado_por_vendas(_combobox(valor))

    def test_variacao_de_caixa_e_acento_ainda_prova(self):
        assert ordenado_por_vendas(_combobox("MAIS VENDIDOS"))
        assert ordenado_por_vendas(_combobox("mais   vendidos"))

    def test_espaco_nao_quebravel_ainda_prova(self):
        """`\\xa0` entre as palavras é comum em render de SPA."""
        assert ordenado_por_vendas(_combobox("Mais\xa0vendidos"))


class TestAusenciaDeProva:
    """Não conseguir ler é "não sei" — nunca "está ordenado por vendas"."""

    @pytest.mark.parametrize("html", ["", "<html><body></body></html>"])
    def test_pagina_sem_controle_devolve_none(self, html):
        assert ordenacao_da_pagina(html) is None
        assert not ordenado_por_vendas(html)

    def test_html_vazio_nao_explode(self):
        assert ordenacao_da_pagina(None) is None

    def test_placeholder_do_radix_nao_conta_como_valor(self):
        """`data-placeholder` é "nada selecionado", não uma ordenação."""
        html = _combobox("Selecione", extra='data-placeholder=""')
        assert ordenacao_da_pagina(html) is None
        assert not ordenado_por_vendas(html)

    def test_pagina_de_bloqueio_nao_prova_nada(self):
        html = "<html><body><h1>Access Denied</h1></body></html>"
        assert not ordenado_por_vendas(html)


class TestComboboxDeFiltroNaoConfunde:
    """A prateleira tem outros selects; só o de ordenação vale."""

    def test_ignora_select_de_filtro_e_pega_o_de_ordenacao(self):
        filtro = (
            "<div><div><span>Marca</span></div>"
            '<div><button role="combobox"><span>Midea</span></button></div></div>'
        )
        html = filtro + _combobox("Mais vendidos")
        assert ordenacao_da_pagina(html) == "Mais vendidos"

    def test_sem_o_rotulo_o_fallback_exige_valor_reconhecido(self):
        """
        Sem "Ordenar por" na página, só um VALOR que seja ordenação conhecida
        é aceito — senão o select de marca viraria "ordenação".
        """
        so_filtro = '<button role="combobox"><span>Midea</span></button>'
        assert ordenacao_da_pagina(so_filtro) is None

        com_ordenacao = (
            '<button role="combobox"><span>Midea</span></button>'
            '<button role="combobox"><span>Mais vendidos</span></button>'
        )
        assert ordenacao_da_pagina(com_ordenacao) == "Mais vendidos"


class TestNormalizar:
    @pytest.mark.parametrize(
        "entrada,esperado",
        [
            ("  Mais   Vendidos ", "mais vendidos"),
            ("Relevância", "relevancia"),
            ("Menor\xa0preço", "menor preco"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_normalizacao(self, entrada, esperado):
        assert normalizar(entrada) == esperado

    def test_constante_bate_o_rotulo_da_leroy(self):
        assert ORDENACAO_MAIS_VENDIDOS == "Mais vendidos"
