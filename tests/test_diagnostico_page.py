"""Testes da página 🩻 Diagnóstico (Streamlit AppTest, sem rede nem Drive)."""
from __future__ import annotations

from streamlit.testing.v1 import AppTest

RUN_TIMEOUT = 60


def _harness_com_dados() -> None:
    from datetime import date, timedelta

    import pandas as pd

    from pricetrack_dashboard.diagnostico_page import render
    from utils import diagnostico as dg

    hoje = date.today()
    titulo = "AR CONDICIONADO SPLIT 12000 BTU FRIO AI ECOMASTER - INVERTER - MIDEA - 220V - M12"
    titulo_lg = "AR CONDICIONADO SPLIT HI WALL 12000 BTU FRIO - INVERTER - LG - 220V - L12"
    linhas = []
    for k in range(1, 5):
        dia = (hoje - timedelta(days=k)).isoformat()
        for mkt, preco in (("CASAS BAHIA", 1900 + k), ("MERCADO LIVRE", 2200), ("MAGAZINE LUIZA", 2000)):
            linhas.append(dict(collection_date=dia, turno="Diário", brand="MIDEA", sku="M12",
                               title=titulo, subcategory="HI-WALL", marketplace=mkt,
                               seller="LOJA A", seller_canonical="LOJA A", min_price=preco))
            linhas.append(dict(collection_date=dia, turno="Diário", brand="LG", sku="L12",
                               title=titulo_lg, subcategory="HI-WALL", marketplace=mkt,
                               seller="LOJA B", seller_canonical="LOJA B", min_price=preco + 300))
        # fora do escopo: nunca pode aparecer
        linhas.append(dict(collection_date=dia, turno="Diário", brand="MIDEA", sku="C24",
                           title="AR CONDICIONADO CASSETE 24000 BTU FRIO - INVERTER - MIDEA - 220V - C24",
                           subcategory="CASSETE", marketplace="CASAS BAHIA",
                           seller="LOJA A", seller_canonical="LOJA A", min_price=100))
    frame = dg.prepare_frame(pd.DataFrame(linhas))
    render(lambda i, f: frame, hoje - timedelta(days=7), hoje)


def test_pagina_renderiza_os_blocos_no_escopo_padrao() -> None:
    at = AppTest.from_function(_harness_com_dados)
    at.run(timeout=RUN_TIMEOUT)
    assert not at.exception, [str(e.value) for e in at.exception]

    titulos = [s.value for s in at.subheader]
    for trecho in ("Quantidade de ofertas", "Anúncios por marca", "Preço mínimo",
                   "Profundidade", "agressivos", "Comparador", "Exportar"):
        assert any(trecho in t for t in titulos), (trecho, titulos)

    assert at.selectbox(key="dg_foco").value == "CASAS BAHIA"
    assert at.selectbox(key="dg_marca").value == "MIDEA"
    assert at.multiselect(key="dg_sub").value == ["HI-WALL"]
    assert at.multiselect(key="dg_tec").value == ["INVERTER"]

    # Cassete (fora do escopo padrão) não entra no recorte nem no seletor de produtos.
    assert "C24" not in at.multiselect(key="dg_skus").options
    resumo = " ".join(c.value for c in at.caption)
    assert "2 produtos" in resumo


def _harness_vazio() -> None:
    from datetime import date

    import pandas as pd

    from pricetrack_dashboard.diagnostico_page import render

    render(lambda i, f: pd.DataFrame(), date(2026, 11, 1), date(2026, 11, 4))


def test_pagina_sem_dado_avisa_e_nao_quebra() -> None:
    at = AppTest.from_function(_harness_vazio)
    at.run(timeout=RUN_TIMEOUT)
    assert not at.exception
    assert any("Sem dado do PriceTrack" in w.value for w in at.warning)


def test_pagina_registrada_no_menu_preco() -> None:
    import app

    assert app.PAGES["🩻 Diagnóstico"] is app.page_diagnostico
    assert "🩻 Diagnóstico" in app._NAV_GROUPS["PREÇO"]


def test_leitura_incompleta_do_historico_nao_fica_em_cache(monkeypatch) -> None:
    """Drive falhou numa partição: o de-para parcial é usado, mas não cacheado."""
    import pandas as pd

    import app

    class _Store:
        def __init__(self, sku, erros):
            self.sku, self.last_read_errors = sku, erros

        def read_days(self, dataset, start, end, columns, partition_column=None):
            self.last_read_errors = list(self.erros_da_vez)
            yield end, pd.DataFrame({"sku": [self.sku], "subcategory": ["HI-WALL"]})

    ruim = _Store("A", [])
    ruim.erros_da_vez = [("pricetrack/data=2026-11-03__run-import.parquet", "download recusado")]
    bom = _Store("B", [])
    bom.erros_da_vez = []
    avisos: list = []
    monkeypatch.setattr(app, "_avisar_particoes_ilegiveis", lambda s: avisos.append(s))
    app._query_subcategory_map_cached.clear()

    monkeypatch.setattr(app, "_history_store", lambda: ruim)
    assert app.query_subcategory_map() == {"A": "HI-WALL"}
    assert avisos == [ruim]

    monkeypatch.setattr(app, "_history_store", lambda: bom)
    assert app.query_subcategory_map() == {"B": "HI-WALL"}   # releu: o parcial não ficou

    def _sem_leitura():
        raise AssertionError("o completo deveria vir do cache")

    monkeypatch.setattr(app, "_history_store", _sem_leitura)
    assert app.query_subcategory_map() == {"B": "HI-WALL"}   # o completo, sim, do cache
    app._query_subcategory_map_cached.clear()


def _harness_grupo_midea() -> None:
    from datetime import date, timedelta

    import pandas as pd

    from pricetrack_dashboard.diagnostico_page import render
    from utils import diagnostico as dg

    hoje = date.today()
    dia = (hoje - timedelta(days=1)).isoformat()
    titulo = "AR CONDICIONADO SPLIT {btu} BTU FRIO X - INVERTER - {m} - 220V - {s}"
    linhas = []
    for i, marca in enumerate(["MIDEA", "MIDEA CARRIER", "SPRINGER CARRIER MIDEA", "CARRIER", "LG"]):
        sku = f"S{i}"
        linhas.append(dict(collection_date=dia, turno="Diário", brand=marca, sku=sku,
                           title=titulo.format(btu=12000, m=marca, s=sku), subcategory="HI-WALL",
                           marketplace="CASAS BAHIA", seller="LOJA", seller_canonical="LOJA",
                           min_price=2000 + i))
    frame = dg.prepare_frame(pd.DataFrame(linhas))
    render(lambda i, f: frame, hoje - timedelta(days=7), hoje)


def test_seletor_de_marca_mostra_o_grupo_midea_como_uma_marca_so() -> None:
    at = AppTest.from_function(_harness_grupo_midea)
    at.run(timeout=RUN_TIMEOUT)
    assert not at.exception, [str(e.value) for e in at.exception]
    assert at.selectbox(key="dg_marca").options == ["LG", "MIDEA"]
    assert at.selectbox(key="dg_marca").value == "MIDEA"
    assert any("regras v2" in c.value for c in at.caption)
