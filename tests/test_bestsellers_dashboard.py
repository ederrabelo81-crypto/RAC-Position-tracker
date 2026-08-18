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


def _pagina_sem_dados_harness() -> None:
    """Renderiza a página com banco mudo e disco vazio."""
    import pandas as pd
    import streamlit as st

    import app

    # Fontes zeradas na mão: o teste não pode depender de a máquina que roda a
    # suíte ter (ou não ter) um master local — no PC coletor ela tem.
    app._bs_ler_supabase = lambda limit: (None, "Supabase não configurado")  # type: ignore[assignment]
    app._bs_ler_disco = lambda: (pd.DataFrame(), "")  # type: ignore[assignment]
    app.query_bestsellers.clear()

    app.page_bestsellers()
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


if __name__ == "__main__":  # execução standalone
    raise SystemExit(pytest.main([__file__, "-q"]))
