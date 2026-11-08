"""Navegação e componentes de leitura do app.py (Nov/2026)."""
import app


def test_toda_pagina_esta_em_exatamente_uma_secao() -> None:
    nas_secoes = [p for nomes in app._NAV_GROUPS.values() for p in nomes]
    assert sorted(nas_secoes) == sorted(app.PAGES), "página fora do menu ou duplicada"
    assert len(nas_secoes) == len(set(nas_secoes))


def test_url_path_unico_e_ascii() -> None:
    slugs = [app._slug(p) for p in app.PAGES]
    assert len(slugs) == len(set(slugs)), slugs
    assert all(s.isascii() and s for s in slugs)
    assert app._slug("🏆 Buy Box") == "buy-box"


def test_nomes_antigos_resolvem_para_pagina_atual() -> None:
    for antigo, (novo, visao) in app._LEGACY_PAGES.items():
        assert novo in app.PAGES, antigo
        if visao:
            assert visao in app._SUBVIEWS[novo], (antigo, visao)


def test_paginas_compostas_cobrem_as_funcoes_antigas() -> None:
    funcs = {f for views in app._SUBVIEWS.values() for f in views.values()}
    assert {app.page_top_movers, app.page_price_anomalies,
            app.page_buybox_position, app.page_share_of_buybox} <= funcs


def test_nomes_do_menu_em_portugues() -> None:
    proibidos = ("Results", "Availability", "Data Health", "Email Digest",
                 "Price Anomalies", "Top Movers", "Price Evolution",
                 "Market Analytics", "Daily Price Vision", "BuyBox Position")
    for nome in app.PAGES:
        assert not any(p in nome for p in proibidos), nome


def test_frase_resposta_do_cockpit() -> None:
    from datetime import date

    lider = {"marca": "Philco", "share": 0.18}
    txt = app._cockpit_headline(0.31, 0.334, lider, date(2026, 11, 8))
    assert "31%" in txt and "▼ 2.4 p.p." in txt and "Philco" in txt and "08/11" in txt
    assert "estável" in app._cockpit_headline(0.31, 0.312, None, None)
    assert app._cockpit_headline(None, None, None, None).startswith("Sem share")


def test_rotulo_direto_no_ultimo_ponto_com_valor() -> None:
    import plotly.graph_objects as go

    fig = go.Figure([
        go.Scatter(x=[1, 2, 3], y=[10, 11, None], mode="lines", name="Midea",
                   line=dict(color="#1a56db")),
        go.Scatter(x=[1, 2, 3], y=[5, 6, 7], mode="lines+markers", name="Philco"),
    ])
    app._label_line_ends(fig)
    notas = {a.text: (a.x, a.y) for a in fig.layout.annotations}
    assert notas["<b>Midea</b>"] == (2, 11)
    assert notas["<b>Philco</b>"] == (3, 7)
    assert fig.layout.showlegend is False


def test_rotulo_direto_desiste_com_linhas_demais() -> None:
    import plotly.graph_objects as go

    fig = go.Figure([go.Scatter(x=[1, 2], y=[i, i], mode="lines", name=str(i))
                     for i in range(9)])
    app._label_line_ends(fig)
    assert not fig.layout.annotations


def test_metodologia_longa_fica_no_como_ler() -> None:
    """Texto fixo longo de metodologia vai em `_como_ler`, não em `st.caption`.

    Regra de `docs/UX_PAINEIS.md`: a resposta vem antes da metodologia. Uma
    caption fixa com mais de 200 caracteres numa página é, na prática,
    metodologia no caminho do número.
    """
    import ast
    from pathlib import Path

    src = Path(app.__file__).read_text(encoding="utf-8")
    longas = []
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and getattr(node.func.value, "id", None) == "st"
                and node.func.attr == "caption" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and len(node.args[0].value) > 200):
            longas.append(node.lineno)
    assert not longas, f"st.caption com metodologia longa nas linhas {longas}: use _como_ler"
