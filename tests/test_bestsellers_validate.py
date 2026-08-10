"""
tests/test_bestsellers_validate.py — Portões de qualidade da coleta.

O modo de falha que estes testes protegem: a coleta "funciona", produz linhas,
e mede a coisa errada. Uma lista de RELEVÂNCIA parseia exatamente como uma
lista de VENDAS — em 08/08/2026, pela relevância a Midea aparecia em 17 de 55
posições da Shopee (melhor rank #4); pela ordenação de vendas, 1 de 35
(rank #14). Sem o portão de ordenação, os dois números entram na mesma série.

Rode: pytest tests/test_bestsellers_validate.py
"""

import pandas as pd
import pytest

from bestsellers.models import BestSellerItem
from bestsellers.storage import to_dataframe
from bestsellers.config import SOURCES
from bestsellers.validate import (
    NIVEL_AVISO,
    NIVEL_ERRO,
    NIVEL_QUARENTENA,
    aplicar_quarentena,
    comparar_endpoints,
    plataformas_em_quarentena,
    validar,
)


def _coleta(plataforma: str, titulos, *, endpoint=None, data="2026-08-10", precos=None):
    """Monta um DataFrame de coleta a partir de uma lista de títulos."""
    spec = SOURCES[plataforma]
    endpoint = endpoint if endpoint is not None else spec.url_publica
    linhas = []
    for posicao, titulo in enumerate(titulos, start=1):
        preco = precos[posicao - 1] if precos else 1999.0
        item = BestSellerItem(rank=posicao, titulo=titulo, preco=preco)
        linhas.append(item.to_row(
            data=data, horario="09:30", run_id="run", spec=spec,
            url_coleta=spec.url_publica, endpoint=endpoint,
        ))
    return to_dataframe(linhas)


_SPLITS = [f"Ar Condicionado Split Inverter Marca{i} 12000 BTUs" for i in range(12)]


class TestPortaoDeOrdenacao:
    def test_endpoint_correto_passa(self):
        assert not [
            o for o in validar(_coleta("amazon", _SPLITS))
            if o.nivel == NIVEL_QUARENTENA
        ]

    def test_endpoint_sem_o_parametro_vai_para_quarentena(self):
        """Shopee sem sortBy=sales é relevância — universo diferente."""
        df = _coleta(
            "shopee", _SPLITS,
            endpoint="https://shopee.com.br/api/v4/search/search_items?by=relevancy",
        )
        ocorrencias = validar(df)
        assert plataformas_em_quarentena(ocorrencias) == ["shopee"]

    def test_quarentena_remove_a_plataforma_da_analise(self):
        ruim = _coleta("shopee", _SPLITS, endpoint="https://shopee.com.br/search?by=relevancy")
        boa = _coleta("amazon", _SPLITS)
        df = pd.concat([ruim, boa], ignore_index=True)

        limpo = aplicar_quarentena(df, validar(df))
        assert set(limpo["plataforma"]) == {"amazon"}

    def test_parametro_pode_vir_da_url_publica(self):
        """Coleta por página prova a ordenação pela URL; por API, pelo endpoint."""
        df = _coleta("amazon", _SPLITS, endpoint="")
        assert not plataformas_em_quarentena(validar(df))


class TestPortoesDeConteudo:
    def test_lista_truncada_vira_aviso(self):
        ocorrencias = validar(_coleta("amazon", _SPLITS[:4]))
        assert any("truncada" in o.mensagem for o in ocorrencias)

    def test_contaminacao_de_categoria(self):
        titulos = _SPLITS[:10] + [
            "Umidificador de Ar 3L", "Depurador de Ar Inox",
            "Ventilador de Coluna", "Climatizador Portátil",
        ]
        ocorrencias = validar(_coleta("casasbahia", titulos))
        assert any("fora do escopo" in o.mensagem for o in ocorrencias)

    def test_cobertura_de_preco_baixa(self):
        precos = [1999.0] * 3 + [None] * 9
        ocorrencias = validar(_coleta("amazon", _SPLITS, precos=precos))
        assert any("`preco`" in o.mensagem for o in ocorrencias)

    def test_posicao_duplicada_e_erro(self):
        df = _coleta("amazon", _SPLITS)
        df.loc[1, "rank"] = 1
        assert any(o.nivel == NIVEL_ERRO for o in validar(df))

    def test_plataforma_ausente_e_reportada(self):
        df = _coleta("amazon", _SPLITS)
        ocorrencias = validar(
            df, esperadas=["amazon", "shopee"], falhas={"shopee": "sessão expirada"}
        )
        ausentes = [o for o in ocorrencias if o.plataforma == "shopee"]
        assert ausentes and ausentes[0].nivel == NIVEL_AVISO
        assert "sessão expirada" in ausentes[0].mensagem

    def test_plataforma_nao_registrada(self):
        df = _coleta("amazon", _SPLITS)
        df["plataforma"] = "varejista_novo"
        assert any("não registrada" in o.mensagem for o in validar(df))


class TestComparacaoEntreDatas:
    def test_endpoint_estavel_nao_gera_ocorrencia(self):
        hoje = _coleta("amazon", _SPLITS, data="2026-08-17")
        antes = _coleta("amazon", _SPLITS, data="2026-08-10")
        assert comparar_endpoints(hoje, antes) == []

    def test_endpoint_diferente_exclui_a_plataforma_dos_deltas(self):
        hoje = _coleta("amazon", _SPLITS, data="2026-08-17")
        antes = _coleta(
            "amazon", _SPLITS, data="2026-08-10",
            endpoint="https://www.amazon.com.br/gp/bestsellers/home/OUTRO_NO/",
        )
        ocorrencias = comparar_endpoints(hoje, antes)
        assert len(ocorrencias) == 1
        assert "populações diferentes" in ocorrencias[0].mensagem.lower()


class TestCelulasVazias:
    def test_endpoint_nan_cai_no_fallback_da_url(self):
        """O NaN do pandas é TRUTHY: `str(x or "")` virava a string "nan", o
        fallback para `url_coleta` nunca disparava e uma lista boa (import do
        histórico manual, sem endpoint) ia para quarentena."""
        df = _coleta("amazon", _SPLITS)
        df["endpoint"] = float("nan")
        assert not plataformas_em_quarentena(validar(df))

    def test_endpoint_vazio_cai_no_fallback_da_url(self):
        df = _coleta("amazon", _SPLITS, endpoint="")
        assert not plataformas_em_quarentena(validar(df))

    def test_endpoint_nan_nos_dois_lados_nao_e_mudanca(self):
        hoje = _coleta("amazon", _SPLITS, data="2026-08-17")
        antes = _coleta("amazon", _SPLITS, data="2026-08-10")
        hoje["endpoint"] = float("nan")
        antes["endpoint"] = None
        assert comparar_endpoints(hoje, antes) == []


class TestPisoRelativoDeVolume:
    """
    O piso absoluto de 10 itens deixa passar uma Amazon com 12 de 30 — e o
    `pct_topN` desse dia acaba comparado com dias de lista cheia. O piso
    relativo ao `itens_esperados` declarado fecha essa porta.
    """

    def test_lista_pela_metade_e_reportada(self):
        # Amazon declara 30 itens esperados; 14 passa no piso absoluto.
        titulos = [f"Ar Condicionado Split Marca{i} 12000 BTUs" for i in range(14)]
        ocorrencias = validar(_coleta("amazon", titulos))
        assert any("truncada" in o.mensagem for o in ocorrencias)

    def test_lista_cheia_passa(self):
        titulos = [f"Ar Condicionado Split Marca{i} 12000 BTUs" for i in range(30)]
        ocorrencias = validar(_coleta("amazon", titulos))
        assert not any("truncada" in o.mensagem for o in ocorrencias)

    def test_lista_menor_por_natureza_passa(self):
        """O Mercado Livre tem 20 posições — não é truncamento."""
        titulos = [f"Ar Condicionado Split Marca{i} 12000 BTUs" for i in range(20)]
        ocorrencias = validar(_coleta("mercadolivre", titulos))
        assert not any("truncada" in o.mensagem for o in ocorrencias)
