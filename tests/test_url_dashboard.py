"""O painel aplica o recorte da URL e reescreve a URL a partir dos filtros."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(ROOT / "app.py")


def test_url_define_o_recorte_inicial() -> None:
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["de"] = "2026-06-08"
    at.query_params["ate"] = "2026-06-15"
    at.query_params["canal"] = "Marketplaces"
    at.query_params["comparar"] = "1"
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert tuple(at.session_state["gf_dates"]) == (date(2026, 6, 8), date(2026, 6, 15))
    assert at.session_state["gf_canais"] == ["Marketplaces"]
    assert at.session_state["gf_compare"] is True


def test_link_invalido_nao_derruba() -> None:
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["plat"] = "Plataforma Que Nao Existe"
    at.query_params["de"] = "lixo"
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
