"""
tests/test_pricetrack_api_import.py — agregação do import diário da API PriceTrack.

Cobre o roadmap docs/PRICETRACK_INSIGHTS.md §3 item 9: ofertas sem `sku`
não reconciliam com o catálogo (join PT × coletas) e agora são rejeitadas
no `aggregate_offers`, com breakdown em `rejections` que alimenta o
rejection_log de `pricetrack_import_log`.

Rode: pytest tests/test_pricetrack_api_import.py
"""
import importlib.util
from datetime import date
from pathlib import Path

import pandas as pd

_SPEC = importlib.util.spec_from_file_location(
    "pricetrack_api_import",
    Path(__file__).resolve().parent.parent / "scripts" / "pricetrack_api_import.py",
)
ptai = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ptai)


def _offer(sku: str = "42MACA09S5", price=1999.90, **overrides) -> dict:
    """Oferta NDJSON mínima no formato do export (snake_case)."""
    base = {
        "category": "AR CONDICIONADO",
        "brand": "MIDEA",
        "sku": sku,
        "product_name": "Ar Condicionado Split Midea 9000 Btus Frio",
        "marketplace": "MERCADO LIVRE",
        "seller": "WEBCONTINENTAL",
        "spot_price": price,
    }
    base.update(overrides)
    return base


class TestAggregateOffersMissingSku:
    def test_rejeita_e_contabiliza_sku_vazio(self):
        df = pd.DataFrame([
            _offer(),
            _offer(sku=""),
            _offer(sku="   "),       # _pick_text normaliza p/ "" → rejeita
            _offer(price=None),      # sem preço — rejeição independente
        ])
        agg, rejections = ptai.aggregate_offers(df, "2026-06-10")
        assert rejections.get("MISSING_SKU") == 2
        # NO_PRICE virou NO_CASH_PRICE: agora só preço À VISTA conta como preço
        # (o fallback antigo aceitava preço a prazo e misturava as bases).
        assert rejections.get("NO_CASH_PRICE") == 1
        assert list(agg["sku"].unique()) == ["42MACA09S5"]

    def test_todas_sem_sku_retorna_vazio(self):
        df = pd.DataFrame([_offer(sku=""), _offer(sku="")])
        agg, rejections = ptai.aggregate_offers(df, "2026-06-10")
        assert agg.empty
        assert rejections.get("MISSING_SKU") == 2

    def test_fora_de_categoria_contabilizada(self):
        df = pd.DataFrame([_offer(), _offer(category="GELADEIRA")])
        agg, rejections = ptai.aggregate_offers(df, "2026-06-10")
        assert rejections.get("OUT_OF_CATEGORY") == 1
        assert len(agg) == 1

    def test_sem_rejeicoes_dict_vazio(self):
        df = pd.DataFrame([_offer()])
        _, rejections = ptai.aggregate_offers(df, "2026-06-10")
        assert rejections == {}


class TestAggregateOffersAgrega:
    def test_min_avg_max_por_grupo(self):
        df = pd.DataFrame([
            _offer(price=1800.0),
            _offer(price=2200.0),
        ])
        agg, _ = ptai.aggregate_offers(df, "2026-06-10")
        # Sem `collection_hour` as ofertas entram só no agregado Diário.
        assert len(agg) == 1
        row = agg.iloc[0]
        assert row["turno"] == "Diário"
        assert row["min_price"] == 1800.0
        assert row["max_price"] == 2200.0
        assert row["avg_price"] == 2000.0
        assert row["collection_date"] == "2026-06-10"
        assert row["source_file"] == "api-2026-06-10"


class TestAggregateOffersTurno:
    """Recorte por turno via `collection_hour` (roadmap PRICETRACK_INSIGHTS §3 #10)."""

    def test_split_diario_manha_tarde(self):
        df = pd.DataFrame([
            _offer(price=1000.0, collection_hour=9),    # Manhã (08–12)
            _offer(price=1200.0, collection_hour=10),   # Manhã
            _offer(price=900.0,  collection_hour=20),   # Tarde (18–22)
            _offer(price=950.0,  collection_hour=15),   # fora das janelas → só Diário
        ])
        agg, _ = ptai.aggregate_offers(df, "2026-06-17")
        by_turno = {t: g.iloc[0] for t, g in agg.groupby("turno")}

        assert set(by_turno) == {"Diário", "Manhã", "Tarde"}
        # Diário = dia inteiro
        assert by_turno["Diário"]["min_price"] == 900.0
        assert by_turno["Diário"]["max_price"] == 1200.0
        # Manhã = horas 9 e 10
        assert by_turno["Manhã"]["min_price"] == 1000.0
        assert by_turno["Manhã"]["max_price"] == 1200.0
        # Tarde = hora 20
        assert by_turno["Tarde"]["min_price"] == 900.0
        assert by_turno["Tarde"]["max_price"] == 900.0

    def test_sem_hora_apenas_diario(self):
        df = pd.DataFrame([_offer(price=1500.0)])
        agg, _ = ptai.aggregate_offers(df, "2026-06-17")
        assert list(agg["turno"].unique()) == ["Diário"]

    def test_apenas_manha_nao_cria_tarde(self):
        df = pd.DataFrame([
            _offer(price=1100.0, collection_hour=8),
            _offer(price=1300.0, collection_hour=12),
        ])
        agg, _ = ptai.aggregate_offers(df, "2026-06-17")
        assert set(agg["turno"].unique()) == {"Diário", "Manhã"}


class TestShouldRedownload:
    """Re-download dos 2 dias voláteis (hoje/ontem); cache para dias antigos."""

    _TODAY = date(2026, 6, 21)

    def test_arquivo_inexistente_sempre_baixa(self):
        # Mesmo um dia antigo precisa baixar se o arquivo não existe localmente.
        assert ptai._should_redownload(
            "2026-01-01", file_exists=False, today=self._TODAY
        ) is True

    def test_hoje_com_cache_rebaixa(self):
        # Export do dia corrente ainda cresce → ignora cache parcial.
        assert ptai._should_redownload(
            "2026-06-21", file_exists=True, today=self._TODAY
        ) is True

    def test_ontem_com_cache_rebaixa(self):
        # D-1 pode ter sido importado provisoriamente intra-dia → re-baixa.
        assert ptai._should_redownload(
            "2026-06-20", file_exists=True, today=self._TODAY
        ) is True

    def test_dia_antigo_com_cache_reaproveita(self):
        # Anteontem para trás é imutável no PriceTrack → usa o cache.
        assert ptai._should_redownload(
            "2026-06-19", file_exists=True, today=self._TODAY
        ) is False

    def test_default_today_usa_hoje_real(self):
        # Sem injetar `today`, a data de HOJE deve re-baixar. Usar "hoje" (e não
        # "ontem") evita flakiness à meia-noite: se o relógio virar entre este
        # cálculo e o date.today() interno, a data passa a valer no máximo como
        # "ontem" para a referência interna — e tanto hoje quanto ontem dão True.
        hoje = date.today().isoformat()
        assert ptai._should_redownload(hoje, file_exists=True) is True

    def test_data_invalida_com_cache_reaproveita(self):
        # Data malformada não derruba o import: cai no cache existente.
        assert ptai._should_redownload(
            "nao-e-data", file_exists=True, today=self._TODAY
        ) is False


class TestBaseDePrecoBestCash:
    """A base gravada é o MENOR à vista (spot vs PIX), nunca o spot sozinho.

    Regressão do bug encontrado em 02/09/2026: o painel do PriceTrack mostrava
    R$ 2.229 para a Dufrio no Ecomaster 12K (Magazine Luiza, PIX -10%) e a
    tabela guardava R$ 2.476,67 — o spot. Todo consumidor a jusante (piso de
    mercado, buy box, violação de MAP, briefing) herdava o erro.
    """

    def test_pix_menor_que_spot_vence(self):
        df = pd.DataFrame([_offer(spot_price=2476.67, pix_price=2229.00)])
        agg, _ = ptai.aggregate_offers(df, "2026-09-01")
        row = agg.iloc[0]
        assert row["min_price"] == 2229.00
        assert row["spot_min_price"] == 2476.67
        assert row["pix_min_price"] == 2229.00

    def test_spot_menor_que_pix_vence(self):
        df = pd.DataFrame([_offer(spot_price=1899.00, pix_price=1999.00)])
        agg, _ = ptai.aggregate_offers(df, "2026-09-01")
        assert agg.iloc[0]["min_price"] == 1899.00

    def test_sem_pix_usa_spot(self):
        df = pd.DataFrame([_offer(spot_price=1899.00, pix_price=None)])
        agg, _ = ptai.aggregate_offers(df, "2026-09-01")
        assert agg.iloc[0]["min_price"] == 1899.00

    def test_preco_a_prazo_nunca_vira_preco(self):
        """A prazo é outra base — somá-lo ao à vista inventa um preço."""
        df = pd.DataFrame([
            _offer(spot_price=None, pix_price=None, forward_price=3200.0),
        ])
        agg, rejections = ptai.aggregate_offers(df, "2026-09-01")
        assert agg.empty
        assert rejections.get("NO_CASH_PRICE") == 1
        # `_` = diagnóstico, subconjunto de NO_CASH_PRICE (fora do total).
        assert rejections.get("_FORWARD_PRICE_ONLY") == 1

    def test_preco_zero_ou_negativo_nao_vira_piso(self):
        df = pd.DataFrame([
            _offer(spot_price=0.0, pix_price=None),
            _offer(spot_price=-1.0, pix_price=None),
            _offer(spot_price=1999.0, pix_price=None),
        ])
        agg, rejections = ptai.aggregate_offers(df, "2026-09-01")
        assert agg.iloc[0]["min_price"] == 1999.0
        assert rejections.get("NO_CASH_PRICE") == 2

    def test_linha_carimba_a_base(self):
        df = pd.DataFrame([_offer(spot_price=1999.0)])
        agg, _ = ptai.aggregate_offers(df, "2026-09-01")
        assert (agg["price_basis"] == ptai.PRICE_BASIS_BEST_CASH).all()


class TestDisponibilidade:
    """Oferta UNAVAILABLE não compete no piso — mas a listagem não some."""

    def test_indisponivel_fora_do_preco(self):
        df = pd.DataFrame([
            _offer(spot_price=2400.0, status="AVAILABLE"),
            _offer(spot_price=999.0, status="UNAVAILABLE"),   # arrastaria o piso
        ])
        agg, _ = ptai.aggregate_offers(df, "2026-09-01")
        row = agg.iloc[0]
        assert row["min_price"] == 2400.0
        assert row["obs_count"] == 1
        assert row["unavailable_count"] == 1

    def test_grupo_so_indisponivel_sobrevive_sem_preco(self):
        df = pd.DataFrame([_offer(spot_price=2400.0, status="UNAVAILABLE")])
        agg, _ = ptai.aggregate_offers(df, "2026-09-01")
        row = agg.iloc[0]
        assert pd.isna(row["min_price"])
        assert pd.isna(row["mode_price"])       # nunca 0.0
        assert row["obs_count"] == 0
        assert row["unavailable_count"] == 1

    def test_indisponivel_SEM_preco_tambem_mantem_o_grupo(self):
        """O caso real: oferta fora do ar normalmente vem sem preço nenhum.

        Filtrar por preço antes de agrupar apagava justamente esses grupos —
        o oposto do prometido (indisponível não compete no piso, mas não some).
        """
        df = pd.DataFrame([
            _offer(spot_price=None, pix_price=None, status="UNAVAILABLE"),
        ])
        agg, rejections = ptai.aggregate_offers(df, "2026-09-01")
        assert len(agg) >= 1
        row = agg.iloc[0]
        assert pd.isna(row["min_price"])
        assert row["title"] != ""               # `title` é NOT NULL na tabela
        assert row["obs_count"] == 0
        assert row["unavailable_count"] == 1
        assert "NO_CASH_PRICE" not in rejections

    def test_status_desconhecido_nao_e_disponivel(self):
        df = pd.DataFrame([
            _offer(spot_price=1500.0, status="PENDING"),
            _offer(spot_price=2400.0, status="AVAILABLE"),
        ])
        agg, _ = ptai.aggregate_offers(df, "2026-09-01")
        row = agg.iloc[0]
        assert row["min_price"] == 2400.0       # 1500 não entra no piso
        assert row["obs_count"] == 1
        assert row["unavailable_count"] == 1

    def test_sem_coluna_status_tudo_conta(self):
        """Export sem `status`: nada a excluir (comportamento anterior)."""
        df = pd.DataFrame([_offer(spot_price=2400.0)])
        assert "status" not in df.columns
        agg, _ = ptai.aggregate_offers(df, "2026-09-01")
        assert agg.iloc[0]["min_price"] == 2400.0
        assert agg.iloc[0]["unavailable_count"] == 0

    def test_status_em_branco_nao_entra_no_preco(self):
        """Coluna presente e valor vazio = status desconhecido, não disponível.

        O ponto da correção é o preço significar exatamente uma coisa; "não sei
        se dá para comprar" não entra num piso de mercado. Nada some em
        silêncio: a observação é contada em `unavailable_count`.
        """
        df = pd.DataFrame([
            _offer(spot_price=2400.0, status=""),
            _offer(spot_price=2500.0, status="AVAILABLE"),
        ])
        agg, _ = ptai.aggregate_offers(df, "2026-09-01")
        row = agg.iloc[0]
        assert row["min_price"] == 2500.0
        assert row["obs_count"] == 1
        assert row["unavailable_count"] == 1


class TestUltimaColeta:
    """`last_price` reproduz o painel ("Preço exibido: última coleta")."""

    def test_pega_a_hora_mais_alta_da_janela(self):
        df = pd.DataFrame([
            _offer(spot_price=2476.67, collection_hour=9),
            _offer(spot_price=2287.78, collection_hour=20),
            _offer(spot_price=2432.22, collection_hour=14),
        ])
        agg, _ = ptai.aggregate_offers(df, "2026-09-01")
        by_turno = {t: g.iloc[0] for t, g in agg.groupby("turno")}
        # Diário: última coleta do dia é a das 20h
        assert by_turno["Diário"]["last_price"] == 2287.78
        assert by_turno["Diário"]["last_hour"] == 20
        assert by_turno["Diário"]["min_price"] == 2287.78
        # Manhã: só a das 9h entra na janela
        assert by_turno["Manhã"]["last_price"] == 2476.67
        assert by_turno["Manhã"]["last_hour"] == 9

    def test_last_difere_de_min_quando_o_preco_sobe_no_dia(self):
        """O caso que fazia painel e dashboard não fecharem."""
        df = pd.DataFrame([
            _offer(spot_price=2000.0, collection_hour=9),
            _offer(spot_price=2500.0, collection_hour=20),
        ])
        agg, _ = ptai.aggregate_offers(df, "2026-09-01")
        row = agg[agg["turno"] == "Diário"].iloc[0]
        assert row["min_price"] == 2000.0     # piso do dia
        assert row["last_price"] == 2500.0    # o que o painel mostra

    def test_sem_hora_ainda_tem_last_price(self):
        df = pd.DataFrame([_offer(spot_price=1999.0)])
        agg, _ = ptai.aggregate_offers(df, "2026-09-01")
        row = agg.iloc[0]
        assert row["last_price"] == 1999.0
        assert pd.isna(row["last_hour"])


class TestChaveDeAgrupamento:
    """O agrupamento tem de falar a mesma chave que a UNIQUE da tabela."""

    def test_dois_titulos_no_mesmo_grupo_viram_uma_linha(self):
        """Antes viravam DUAS linhas agregadas com a mesma chave do banco.

        O upsert (`on_conflict` sem `title`) resolvia o conflito guardando só a
        última do lote — a outra sumia em silêncio, levando junto as coletas
        que ela agregava.
        """
        df = pd.DataFrame([
            _offer(spot_price=2400.0, collection_hour=9),
            _offer(spot_price=2200.0, collection_hour=20,
                   product_name="Ar Condicionado Split Midea 9000 Btus Frio 220V"),
        ])
        agg, _ = ptai.aggregate_offers(df, "2026-09-01")
        diario = agg[agg["turno"] == "Diário"]
        assert len(diario) == 1
        row = diario.iloc[0]
        assert row["min_price"] == 2200.0
        assert row["obs_count"] == 2            # nenhuma coleta perdida
        assert row["title"]                     # título representativo


class TestAliasDeCampo:
    def test_segunda_grafia_preenche_quando_a_primeira_e_nula(self):
        """Parar na primeira grafia descartava o valor válido da segunda."""
        df = pd.DataFrame([{**_offer(), "spot_price": None, "spotPrice": 1999.0}])
        agg, _ = ptai.aggregate_offers(df, "2026-09-01")
        assert agg.iloc[0]["min_price"] == 1999.0


class TestRejeicoesNaoSeSobrepoem:
    def test_forward_only_e_diagnostico_nao_entra_no_total(self):
        """`_FORWARD_PRICE_ONLY` é subconjunto de `NO_CASH_PRICE`.

        Somar os dois contaria a mesma oferta duas vezes em `rows_rejected`.
        """
        df = pd.DataFrame([
            _offer(spot_price=None, pix_price=None, forward_price=3200.0),
        ])
        _, rejections = ptai.aggregate_offers(df, "2026-09-01")
        assert rejections["NO_CASH_PRICE"] == 1
        assert rejections["_FORWARD_PRICE_ONLY"] == 1
        total = sum(v for k, v in rejections.items() if not k.startswith("_"))
        assert total == 1
