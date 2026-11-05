"""Testes de utils/diagnostico.py — valores esperados calculados à mão."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from utils import diagnostico as dg

D1, D2, D3, D4 = date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)
T_MIDEA = "AR CONDICIONADO SPLIT 12000 BTU FRIO AI ECOMASTER - INVERTER - MIDEA - 220V - M12"
T_LG = "AR CONDICIONADO SPLIT HI WALL 12000 BTU FRIO - INVERTER - LG - 220V - L12"
T_CASSETE = "AR CONDICIONADO CASSETE 24000 BTU FRIO - INVERTER - MIDEA - 220V - C24"
T_ONOFF = "AR CONDICIONADO SPLIT 9000 BTU FRIO ON/OFF - GREE - 220V - G09"
CB, ML, MG, LOJA = "CASAS BAHIA", "MERCADO LIVRE", "MAGAZINE LUIZA", "MIDEA"


def _linha(dia, mkt, sku, seller, preco, brand="MIDEA", title=T_MIDEA, sub="HI-WALL", **kw):
    base = {
        "collection_date": dia.isoformat(), "turno": "Diário", "brand": brand,
        "sku": sku, "title": title, "subcategory": sub, "marketplace": mkt,
        "seller": seller, "seller_canonical": seller, "min_price": preco,
        "imported_at": "2026-11-05T00:00:00Z",
    }
    base.update(kw)
    return base


@pytest.fixture
def frame():
    linhas = [
        # M12 (Midea) — foco CB nos 4 dias, dois sellers no D1
        _linha(D1, CB, "M12", "LOJA A", 2000), _linha(D1, CB, "M12", "LOJA B", 1900),
        _linha(D2, CB, "M12", "LOJA A", 1800),
        _linha(D3, CB, "M12", "LOJA A", 1850),
        _linha(D4, CB, "M12", "LOJA A", 1900),
        # mercado do M12: ML e MG; a loja própria MIDEA é barata e NÃO pode entrar
        *[_linha(d, ML, "M12", "ML1", 2200) for d in (D1, D2, D3, D4)],
        *[_linha(d, MG, "M12", "MG1", 2000) for d in (D1, D2, D3, D4)],
        *[_linha(d, LOJA, "M12", "MIDEA", 1000) for d in (D1, D2, D3, D4)],
        # L12 (LG) — no foco só no D1 (presença 1 de 4 < 2 → fora dos agressivos)
        _linha(D1, CB, "L12", "LOJA A", 1500, brand="LG", title=T_LG),
        _linha(D1, ML, "L12", "ML1", 3000, brand="LG", title=T_LG),
        # fora do escopo padrão: cassete e on/off
        _linha(D1, CB, "C24", "LOJA A", 100, title=T_CASSETE, sub="CASSETE"),
        _linha(D1, CB, "G09", "LOJA A", 100, brand="GREE", title=T_ONOFF),
        # turno Manhã é ignorado; duplicata de partição fica com a mais recente
        _linha(D1, CB, "M12", "LOJA C", 1, turno="Manhã"),
        _linha(D2, CB, "M12", "LOJA A", 9999, imported_at="2026-11-01T00:00:00Z"),
    ]
    return dg.apply_filters(dg.prepare_frame(pd.DataFrame(linhas)))


def test_prepare_e_escopo_padrao(frame):
    assert set(frame["sku"]) == {"M12", "L12"}
    assert "LOJA C" not in set(frame["seller"])
    d2 = frame[(frame["dia"] == D2) & (frame["marketplace"] == CB)]
    assert d2["preco"].tolist() == [1800]          # duplicata antiga descartada


def test_bloco1_ofertas_por_marketplace(frame):
    r = dg.ofertas_por_marketplace(frame, CB, top=2)
    d1 = dict(r[r["dia"] == D1][["marketplace", "ofertas"]].values)
    # CB no D1: M12×LOJA A, M12×LOJA B, L12×LOJA A
    assert d1[CB] == 3
    assert set(r["marketplace"]) == {CB, ML}       # top 2 inclui o foco


def test_bloco1_foco_entra_mesmo_fora_do_top(frame):
    r = dg.ofertas_por_marketplace(frame, LOJA, top=1)
    assert LOJA in set(r["marketplace"])


def test_bloco4_marca_com_todas(frame):
    r = dg.anuncios_por_dimensao(frame, CB, "marca")
    d1 = {g: (q, p) for _, g, q, p in r[r["dia"] == D1].itertuples(index=False)}
    assert d1["MIDEA"] == (2, pytest.approx(200 / 3))
    assert d1["LG"] == (1, pytest.approx(100 / 3))
    assert d1[dg.TODAS] == (3, 100.0)


def test_bloco5_preco_minimo_por_marca(frame):
    r = dg.preco_minimo_por_marca(frame, CB)
    linha = r[(r["dia"] == D1) & (r["marca"] == "MIDEA")].iloc[0]
    assert (linha["preco_min"], linha["sku"], linha["seller"]) == (1900, "M12", "LOJA B")


def test_mediana_exclui_foco_e_loja_de_marca(frame):
    r = dg.mediana_mercado(frame, CB)
    m = r[(r["dia"] == D1) & (r["sku"] == "M12")].iloc[0]
    assert m["mediana_mercado"] == 2100            # mediana(ML 2200, MG 2000)
    assert m["n_marketplaces"] == 2
    com_loja = dg.mediana_mercado(frame, CB, excluir_lojas_marca=False)
    assert com_loja[(com_loja["dia"] == D1) & (com_loja["sku"] == "M12")].iloc[0]["mediana_mercado"] == 2000


def test_bloco6_profundidade_por_marca(frame):
    r = dg.profundidade_por_marca(frame, CB)
    d2 = r[(r["dia"] == D2) & (r["marca"] == "MIDEA")].iloc[0]
    assert d2["profundidade_pct"] == pytest.approx(100 * (2100 - 1800) / 2100)


def test_bloco7_produtos_agressivos(frame):
    r = dg.produtos_agressivos(frame, CB)
    assert r["sku"].tolist() == ["M12"]            # L12 não tem presença mínima
    p = r.iloc[0]
    esperado = [100 * (2100 - x) / 2100 for x in (1900, 1800, 1850, 1900)]
    assert p["pct_abaixo_medio"] == pytest.approx(sum(esperado) / 4)
    assert (p["menor_preco"], p["seller"], p["n_sellers_foco"]) == (1800, "LOJA A", 1)
    assert p["mediana_mercado"] == 2100
    assert p["serie_precos"] == [1900, 1800, 1850, 1900]
    assert p["dias_presenca"] == 4


def test_bloco8_historico_produtos(frame):
    r = dg.historico_produtos(frame, ["M12"], foco=CB)
    assert r.sort_values("dia")["preco_min"].tolist() == [1900, 1800, 1850, 1900]


def test_exportar_datas_iso(frame):
    out = dg.anuncios_dia(frame)
    assert out["data"].iloc[0] == "2026-11-01"
    assert "menor_preco_a_vista" in out.columns


def test_load_frame_le_dia_a_dia_e_ignora_outros_turnos():
    class _Store:
        def read_days(self, dataset, start, end, columns, partition_column=None):
            assert dataset == "pricetrack"
            yield D1, pd.DataFrame([_linha(D1, CB, "M12", "LOJA A", 2000),
                                    _linha(D1, CB, "M12", "LOJA A", 1, turno="Tarde")])

    df = dg.load_frame(_Store(), D1, D1)
    assert df["preco"].tolist() == [2000]


def test_frame_vazio_nao_quebra_metricas():
    vazio = dg.prepare_frame(pd.DataFrame())
    assert dg.ofertas_por_marketplace(vazio, CB).empty
    assert dg.produtos_agressivos(vazio, CB).empty
    assert dg.profundidade_por_marca(vazio, CB).empty


# ── Apontamentos de revisão do PR #424 ──────────────────────────────────────

def test_depara_de_fora_do_periodo_classifica_historico_sem_subcategoria():
    """Período inteiro anterior à migração 007: nenhuma linha tem subcategoria."""
    antigas = pd.DataFrame([_linha(D1, CB, "M12", "LOJA A", 2000, sub=None)])
    sem_mapa = dg.apply_filters(dg.prepare_frame(antigas))
    assert sem_mapa.empty                          # "SPLIT" sozinho não basta
    com_mapa = dg.apply_filters(dg.prepare_frame(antigas, mapping={"M12": "HI-WALL"}))
    assert com_mapa["sku"].tolist() == ["M12"]


def test_load_subcategory_map_le_dias_recentes():
    class _Store:
        def read_days(self, dataset, start, end, columns):
            assert columns == ["sku", "subcategory"] and (end - start).days == dg.DIAS_DEPARA
            yield D4, pd.DataFrame({"sku": ["M12", "C24"], "subcategory": ["HI-WALL", "CASSETE"]})

    assert dg.load_subcategory_map(_Store(), D4) == {"M12": "HI-WALL", "C24": "CASSETE"}


def test_nome_declarado_vale_quando_nao_ha_depara():
    linha = _linha(D1, CB, "H09", "LOJA A", 2000, sub=None,
                   title="AR CONDICIONADO SPLIT HI WALL INVERTER 9000 BTUS FRIO - HISENSE - 220V - H09")
    assert dg.prepare_frame(pd.DataFrame([linha]))["subcategory"].tolist() == ["HI-WALL"]


def test_reimportacao_vence_a_particao_da_poda_mesmo_sem_imported_at():
    tier = _linha(D1, CB, "M12", "LOJA A", 2000,
                  _particao="pricetrack/data=2026-11-01__run-tier1102.parquet")
    reimport = _linha(D1, CB, "M12", "LOJA A", 1700, imported_at=None,
                      _particao="pricetrack/data=2026-11-01__run-import.parquet")
    for ordem in ([tier, reimport], [reimport, tier]):
        assert dg.prepare_frame(pd.DataFrame(ordem))["preco"].tolist() == [1700]


def test_versao_nova_sem_preco_nao_ressuscita_a_antiga():
    antiga = _linha(D1, CB, "M12", "LOJA A", 2000, imported_at="2026-11-01T00:00:00Z")
    nova = _linha(D1, CB, "M12", "LOJA A", None, imported_at="2026-11-02T00:00:00Z")
    assert dg.prepare_frame(pd.DataFrame([antiga, nova])).empty


def test_duas_grafias_do_mesmo_seller_viram_uma_linha():
    a = _linha(D1, CB, "M12", "friopecas", 2000, seller_canonical="Friopeças")
    b = _linha(D1, CB, "M12", "FRIOPECAS OFICIAL", 1950, seller_canonical="Friopeças")
    f = dg.prepare_frame(pd.DataFrame([a, b]))
    assert f[["seller", "preco"]].values.tolist() == [["Friopeças", 1950]]


def test_teto_de_30k_btu_vale_mesmo_sem_filtro_de_subcategoria():
    t36 = "AR CONDICIONADO SPLIT HI WALL 36000 BTU FRIO - INVERTER - MIDEA - 220V - M36"
    f = dg.prepare_frame(pd.DataFrame([_linha(D1, CB, "M36", "LOJA A", 6000, title=t36),
                                       _linha(D1, CB, "M12", "LOJA A", 2000)]))
    assert dg.apply_filters(f, subcategorias=None, tecnologias=None)["sku"].tolist() == ["M12"]


def test_grupo_midea_somado_sob_midea_com_grafia_original_preservada():
    linhas = [
        _linha(D1, CB, "M12", "LOJA A", 2000, brand="MIDEA"),
        _linha(D1, CB, "M18", "LOJA A", 2500, brand="MIDEA CARRIER",
               title=T_MIDEA.replace("12000", "18000").replace("M12", "M18")),
        _linha(D1, CB, "S09", "LOJA B", 1700, brand="SPRINGER CARRIER MIDEA",
               title=T_MIDEA.replace("12000", "9000").replace("M12", "S09")),
        _linha(D1, CB, "L12", "LOJA A", 1500, brand="LG", title=T_LG),
    ]
    f = dg.apply_filters(dg.prepare_frame(pd.DataFrame(linhas)))
    assert set(f["brand"]) == {"MIDEA", "LG"}
    assert set(f.loc[f["brand"] == "MIDEA", "brand_original"]) == {
        "MIDEA", "MIDEA CARRIER", "SPRINGER CARRIER MIDEA"}
    marca = dg.anuncios_por_dimensao(f, CB, "marca")
    assert dict(zip(marca["grupo"], marca["qtd"]))["MIDEA"] == 3
    minimo = dg.preco_minimo_por_marca(f, CB)
    assert minimo.loc[minimo["marca"] == "MIDEA", "preco_min"].item() == 1700


@pytest.mark.parametrize("alias", [
    "MIDEA", "MIDEA CARRIER", "SPRINGER CARRIER MIDEA", "SPRINGER MIDEA", "CARRIER", "SPRINGER",
])
def test_todas_as_grafias_do_grupo_viram_midea_no_frame(alias):
    f = dg.prepare_frame(pd.DataFrame([_linha(D1, CB, "M12", "LOJA A", 2000, brand=alias)]))
    assert (f["brand"].item(), f["brand_original"].item()) == ("MIDEA", alias)


def test_mesmo_sku_com_duas_grafias_do_grupo_fica_com_o_menor_preco():
    """Dedup pela marca crua: a mais recente não apaga a outra grafia antes do mínimo."""
    a = _linha(D1, CB, "M12", "LOJA A", 1800, brand="MIDEA", imported_at="2026-11-01T00:00:00Z")
    b = _linha(D1, CB, "M12", "LOJA A", 2100, brand="SPRINGER MIDEA", imported_at="2026-11-02T00:00:00Z")
    f = dg.prepare_frame(pd.DataFrame([a, b]))
    assert f[["brand", "preco"]].values.tolist() == [["MIDEA", 1800]]


@pytest.mark.parametrize("loja", ["CARRIER", "SPRINGER", "MIDEA CARRIER", "SPRINGER MIDEA"])
def test_lojas_do_grupo_ficam_fora_da_mediana(loja):
    linhas = [
        _linha(D1, CB, "M12", "LOJA A", 1900),
        _linha(D1, ML, "M12", "ML1", 2200),
        _linha(D1, MG, "M12", "MG1", 2000),
        _linha(D1, loja, "M12", loja, 900),       # loja própria, barata: não pode puxar a mediana
    ]
    med = dg.mediana_mercado(dg.prepare_frame(pd.DataFrame(linhas)), CB)
    assert med["mediana_mercado"].item() == 2100
    assert med["n_marketplaces"].item() == 2


def test_carrefour_nao_e_confundido_com_carrier():
    linhas = [_linha(D1, CB, "M12", "LOJA A", 1900), _linha(D1, "CARREFOUR", "M12", "C1", 2000)]
    med = dg.mediana_mercado(dg.prepare_frame(pd.DataFrame(linhas)), CB)
    assert med["n_marketplaces"].item() == 1
