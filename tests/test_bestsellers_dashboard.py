"""
tests/test_bestsellers_dashboard.py — Página 🥇 Mais Vendidos do dashboard.

A coleta de mais vendidos já gravava CSV, master e Supabase, mas nada disso
aparecia no painel. Estes testes travam o caminho inteiro da leitura:

  * a página está registrada no menu (sem isso, o código existe e ninguém vê);
  * a série é lida do banco e, quando ele não serve, do disco — com o MOTIVO
    preservado, porque cair no CSV local em silêncio faria o banco mudo passar
    despercebido por semanas;
  * o vazio-sem-erro do PostgREST (RLS ligada, chave `anon`) é diagnosticado
    como bloqueio de leitura, não como "não coletou" — é exatamente o sintoma
    de uma coleta que roda e não aparece em lugar nenhum;
  * `sku_plataforma` sobrevive à ida e volta pelo CSV como TEXTO (virando
    float, o pareamento entre dias não acha item nenhum e nada no log acusa);
  * a página renderiza com dados e sem dados, sem exceção.

Rode: pytest tests/test_bestsellers_dashboard.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402
import app  # noqa: E402 — importável sem renderizar (entry sob `__main__`)

APP_FILE = str(ROOT / "app.py")
PAGINA = "🥇 Mais Vendidos"
RUN_TIMEOUT = 60


# ---------------------------------------------------------------------------
# Registro no menu
# ---------------------------------------------------------------------------

def test_pagina_registrada_no_menu() -> None:
    assert PAGINA in app.PAGES, list(app.PAGES)
    assert app.PAGES[PAGINA] is app.page_bestsellers
    assert PAGINA in app._NAV_GROUPS["INSIGHTS"], app._NAV_GROUPS["INSIGHTS"]


# ---------------------------------------------------------------------------
# Leitura do disco
# ---------------------------------------------------------------------------

def _linhas_csv() -> pd.DataFrame:
    """Duas posições no formato canônico da série."""
    from bestsellers.config import SOURCES
    from bestsellers.models import BestSellerItem

    spec = SOURCES["amazon"]
    linhas = [
        BestSellerItem(
            rank=posicao,
            titulo=f"Ar Condicionado Split Inverter {marca} 12000 BTUs",
            preco=2000.0 + posicao,
            # ASIN só de dígitos: é aqui que o inferidor de tipo do pandas
            # transformaria o identificador em 200000002.0.
            sku_plataforma=f"20000000{posicao}",
        ).to_row(
            data="2026-08-10", horario="09:30", run_id="run", spec=spec,
            url_coleta=spec.url_publica, endpoint=spec.url_publica,
        )
        for posicao, marca in enumerate(("Midea", "LG"), start=1)
    ]
    from bestsellers.storage import to_dataframe
    return to_dataframe(linhas)


def test_alinhar_completa_colunas_canonicas() -> None:
    from bestsellers.models import COLUNAS

    alinhado = app._bs_alinhar(pd.DataFrame([{"rank": 1, "plataforma": "amazon"}]))
    assert list(alinhado.columns) == list(COLUNAS)
    assert alinhado["titulo"].isna().all()


def test_ler_csv_preserva_sku_como_texto(tmp_path: Path) -> None:
    caminho = tmp_path / "bestsellers_2026-08-10.csv"
    _linhas_csv().to_csv(caminho, index=False, encoding="utf-8-sig", sep=";")

    lido = app._bs_ler_csv(caminho)
    assert len(lido) == 2
    assert list(lido["sku_plataforma"]) == ["200000001", "200000002"]


def test_ler_csv_de_arquivo_inexistente_devolve_vazio(tmp_path: Path) -> None:
    assert app._bs_ler_csv(tmp_path / "nao_existe.csv").empty


def test_disco_prefere_master_e_cai_nos_csvs_do_dia(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    master = tmp_path / "data" / "master_bestsellers.csv"
    saida = tmp_path / "output"
    saida.mkdir(parents=True)
    monkeypatch.setattr(app, "_bs_caminhos", lambda: (master, saida))

    # Sem master e sem CSV do dia: nada a exibir.
    vazio, origem = app._bs_ler_disco()
    assert vazio.empty and origem == ""

    # Só os CSVs brutos do dia (máquina que perdeu o master): ainda serve —
    # o coletor grava essa evidência ANTES de tentar o banco.
    _linhas_csv().to_csv(
        saida / "bestsellers_2026-08-10.csv", index=False,
        encoding="utf-8-sig", sep=";",
    )
    do_dia, origem_dia = app._bs_ler_disco()
    assert len(do_dia) == 2 and "bestsellers_*.csv" in origem_dia

    # Com master, ele vence: é a série acumulada e validada.
    master.parent.mkdir(parents=True, exist_ok=True)
    historico = pd.concat([_linhas_csv(), _linhas_csv()], ignore_index=True)
    historico.to_csv(master, index=False, encoding="utf-8-sig", sep=";")
    do_master, origem_master = app._bs_ler_disco()
    assert len(do_master) == 4 and origem_master.endswith("master_bestsellers.csv")


# ---------------------------------------------------------------------------
# Diagnóstico do vazio-sem-erro
# ---------------------------------------------------------------------------

class _ClienteFake:
    """Cliente Supabase mínimo: a view responde, a tabela não."""

    def __init__(self, view_tem_dado: bool) -> None:
        self._view_tem_dado = view_tem_dado

    def table(self, nome: str):
        dados = [{"data": "2026-08-10"}] if self._view_tem_dado else []
        return _QueryFake(dados)


class _QueryFake:
    def __init__(self, dados) -> None:
        self.data = dados

    def select(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return self


def test_motivo_denuncia_rls_quando_a_view_tem_dado() -> None:
    motivo = app._bs_motivo_tabela_vazia(_ClienteFake(view_tem_dado=True))
    assert "Row Level Security" in motivo
    assert "service_role" in motivo


def test_motivo_generico_quando_nem_a_view_tem_dado() -> None:
    motivo = app._bs_motivo_tabela_vazia(_ClienteFake(view_tem_dado=False))
    assert "Row Level Security" not in motivo
    assert "vazia para esta credencial" in motivo


def test_query_cai_no_disco_preservando_o_motivo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Banco mudo + disco com dado: exibe o dado E conta por que o banco falhou."""
    monkeypatch.setattr(
        app, "_bs_ler_supabase", lambda limit: (pd.DataFrame(), "RLS bloqueou")
    )
    monkeypatch.setattr(
        app, "_bs_ler_disco", lambda: (_linhas_csv(), "data/bestsellers/master.csv")
    )
    app.query_bestsellers.clear()
    df, origem, aviso = app.query_bestsellers()
    app.query_bestsellers.clear()

    assert len(df) == 2
    assert origem.startswith("CSV local")
    assert aviso == "RLS bloqueou", "cair no CSV em silêncio esconde o banco mudo"


def test_query_une_banco_e_disco_sem_duplicar_posicao(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As duas fontes divergem: a página mostra a UNIÃO, com o banco vencendo.

    O banco fica para trás quando o upload falha (chave `anon`, rede, RLS) e o
    master local só tem o que aquela máquina coletou. Ler só a primeira fonte
    que responder esconderia dias inteiros de coleta.
    """
    do_banco = _linhas_csv()                       # 10/08, ranks 1 e 2
    do_banco["preco"] = [1.0, 2.0]
    do_disco = _linhas_csv()                       # mesmas posições, preço velho
    do_disco["preco"] = [99.0, 99.0]
    outro_dia = _linhas_csv()
    outro_dia["data"] = "2026-08-03"               # só o disco tem este dia

    monkeypatch.setattr(app, "_bs_ler_supabase", lambda limit: (do_banco, ""))
    monkeypatch.setattr(
        app, "_bs_ler_disco",
        lambda: (pd.concat([do_disco, outro_dia], ignore_index=True), "master.csv"),
    )
    app.query_bestsellers.clear()
    df, origem, _ = app.query_bestsellers()
    app.query_bestsellers.clear()

    # 2 posições do banco + 2 do dia que só existe no disco — sem duplicata.
    assert len(df) == 4, df[["data", "plataforma", "rank"]]
    assert sorted(df["data"].unique()) == ["2026-08-03", "2026-08-10"]
    do_dia_10 = df[df["data"] == "2026-08-10"]
    assert sorted(do_dia_10["preco"]) == [1.0, 2.0], "o banco tem de vencer o CSV"
    assert "Supabase" in origem and "CSV local" in origem, origem
    # Contagem PÓS-dedup: o disco contribuiu com 1 dia (03/08), não com os 2
    # que o arquivo tem — 10/08 foi coberto pelo banco. Contar antes somaria
    # 3 dias numa série de 2.
    assert "Supabase · `bestsellers` (1 dia(s))" in origem, origem
    assert "(1 dia(s))" in origem.split("+")[1], origem
    assert "_fonte" not in df.columns, "coluna interna vazou para a série"


def test_pagina_com_tudo_em_quarentena_mostra_o_detalhe() -> None:
    """Quando nada sobra, o painel ainda tem de dizer QUAL endpoint reprovou."""
    at = AppTest.from_function(_pagina_toda_reprovada_harness)
    at.run(timeout=RUN_TIMEOUT)
    assert not at.exception, f"página estourou: {[str(e.value) for e in at.exception]}"

    erros = " ".join(str(e.value) for e in at.error)
    assert "portão de ordenação" in erros, erros
    # O detalhe é o que permite agir: sem ele resta a mensagem genérica.
    tabelas = at.dataframe
    assert tabelas, "faltou a tabela do que foi reprovado"
    reprovadas = tabelas[0].value
    assert "Endpoint chamado" in reprovadas.columns, list(reprovadas.columns)
    assert "Shopee" in set(reprovadas["Plataforma"]), reprovadas


def _pagina_toda_reprovada_harness() -> None:
    """Série em que NENHUMA leitura prova a ordenação por vendas."""
    import streamlit as st

    import app
    from bestsellers.config import SOURCES
    from bestsellers.models import BestSellerItem
    from bestsellers.storage import to_dataframe

    spec = SOURCES["shopee"]
    linhas = [
        BestSellerItem(
            rank=posicao,
            titulo=f"Ar Condicionado Split Inverter Midea {posicao} 12000 BTUs",
            preco=1800.0 + posicao,
            sku_plataforma=f"shopee-{posicao}",
        ).to_row(
            data="2026-08-10", horario="09:30", run_id="run", spec=spec,
            url_coleta=spec.url_publica,
            # Busca sem `sortBy=sales`: relevância, não vendas.
            endpoint="https://shopee.com.br/search?keyword=ar%20condicionado",
        )
        for posicao in range(1, 11)
    ]

    def _fake(limit: int = 0):
        return to_dataframe(linhas), "Supabase · `bestsellers`", ""

    _fake.clear = lambda: None  # type: ignore[attr-defined]
    original = app.query_bestsellers
    app.query_bestsellers = _fake  # type: ignore[assignment]
    try:
        app.page_bestsellers()
    finally:
        app.query_bestsellers = original

    st.session_state["_out"] = {"ok": True}


def test_quarentena_remove_leitura_sem_prova_de_ordenacao() -> None:
    """Endpoint sem o parâmetro canônico não é lista de mais vendidos."""
    from bestsellers import metrics
    from bestsellers.config import SOURCES

    boa = _linhas_csv()
    ruim = _linhas_csv()
    ruim["plataforma"] = "shopee"
    ruim["plataforma_nome"] = SOURCES["shopee"].nome
    # URL de busca da Shopee SEM `sortBy=sales`: é relevância, não vendas.
    ruim["endpoint"] = "https://shopee.com.br/search?keyword=ar%20condicionado"
    ruim["url_coleta"] = ruim["endpoint"]

    serie = metrics.preparar(pd.concat([boa, ruim], ignore_index=True))
    limpa, reprovadas = app._bs_quarentena(serie)

    assert set(limpa["plataforma"]) == {"amazon"}
    assert len(reprovadas) == 1
    assert reprovadas.iloc[0]["plataforma"] == "shopee"
    assert reprovadas.iloc[0]["linhas"] == 2


def test_quarentena_nao_reprova_plataforma_sem_spec() -> None:
    """Sem spec não há parâmetro canônico para exigir — `validar` avisa, não corta."""
    from bestsellers import metrics

    desconhecida = _linhas_csv()
    desconhecida["plataforma"] = "varejista_novo"
    serie = metrics.preparar(desconhecida)

    limpa, reprovadas = app._bs_quarentena(serie)
    assert len(limpa) == 2 and reprovadas.empty


def test_query_sem_nenhuma_fonte_devolve_o_motivo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app, "_bs_ler_supabase", lambda limit: (None, "Supabase não configurado")
    )
    monkeypatch.setattr(app, "_bs_ler_disco", lambda: (pd.DataFrame(), ""))
    app.query_bestsellers.clear()
    df, origem, aviso = app.query_bestsellers()
    app.query_bestsellers.clear()

    assert df.empty and origem == ""
    assert aviso == "Supabase não configurado"


# ---------------------------------------------------------------------------
# Renderização
# ---------------------------------------------------------------------------

def _pagina_com_dados_harness() -> None:
    """Renderiza a página com uma série sintética de duas segundas-feiras."""
    import pandas as pd
    import streamlit as st

    import app
    from bestsellers.config import SOURCES
    from bestsellers.models import BestSellerItem
    from bestsellers.storage import to_dataframe

    # 03/08 e 10/08 de 2026 são segundas: é o par que o motor aceita comparar.
    marcas_amazon = ["Midea", "LG", "Springer", "Gree", "Midea", "Elgin",
                     "Philco", "TCL", "Carrier", "Samsung"] * 2
    marcas_ml = ["LG", "Midea", "Gree", "Elgin", "Samsung", "Philco"] * 2

    linhas = []
    for data in ("2026-08-03", "2026-08-10"):
        for chave, marcas in (("amazon", marcas_amazon), ("mercadolivre", marcas_ml)):
            spec = SOURCES[chave]
            for posicao, marca in enumerate(marcas, start=1):
                item = BestSellerItem(
                    rank=posicao,
                    titulo=f"Ar Condicionado Split Inverter {marca} 12000 BTUs",
                    preco=1700.0 + posicao * 10,
                    sku_plataforma=f"{chave}-{posicao}",
                )
                linhas.append(item.to_row(
                    data=data, horario="09:30", run_id="run", spec=spec,
                    url_coleta=spec.url_publica, endpoint=spec.url_publica,
                ))

    serie = to_dataframe(linhas)

    def _fake(limit: int = 0):
        return serie, "Supabase · tabela `bestsellers`", ""

    _fake.clear = lambda: None  # type: ignore[attr-defined]

    # Restaurado no finally: o AppTest roda no MESMO processo da suíte, e um
    # loader trocado que vazasse daqui mandaria dados para os testes seguintes.
    original = app.query_bestsellers
    app.query_bestsellers = _fake  # type: ignore[assignment]
    try:
        app.page_bestsellers()
    finally:
        app.query_bestsellers = original

    st.session_state["_out"] = {
        "linhas": len(serie),
        "datas": sorted(pd.to_datetime(serie["data"]).dt.date.unique()),
    }


def test_pagina_renderiza_com_dados() -> None:
    at = AppTest.from_function(_pagina_com_dados_harness)
    at.run(timeout=RUN_TIMEOUT)
    assert not at.exception, f"página estourou: {[str(e.value) for e in at.exception]}"

    # KPI por plataforma como card, com o delta contra a segunda anterior.
    rotulos = {m.label for m in at.metric}
    assert {"Amazon", "Mercado Livre"} <= rotulos, rotulos

    textos = " ".join(str(m.value) for m in at.markdown) + " ".join(
        str(c.value) for c in at.caption
    )
    assert "Mais Vendidos" in textos
    # A regra dura precisa estar à vista de quem lê o número.
    assert "ordinal" in textos.lower()


def _pagina_com_quarentena_harness() -> None:
    """Renderiza a página com uma plataforma reprovada no portão de ordenação."""
    import pandas as pd
    import streamlit as st

    import app
    from bestsellers.config import SOURCES
    from bestsellers.models import BestSellerItem
    from bestsellers.storage import to_dataframe

    linhas = []
    for chave, endpoint in (
        ("amazon", SOURCES["amazon"].url_publica),
        # Busca da Shopee SEM `sortBy=sales`: relevância, não vendas.
        ("shopee", "https://shopee.com.br/search?keyword=ar%20condicionado"),
    ):
        spec = SOURCES[chave]
        for posicao, marca in enumerate(
            ["Midea", "LG", "Gree", "Elgin", "TCL", "Philco",
             "Samsung", "Consul", "Carrier", "Hisense"], start=1,
        ):
            item = BestSellerItem(
                rank=posicao,
                titulo=f"Ar Condicionado Split Inverter {marca} 12000 BTUs",
                preco=1800.0 + posicao,
                sku_plataforma=f"{chave}-{posicao}",
            )
            linhas.append(item.to_row(
                data="2026-08-10", horario="09:30", run_id="run", spec=spec,
                url_coleta=spec.url_publica, endpoint=endpoint,
            ))

    serie = to_dataframe(linhas)

    def _fake(limit: int = 0):
        return serie, "Supabase · `bestsellers`", ""

    _fake.clear = lambda: None  # type: ignore[attr-defined]
    original = app.query_bestsellers
    app.query_bestsellers = _fake  # type: ignore[assignment]
    try:
        app.page_bestsellers()
    finally:
        app.query_bestsellers = original

    st.session_state["_out"] = {"linhas": len(serie)}


def test_pagina_poe_em_quarentena_sem_quebrar() -> None:
    """A leitura sem prova de ordenação sai da análise E é nomeada na tela."""
    at = AppTest.from_function(_pagina_com_quarentena_harness)
    at.run(timeout=RUN_TIMEOUT)
    assert not at.exception, f"página estourou: {[str(e.value) for e in at.exception]}"

    avisos = " ".join(str(w.value) for w in at.warning)
    assert "quarentena" in avisos.lower(), avisos
    assert "Shopee" in avisos, avisos

    # A plataforma reprovada não pode aparecer como card de KPI.
    rotulos = {m.label for m in at.metric}
    assert "Amazon" in rotulos, rotulos
    assert "Shopee" not in rotulos, rotulos


def _pagina_sem_dados_harness() -> None:
    """Renderiza a página com banco mudo e disco vazio."""
    import pandas as pd
    import streamlit as st

    import app

    # Fontes zeradas na mão: o teste não pode depender de a máquina que roda a
    # suíte ter (ou não ter) um master local — no PC coletor ela tem.
    # Restauradas no finally: o AppTest roda no MESMO interpretador da suíte, e
    # um stub que vazasse daqui derrubaria qualquer teste posterior que fosse
    # ler a série de verdade.
    supabase_real, disco_real = app._bs_ler_supabase, app._bs_ler_disco
    app._bs_ler_supabase = lambda limit: (None, "Supabase não configurado")  # type: ignore[assignment]
    app._bs_ler_disco = lambda: (pd.DataFrame(), "")  # type: ignore[assignment]
    app.query_bestsellers.clear()
    try:
        app.page_bestsellers()
    finally:
        app._bs_ler_supabase, app._bs_ler_disco = supabase_real, disco_real
        app.query_bestsellers.clear()

    st.session_state["_out"] = {"ok": True}


def test_pagina_sem_dados_mostra_diagnostico() -> None:
    """Sem banco e sem CSV, a página explica onde a série deveria estar."""
    at = AppTest.from_function(_pagina_sem_dados_harness)
    at.run(timeout=RUN_TIMEOUT)
    assert not at.exception, f"página estourou: {[str(e.value) for e in at.exception]}"

    erros = " ".join(str(e.value) for e in at.error)
    assert "Supabase não configurado" in erros, erros
    corpo = " ".join(str(m.value) for m in at.markdown)
    assert "collect_bestsellers.py" in corpo, "faltou o comando de coleta"
    assert "master_bestsellers.csv" in corpo, "faltou dizer onde a série deveria estar"


# ---------------------------------------------------------------------------
# Separação de referência (Mais Vendidos × Relevância)
# ---------------------------------------------------------------------------

def _pagina_duas_referencias_harness() -> None:
    """Renderiza a página com uma plataforma de vendas E uma de relevância.

    O painel deve oferecer o seletor de referência e mostrar UMA por vez —
    nunca as duas populações na mesma tabela.
    """
    import pandas as pd
    import streamlit as st

    import app
    from bestsellers.config import SOURCES
    from bestsellers.models import BestSellerItem
    from bestsellers.storage import to_dataframe

    marcas = ["Midea", "LG", "Springer", "Gree", "Midea", "Elgin",
              "Philco", "TCL", "Carrier", "Samsung"] * 2
    linhas = []
    for data in ("2026-08-03", "2026-08-10"):
        # amazon = mais_vendidos ; dufrio = relevancia (âncora OrderByScoreDESC).
        for chave in ("amazon", "dufrio"):
            spec = SOURCES[chave]
            endpoint = spec.url_publica + (
                "?O=OrderByScoreDESC" if chave == "dufrio" else ""
            )
            for posicao, marca in enumerate(marcas, start=1):
                linhas.append(BestSellerItem(
                    rank=posicao,
                    titulo=f"Ar Condicionado Split Inverter {marca} 12000 BTUs",
                    preco=1700.0 + posicao * 10,
                    sku_plataforma=f"{chave}-{posicao}",
                ).to_row(
                    data=data, horario="09:30", run_id="run", spec=spec,
                    url_coleta=spec.url_publica, endpoint=endpoint,
                ))

    serie = to_dataframe(linhas)

    def _fake(limit: int = 0):
        return serie, "Supabase · `bestsellers`", ""

    _fake.clear = lambda: None  # type: ignore[attr-defined]
    original = app.query_bestsellers
    app.query_bestsellers = _fake  # type: ignore[assignment]
    try:
        app.page_bestsellers()
    finally:
        app.query_bestsellers = original

    st.session_state["_out"] = {"ok": True}


def test_pagina_oferece_seletor_de_referencia() -> None:
    at = AppTest.from_function(_pagina_duas_referencias_harness)
    at.run(timeout=RUN_TIMEOUT)
    assert not at.exception, f"página estourou: {[str(e.value) for e in at.exception]}"

    # O radio de referência aparece com as duas opções.
    radios = {r.label: list(r.options) for r in at.radio}
    assert "Referência da lista" in radios, list(radios)
    assert {"Mais Vendidos", "Relevância"} <= set(radios["Referência da lista"]), radios

    # Segmento default = Mais Vendidos: só a Amazon aparece nos cards, nunca a
    # Dufrio (relevância) — as duas populações não se misturam.
    rotulos = {m.label for m in at.metric}
    assert "Amazon" in rotulos, rotulos
    assert "Dufrio" not in rotulos, "relevância vazou para o segmento de vendas"


def test_pagina_relevancia_isola_o_segmento() -> None:
    at = AppTest.from_function(_pagina_duas_referencias_harness)
    at.run(timeout=RUN_TIMEOUT)
    assert not at.exception, f"página estourou: {[str(e.value) for e in at.exception]}"

    # Troca para o segmento Relevância.
    at.radio(key="bs_referencia").set_value("relevancia").run(timeout=RUN_TIMEOUT)
    assert not at.exception, f"página estourou: {[str(e.value) for e in at.exception]}"

    rotulos = {m.label for m in at.metric}
    assert "Dufrio" in rotulos, rotulos
    assert "Amazon" not in rotulos, "vendas vazou para o segmento de relevância"
    # O aviso que marca a leitura como proxy tem de estar à vista.
    avisos = " ".join(str(w.value) for w in at.warning)
    assert "proxy" in avisos.lower(), avisos


if __name__ == "__main__":  # execução standalone
    raise SystemExit(pytest.main([__file__, "-q"]))
