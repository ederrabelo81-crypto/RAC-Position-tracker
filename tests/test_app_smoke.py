"""Smoke tests do dashboard Streamlit (app.py).

Verifica que cada página renderiza o estado inicial sem lançar exceção —
incluindo as páginas de buy box (👑 Share of Buy Box) e 🩺 Data Health. Não
exige credenciais Supabase: sem conexão, as páginas mostram avisos/estado
vazio, mas não devem quebrar no carregamento.

As páginas são descobertas automaticamente do dict ``PAGES`` em app.py (via
AST, sem executar o módulo), então novas páginas entram no smoke test sozinhas.

Requer streamlit (instalado pelo SessionStart hook / requirements_app.txt);
caso contrário, o módulo é pulado.
"""
import ast
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _discover_pages() -> list[str]:
    """Extrai as chaves do dict PAGES de app.py sem executar o módulo."""
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "PAGES" for t in node.targets
        ) and isinstance(node.value, ast.Dict):
            return [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
    return []


PAGES = _discover_pages()


def test_pages_discovered():
    # Sanidade: o parser achou o dict PAGES e ele tem as páginas esperadas.
    assert len(PAGES) >= 10
    assert "👑 Share of Buy Box" in PAGES
    assert "🩺 Data Health" in PAGES


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_exception(page):
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.session_state["_current_page"] = page
    at.run()
    # at.exception é um ElementList (vazio = nenhuma exceção renderizada).
    assert not at.exception, f"Página '{page}' lançou exceção: {list(at.exception)}"
