"""
tests/test_bestsellers_metrics.py — Motor de métricas de mais vendidos.

Estes testes travam as REGRAS DURAS da rotina como comportamento, não como
comentário:

  * comparação diária só entre o mesmo dia da semana (sábado promocional
    contra segunda produziu +8,6% de "alta de preço" que era calendário);
  * agregação semanal/mensal é MÉDIA das leituras, nunca soma — posição é
    ordinal;
  * `vendidos` mensal e acumulado jamais entram na mesma soma;
  * menos de 3 leituras não vira tendência.

Rode: pytest tests/test_bestsellers_metrics.py
"""

import pandas as pd
import pytest

from bestsellers import metrics
from bestsellers.config import SOURCES
from bestsellers.models import BestSellerItem
from bestsellers.storage import to_dataframe


def _lista(plataforma: str, data: str, marcas, *, precos=None, sku_prefixo="sku"):
    """Uma lista de ranking onde cada posição recebe a marca informada."""
    spec = SOURCES[plataforma]
    linhas = []
    for posicao, marca in enumerate(marcas, start=1):
        titulo = f"Ar Condicionado Split Inverter {marca} 12000 BTUs"
        item = BestSellerItem(
            rank=posicao,
            titulo=titulo,
            preco=(precos or {}).get(posicao, 2000.0),
            sku_plataforma=f"{sku_prefixo}-{marca}-{posicao}",
        )
        linhas.append(item.to_row(
            data=data, horario="09:30", run_id="run", spec=spec,
            url_coleta=spec.url_publica, endpoint=spec.url_publica,
        ))
    return to_dataframe(linhas)


# 4 posições Midea em 10 → 40%, melhor posição #1 (baseline Magalu de 10/08).
_TOP10_40PCT = ["Midea", "LG", "Springer", "Samsung", "Carrier", "LG", "Gree",
                "Comfee", "LG", "Samsung"]
# 1 posição Midea em 10 → 10%, melhor posição #5.
_TOP10_10PCT = ["LG", "LG", "Samsung", "Gree", "Midea", "LG", "Samsung",
                "Gree", "Consul", "LG"]


class TestKpiTopo:
    def test_percentual_e_melhor_posicao(self):
        kpi = metrics.kpi_topo(metrics.preparar(_lista("magazineluiza", "2026-08-10", _TOP10_40PCT)))
        linha = kpi.iloc[0]
        assert linha["pct_topN"] == 40.0
        assert linha["melhor_rank"] == 1
        assert linha["n_posicoes"] == 4

    def test_denominador_e_publicado(self):
        """Numa lista contaminada '50%' pode ser 1 split em 2 — o denominador
        muda a leitura e por isso viaja junto do percentual."""
        titulos = ["Ar Condicionado Split Midea 12000", "Umidificador de Ar 3L",
                   "Depurador de Ar Inox", "Ventilador de Coluna"]
        spec = SOURCES["casasbahia"]
        linhas = [
            BestSellerItem(rank=i, titulo=t).to_row(
                data="2026-08-10", horario="09:30", run_id=None, spec=spec,
                url_coleta=spec.url_publica, endpoint=spec.url_publica,
            )
            for i, t in enumerate(titulos, start=1)
        ]
        kpi = metrics.kpi_topo(metrics.preparar(to_dataframe(linhas)))
        assert kpi.iloc[0]["itens_topN"] == 1     # só 1 split entre as 4 posições
        assert kpi.iloc[0]["pct_topN"] == 100.0

    def test_itens_fora_de_escopo_nao_entram_no_kpi(self):
        marcas = _TOP10_40PCT
        df = _lista("amazon", "2026-08-10", marcas)
        # Um portátil não conta para o KPI de split hi-wall.
        extra = BestSellerItem(rank=11, titulo="Ar Condicionado Portátil Midea 12000")
        df = pd.concat([df, to_dataframe([extra.to_row(
            data="2026-08-10", horario="09:30", run_id=None,
            spec=SOURCES["amazon"], url_coleta="u", endpoint="e",
        )])], ignore_index=True)

        kpi = metrics.kpi_topo(metrics.preparar(df))
        assert kpi.iloc[0]["itens"] == 10


class TestBaseDeComparacao:
    def test_prefere_o_mesmo_dia_da_semana(self):
        """2026-08-17 é segunda; 08-10 também. 08-15 (sábado) é mais recente
        mas comparar sábado com segunda mistura calendário com competição."""
        historico = metrics.preparar(pd.concat([
            _lista("amazon", "2026-08-10", _TOP10_40PCT),
            _lista("amazon", "2026-08-15", _TOP10_10PCT),
        ], ignore_index=True))

        base, mesmo_dia, intervalo = metrics.escolher_base_comparacao(
            historico, "2026-08-17"
        )
        assert mesmo_dia is True
        assert intervalo == 7
        assert pd.to_datetime(base["data"].iloc[0]).strftime("%Y-%m-%d") == "2026-08-10"

    def test_sem_mesmo_weekday_marca_como_nao_comparavel(self):
        historico = metrics.preparar(_lista("amazon", "2026-08-15", _TOP10_10PCT))
        base, mesmo_dia, intervalo = metrics.escolher_base_comparacao(
            historico, "2026-08-17"
        )
        assert base is not None
        assert mesmo_dia is False
        assert intervalo == 2

    def test_sem_historico(self):
        vazio = metrics.preparar(pd.DataFrame(columns=_lista("amazon", "2026-08-10", ["LG"]).columns))
        assert metrics.escolher_base_comparacao(vazio, "2026-08-17") == (None, False, None)


class TestDeltaDiario:
    def test_ganho_e_perda_em_pontos_percentuais(self):
        hoje = metrics.preparar(_lista("magazineluiza", "2026-08-17", _TOP10_10PCT))
        base = metrics.preparar(_lista("magazineluiza", "2026-08-10", _TOP10_40PCT))

        delta = metrics.delta_diario(hoje, base)
        assert delta.iloc[0]["delta_pp"] == -30.0


class TestSerieAgregada:
    def _historico_tres_semanas(self):
        # Três segundas consecutivas, share caindo: 40% → 30% → 10%.
        meio = ["Midea", "LG", "Springer", "Samsung", "LG", "LG", "Gree",
                "Comfee", "LG", "Samsung"]  # 3 posições → 30%
        return metrics.preparar(pd.concat([
            _lista("magazineluiza", "2026-08-03", _TOP10_40PCT),
            _lista("magazineluiza", "2026-08-10", meio),
            _lista("magazineluiza", "2026-08-17", _TOP10_10PCT),
        ], ignore_index=True))

    def test_agregacao_semanal_e_media(self):
        historico = self._historico_tres_semanas()
        serie = metrics.serie_agregada(historico, metrics.PERIODO_SEMANAL)
        # Cada semana tem 1 leitura: a média é o próprio valor do dia.
        assert list(serie["pct_topN_medio"]) == [40.0, 30.0, 10.0]
        assert len(serie) == 3

    def test_uma_leitura_nao_vira_tendencia(self):
        serie = metrics.serie_agregada(
            self._historico_tres_semanas(), metrics.PERIODO_SEMANAL
        )
        assert not serie["tendencia_valida"].any()

    def test_tres_leituras_no_mesmo_periodo_validam_a_tendencia(self):
        historico = metrics.preparar(pd.concat([
            _lista("amazon", "2026-08-03", _TOP10_40PCT),
            _lista("amazon", "2026-08-04", _TOP10_40PCT),
            _lista("amazon", "2026-08-05", _TOP10_10PCT),
        ], ignore_index=True))
        serie = metrics.serie_agregada(historico, metrics.PERIODO_SEMANAL)
        assert serie.iloc[0]["n_leituras"] == 3
        assert bool(serie.iloc[0]["tendencia_valida"]) is True
        assert serie.iloc[0]["pct_topN_medio"] == 30.0  # (40+40+10)/3

    def test_agregacao_mensal(self):
        historico = metrics.preparar(pd.concat([
            _lista("amazon", "2026-07-06", _TOP10_40PCT),
            _lista("amazon", "2026-08-03", _TOP10_10PCT),
        ], ignore_index=True))
        serie = metrics.serie_agregada(historico, metrics.PERIODO_MENSAL)
        assert list(serie["periodo"]) == ["2026-07", "2026-08"]

    def test_periodo_invalido(self):
        with pytest.raises(ValueError, match="Período inválido"):
            metrics.serie_agregada(self._historico_tres_semanas(), "trimestral")


class TestVariacaoPeriodo:
    def test_direcao_de_ganho_e_perda(self):
        historico = metrics.preparar(pd.concat([
            _lista("magazineluiza", "2026-08-03", _TOP10_10PCT),
            _lista("magazineluiza", "2026-08-10", _TOP10_40PCT),
            _lista("magazineluiza", "2026-08-17", _TOP10_10PCT),
        ], ignore_index=True))
        variacao = metrics.variacao_periodo(
            metrics.serie_agregada(historico, metrics.PERIODO_SEMANAL)
        )
        assert list(variacao["direcao"]) == ["sem base", "ganho", "perda"]
        assert list(variacao["delta_pp"])[1:] == [30.0, -30.0]

    def test_plataformas_nao_se_misturam(self):
        """Cada plataforma tem sua própria série: o delta de uma não pode
        vazar para a outra."""
        historico = metrics.preparar(pd.concat([
            _lista("amazon", "2026-08-03", _TOP10_40PCT),
            _lista("magazineluiza", "2026-08-10", _TOP10_10PCT),
        ], ignore_index=True))
        variacao = metrics.variacao_periodo(
            metrics.serie_agregada(historico, metrics.PERIODO_SEMANAL)
        )
        assert set(variacao["direcao"]) == {"sem base"}


class TestVelocidadeDeclarada:
    def _com_vendidos(self, plataforma, base, quantidades, data="2026-08-10"):
        spec = SOURCES[plataforma]
        linhas = []
        for posicao, (marca, quantidade) in enumerate(quantidades, start=1):
            item = BestSellerItem(
                rank=posicao,
                titulo=f"Ar Condicionado Split {marca} 12000 BTUs",
                vendidos=quantidade,
                base_vendidos=base,
            )
            linhas.append(item.to_row(
                data=data, horario="09:30", run_id=None, spec=spec,
                url_coleta="u", endpoint="e",
            ))
        return to_dataframe(linhas)

    def test_soma_apenas_a_base_mensal(self):
        mensal = self._com_vendidos("shopee", "mes", [("Midea", 900), ("LG", 100)])
        acumulado = self._com_vendidos(
            "mercadolivre", "acumulado", [("Samsung", 50_000)]
        )
        df = metrics.preparar(pd.concat([mensal, acumulado], ignore_index=True))

        velocidade = metrics.velocidade_declarada(df)
        assert set(velocidade["marca"]) == {"Midea", "LG"}
        assert velocidade["un_mes"].sum() == 1000
        assert velocidade[velocidade["marca"] == "Midea"]["pct"].iloc[0] == 90.0

    def test_sem_base_mensal_devolve_vazio(self):
        df = metrics.preparar(
            self._com_vendidos("mercadolivre", "acumulado", [("Midea", 5000)])
        )
        assert not len(metrics.velocidade_declarada(df))


class TestEstabilidade:
    def test_ranking_parado_e_curadoria_suspeita(self):
        """Leroy: 41% dos itens na mesma posição em 48h, contra 12–19% nas
        demais. Ranking de venda real se mexe."""
        marcas = _TOP10_40PCT
        antes = metrics.preparar(_lista("leroymerlin", "2026-08-08", marcas))
        hoje = metrics.preparar(_lista("leroymerlin", "2026-08-10", marcas))

        tabela = metrics.estabilidade(hoje, antes)
        assert tabela.iloc[0]["rank_inalterado_pct"] == 100
        assert tabela.iloc[0]["veredito"] == "CURADORIA SUSPEITA"

    def test_ranking_que_se_mexe(self):
        antes = metrics.preparar(_lista("amazon", "2026-08-08", _TOP10_40PCT))
        hoje = metrics.preparar(
            _lista("amazon", "2026-08-10", list(reversed(_TOP10_40PCT)))
        )
        # sku_plataforma inclui a posição, então invertê-la muda a chave;
        # o pareamento cai no título, que é o que sobra na vida real.
        tabela = metrics.estabilidade(hoje, antes)
        if len(tabela):
            assert tabela.iloc[0]["veredito"] == "ranking vivo"

    def test_plataforma_excluida_nao_aparece(self):
        antes = metrics.preparar(_lista("amazon", "2026-08-08", _TOP10_40PCT))
        hoje = metrics.preparar(_lista("amazon", "2026-08-10", _TOP10_40PCT))
        assert not len(metrics.estabilidade(hoje, antes, excluir=["amazon"]))


class TestPisoPreco:
    def test_movimento_relevante(self):
        antes = metrics.preparar(_lista(
            "amazon", "2026-08-08", ["Midea", "LG"], precos={1: 2000.0, 2: 1800.0}
        ))
        hoje = metrics.preparar(_lista(
            "amazon", "2026-08-10", ["Midea", "LG"], precos={1: 2200.0, 2: 1799.0}
        ))

        piso = metrics.piso_preco(hoje, antes)
        movimentos = metrics.movimentos_relevantes(piso)
        assert len(movimentos) == 1                    # LG moveu 0,06% → ignorado
        assert movimentos.iloc[0]["marca"] == "Midea"
        assert movimentos.iloc[0]["delta_pct"] == 10.0


class TestApoio:
    def test_marcas_fora_do_painel(self):
        """Marca que rankeia e não está no painel é pedido de inclusão —
        em 10/08/2026 apareceram HQ, Hisense, Consul, Britânia, Daikin."""
        df = metrics.preparar(_lista("amazon", "2026-08-10", _TOP10_10PCT))
        fora = metrics.marcas_fora_do_radar(df, ["Midea", "LG", "Samsung", "Gree"])
        assert fora == ["Consul"]

    def test_lider_por_plataforma(self):
        df = metrics.preparar(pd.concat([
            _lista("amazon", "2026-08-10", _TOP10_40PCT),
            _lista("magazineluiza", "2026-08-10", _TOP10_10PCT),
        ], ignore_index=True))
        lideres = metrics.lideres(df).set_index("plataforma")
        assert lideres.loc["amazon", "marca"] == "Midea"
        assert lideres.loc["magazineluiza", "marca"] == "LG"

    def test_presenca_por_marca(self):
        df = metrics.preparar(_lista("amazon", "2026-08-10", _TOP10_40PCT))
        presenca = metrics.presenca_por_marca(df).set_index("marca")
        assert presenca.loc["LG", "posicoes"] == 3

    def test_nome_do_dia_da_semana(self):
        assert metrics.nome_dia_semana("2026-08-10") == "segunda"


class TestLideresNaoFabricaLinha:
    """
    `groupby.first()` pega o primeiro valor NÃO-NULO de cada coluna,
    independentemente — um #1 sem preço saía com a marca da posição 1 e o
    preço da posição 2. Esse preço fabricado alimentaria direto a regra de
    decisão "#1 abaixo de R$ 1.800 = disputa de entrada".
    """

    def test_lider_sem_preco_nao_herda_o_preco_do_segundo(self):
        spec = SOURCES["amazon"]
        linhas = []
        for posicao, (marca, preco) in enumerate(
            [("Midea", None), ("LG", 1500.0), ("Gree", 1600.0)], start=1
        ):
            item = BestSellerItem(
                rank=posicao,
                titulo=f"Ar Condicionado Split {marca} 12000 BTUs",
                preco=preco,
            )
            linhas.append(item.to_row(
                data="2026-08-10", horario="09:30", run_id=None, spec=spec,
                url_coleta="u", endpoint="e",
            ))
        lider = metrics.lideres(metrics.preparar(to_dataframe(linhas))).iloc[0]
        assert lider["marca"] == "Midea"
        assert pd.isna(lider["preco"])


class TestEstabilidadeAposIdaEVoltaPeloCsv:
    """
    Um `sku_plataforma` só de dígitos (itemid da Shopee, productId da VTEX)
    volta do CSV como float ("200000002.0") se o dtype não for forçado.

    O estrago aparece exatamente no caminho do brief diário: a coleta de HOJE
    está em memória (SKU string) e a base de comparação vem do master em CSV
    (SKU float). O pareamento não acha item nenhum, e a tabela de
    estabilidade — onde mora o alerta CURADORIA SUSPEITA — fica vazia sem
    erro nenhum no log.
    """

    def _dia(self, data):
        spec = SOURCES["shopee"]
        linhas = []
        for posicao in range(1, 8):
            item = BestSellerItem(
                rank=posicao,
                titulo=f"Ar Condicionado Split Marca{posicao} 12000 BTUs {data}",
                # Um SKU vazio faz o pandas inferir float na coluna inteira.
                sku_plataforma=None if posicao == 7 else f"20000000{posicao}",
            )
            linhas.append(item.to_row(
                data=data, horario="09:30", run_id=None, spec=spec,
                url_coleta="u", endpoint="e",
            ))
        return to_dataframe(linhas)

    def test_sku_numerico_do_csv_pareia_com_a_coleta_em_memoria(self, tmp_path):
        from bestsellers import storage

        caminho = str(tmp_path / "master.csv")
        storage.atualizar_historico(self._dia("2026-08-08"), caminho)

        # Base: vinda do CSV. Hoje: recém-coletado, ainda em memória.
        anterior = metrics.preparar(storage.carregar_historico(caminho))
        hoje = metrics.preparar(self._dia("2026-08-10"))

        # O título muda entre os dias de propósito: sem o SKU não há
        # pareamento nenhum, então este teste mede o SKU e só ele.
        tabela = metrics.estabilidade(hoje, anterior)
        assert len(tabela) == 1
        assert tabela.iloc[0]["itens_comuns"] == 6   # 7 posições, 1 sem SKU
