"""Redundancy tests — filtros globais enxutos + seletor de Fonte de Dados.

Roda o dashboard Streamlit *headless* (streamlit.testing.v1.AppTest) e exercita,
de ponta a ponta, as mudanças do PR:

  1. Helpers puros: ``_gf_sources`` (padrão = ambas; vazio→ambas; seleção única)
     e ``_gf_cmp_dates`` (janela de comparação automática).
  2. Gating de fonte: ``query_coletas`` / ``query_pricetrack_daily`` curto-
     circuitam ANTES de tocar o banco quando a fonte está desligada.
  3. Compatibilidade de assinatura: todas as funções com o novo parâmetro
     ``sources_tuple`` aceitam o kwarg exatamente como os call-sites passam.
  4. Sweep de fumaça: cada uma das 20 páginas renderiza SEM exceção sob as três
     combinações de fonte (ambas / só coletas / só pricetrack), provando que
     nada quebrou e que tudo segue funcionando como hoje (sem Supabase → as
     páginas mostram aviso de "sem dados", não estouram).

Sem credenciais Supabase: ``_get_supabase()`` devolve ``None`` e as consultas
voltam vazias — perfeito para validar fluxo/headers sem rede.

Uso:
    python tests/test_global_filters.py          # standalone (PASS/FAIL + exit code)
    pytest tests/test_global_filters.py -q       # via pytest
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

APP_FILE = str(ROOT / "app.py")
RUN_TIMEOUT = 90  # generoso: primeira execução compila o app inteiro

# As 20 páginas registradas em PAGES (app.py).
PAGE_NAMES = [
    "🏠 Overview", "🚨 Top Movers", "📊 Results", "📈 Price Evolution",
    "📊 Market Analytics", "🗂️ Ficha do Produto", "🏆 BuyBox Position",
    "👑 Share of Buy Box", "⭐ Reputação & Avaliações", "📣 SoV Patrocinado",
    "🛡️ Price Compliance", "📦 Availability", "🧠 Competitive Intelligence",
    "🚀 Run Collection", "📧 Email Digest", "🔔 Price Anomalies",
    "📂 Import History", "🩺 Data Health", "🤖 Automação", "🧬 Família & SKU",
]

SOURCE_COMBOS = [
    ["coletas", "pricetrack"],  # padrão (idêntico a hoje)
    ["coletas"],                # só Coletas Python
    ["pricetrack"],             # só PriceTrack
]


# ---------------------------------------------------------------------------
# 1) Helpers puros — _gf_sources / _gf_cmp_dates / _gf_sources_key
# ---------------------------------------------------------------------------

def _helper_harness() -> None:
    import streamlit as st
    import app
    from datetime import date as _d

    out: dict = {}
    # _gf_sources: chave ausente → ambas (padrão histórico)
    out["default"] = app._gf_sources()
    # _gf_sources: lista vazia → cai de volta para ambas (nunca zera tudo)
    st.session_state["gf_sources"] = []
    out["empty_fallback"] = app._gf_sources()
    # _gf_sources: seleção única é respeitada, na ordem canônica
    st.session_state["gf_sources"] = ["pricetrack"]
    out["only_pt"] = app._gf_sources()
    st.session_state["gf_sources"] = ["coletas"]
    out["only_col"] = app._gf_sources()
    out["key_is_tuple"] = isinstance(app._gf_sources_key(), tuple)

    # _gf_cmp_dates: janela anterior, mesma duração, sem overlap.
    st.session_state["gf_dates"] = (_d(2026, 6, 8), _d(2026, 6, 15))
    out["cmp"] = app._gf_cmp_dates()
    st.session_state["_out"] = out


def test_helpers() -> None:
    at = AppTest.from_function(_helper_harness)
    at.run(timeout=RUN_TIMEOUT)
    assert not at.exception, f"helper harness raised: {list(at.exception)}"
    out = at.session_state["_out"]
    assert out["default"] == ["coletas", "pricetrack"], out["default"]
    assert out["empty_fallback"] == ["coletas", "pricetrack"], out["empty_fallback"]
    assert out["only_pt"] == ["pricetrack"], out["only_pt"]
    assert out["only_col"] == ["coletas"], out["only_col"]
    assert out["key_is_tuple"] is True
    cs, ce = out["cmp"]
    # atual = 08/06→15/06 (8 dias inclusivos) ⇒ cmp = 31/05→07/06
    assert ce == date(2026, 6, 7), ce
    assert cs == date(2026, 5, 31), cs


# ---------------------------------------------------------------------------
# 2) Gating de fonte — curto-circuito ANTES de tocar o banco
# ---------------------------------------------------------------------------

def _gating_harness() -> None:
    import streamlit as st
    import app
    from datetime import date as _d

    class _Boom:
        """Cliente sentinela: estoura se a query chegar a tocá-lo."""
        def table(self, *a, **k):
            raise AssertionError("query tocou o banco com a fonte desligada")

    app._get_supabase = lambda: _Boom()  # type: ignore[assignment]
    s, e = _d(2026, 6, 1), _d(2026, 6, 7)
    out: dict = {}

    # coletas desligada → query_coletas devolve vazio sem tocar o _Boom
    st.session_state["gf_sources"] = ["pricetrack"]
    out["coletas_off_rows"] = len(app.query_coletas(s, e))

    # pricetrack desligada → query_pricetrack_daily devolve vazio idem
    st.session_state["gf_sources"] = ["coletas"]
    out["pt_off_rows"] = len(app.query_pricetrack_daily(s, e))

    # wrapper coletas-only com cliente não-nulo + coletas off → vazio (guard)
    out["overview_guard_rows"] = len(
        app._overview_data(str(s), str(e), (), (), sources_tuple=("pricetrack",))
    )
    st.session_state["_out"] = out


def test_source_gating() -> None:
    at = AppTest.from_function(_gating_harness)
    at.run(timeout=RUN_TIMEOUT)
    assert not at.exception, f"gating harness raised: {list(at.exception)}"
    out = at.session_state["_out"]
    assert out["coletas_off_rows"] == 0
    assert out["pt_off_rows"] == 0
    assert out["overview_guard_rows"] == 0


# ---------------------------------------------------------------------------
# 3) Compatibilidade de assinatura — todos os call-sites do novo kwarg
# ---------------------------------------------------------------------------

def _signature_harness() -> None:
    import streamlit as st
    import app
    from datetime import date as _d

    app._get_supabase = lambda: None  # type: ignore[assignment]  # força vazio rápido
    s, e = _d(2026, 6, 1), _d(2026, 6, 7)
    combos = [("coletas", "pricetrack"), ("coletas",), ("pricetrack",)]
    out: dict = {"errors": []}
    for src in combos:
        try:
            app._overview_data(str(s), str(e), (), (), sources_tuple=src)
            app._price_data(str(s), str(e), (), (), sources_tuple=src)
            app._pt_top_movers_data(str(s), str(e), (), (), (), (), sources_tuple=src)
            app._query_pt_compliance(7, sources_tuple=src)
            app._query_products_history(("X",), str(s), str(e), sources_tuple=src)
        except TypeError as exc:  # assinatura incompatível com o call-site
            out["errors"].append(f"{src}: {exc}")
    st.session_state["_out"] = out


def test_signatures_accept_sources_tuple() -> None:
    at = AppTest.from_function(_signature_harness)
    at.run(timeout=RUN_TIMEOUT)
    assert not at.exception, f"signature harness raised: {list(at.exception)}"
    out = at.session_state["_out"]
    assert out["errors"] == [], out["errors"]


# ---------------------------------------------------------------------------
# 4) Sweep de fumaça — 20 páginas × 3 combinações de fonte, sem exceção
# ---------------------------------------------------------------------------

def _run_page(page: str, sources: list[str]):
    at = AppTest.from_file(APP_FILE)
    at.session_state["_current_page"] = page
    at.session_state["gf_sources"] = sources
    at.session_state["gf_dates"] = (date(2026, 6, 8), date(2026, 6, 15))
    at.session_state["gf_compare"] = True  # exercita também o caminho de comparação
    at.run(timeout=RUN_TIMEOUT)
    return at


def test_all_pages_render_under_every_source() -> None:
    failures: list[str] = []
    for page in PAGE_NAMES:
        for sources in SOURCE_COMBOS:
            try:
                at = _run_page(page, sources)
            except Exception as exc:  # falha de execução do próprio AppTest
                failures.append(f"{page} / {sources} → runner crash: {exc!r}")
                continue
            if at.exception:
                msgs = "; ".join(str(e.value) for e in at.exception)
                failures.append(f"{page} / {sources} → {msgs}")
    assert not failures, "Páginas com exceção:\n  " + "\n  ".join(failures)


# ---------------------------------------------------------------------------
# 5) Painel global enxuto — Fonte de Dados presente; catálogo fino removido
# ---------------------------------------------------------------------------

def test_global_panel_is_lean() -> None:
    """No Overview (que só usa os filtros globais) a sidebar deve conter
    apenas os recortes transversais; os widgets de catálogo saíram do global."""
    at = AppTest.from_file(APP_FILE)
    at.session_state["_current_page"] = "🏠 Overview"
    at.run(timeout=RUN_TIMEOUT)
    assert not at.exception, f"Overview raised: {list(at.exception)}"

    labels = {ms.label for ms in at.multiselect}
    assert "Fonte de Dados" in labels, f"faltou Fonte de Dados; tem {labels}"
    assert "Plataformas" in labels, labels
    assert "Marcas" in labels, labels
    # Removidos do painel global (continuam por página, não aqui):
    for gone in ("Estado do match", "Família", "SKU do catálogo",
                 "Capacidade BTU (catálogo)"):
        assert gone not in labels, f"{gone!r} ainda no painel global: {labels}"


# ---------------------------------------------------------------------------
# Runner standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        ("helpers (_gf_sources / _gf_cmp_dates)", test_helpers),
        ("source gating (curto-circuito)",        test_source_gating),
        ("assinaturas sources_tuple",             test_signatures_accept_sources_tuple),
        ("painel global enxuto",                  test_global_panel_is_lean),
        ("sweep 20 páginas × 3 fontes",           test_all_pages_render_under_every_source),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  ✅  {name}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  ❌  {name}\n        {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR 💥  {name}\n        {type(exc).__name__}: {exc}")
    print("-" * 60)
    print("RESULTADO:", "TODOS PASSARAM ✅" if failed == 0 else f"{failed} FALHA(S) ❌")
    sys.exit(1 if failed else 0)
