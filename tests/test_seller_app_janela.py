"""Regressão — o slider de dias TEM de mexer no histórico do painel do seller.

Bug relatado (06/09/2026): "ao mudar a quantidade de dias os dados históricos
parecem não mudar". Causa raiz — a janela era `date.today() - dias` com teto
fixo de 60 dias, enquanto o fato tinha só 8 dias de histórico (28/08 a 04/09)
e a coleta atrasava 2 dias:

  * qualquer janela ≥ ~10 dias já continha TODO o dado, então arrastar o slider
    de 14 para 60 não mudava uma linha na tela — o sintoma exato;
  * o atraso da coleta comia o fim baixo do slider (dias contados de um "hoje"
    que ainda não tinha coletado).

A correção ancora a janela no último dia COM dado (`_janela`), amarra o teto do
slider ao histórico que existe (`_dias_max`) e passa a dizer a cobertura na
tela (`_legenda_janela`). Estes testes fixam as duas funções puras — hermético,
nada toca o Supabase nem o runtime do Streamlit.

Uso:
    python tests/test_seller_app_janela.py     # standalone (PASS/FAIL + exit)
    pytest tests/test_seller_app_janela.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APP = ROOT / "seller_app" / "app.py"

pytest.importorskip("streamlit", reason="dashboard não instalado neste ambiente")


def _mod():
    spec = importlib.util.spec_from_file_location("_seller_app_janela", APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# O intervalo que reproduz o bug: 8 dias de dado, 2 dias de atraso.
MIN_DATA, MAX_DATA = date(2026, 8, 28), date(2026, 9, 4)
INTERVALO = (MIN_DATA, MAX_DATA)


def test_dias_max_amarrado_ao_historico_real():
    """Teto do slider = span do dado, não 60 fixo. Aqui: 8 dias → teto 8."""
    m = _mod()
    assert m._dias_max(INTERVALO) == 8, (
        "com 8 dias de dado o slider não pode ir a 60 — a metade de cima "
        "seria inerte e o histórico não mudaria, que é o bug")


def test_dias_max_limites():
    """Piso 4 (garante min<max no slider) e teto 60 (dado longo)."""
    m = _mod()
    assert m._dias_max((None, None)) == 60, "sem dado, teto genérico"
    assert m._dias_max((date(2026, 9, 4), date(2026, 9, 4))) == 4, "1 dia → piso 4"
    assert m._dias_max((date(2020, 1, 1), date(2026, 9, 4))) == 60, "muito dado → 60"


def test_janela_ancora_no_ultimo_dia_com_dado():
    """`desde` conta a partir de `max_data`, não de `date.today()`.

    É o que remove a zona morta do atraso: 3 dias = 3 dias de dado real.
    """
    m = _mod()
    assert m._janela(3, INTERVALO) == MAX_DATA - timedelta(days=3)
    assert m._janela(8, INTERVALO) == MAX_DATA - timedelta(days=8)


def test_janela_muda_com_os_dias():
    """O coração do bug: mudar `dias` PRECISA mudar `desde`.

    `desde` é a chave de cache de todos os carregadores; se ele não anda, a
    consulta é a mesma e a tela não muda. Cada notch do slider tem de produzir
    um `desde` distinto e monotônico (mais dias → começa mais cedo).
    """
    m = _mod()
    desdes = [m._janela(d, INTERVALO) for d in range(3, m._dias_max(INTERVALO) + 1)]
    assert len(set(desdes)) == len(desdes), "cada janela tem um `desde` único"
    assert desdes == sorted(desdes, reverse=True), "mais dias → `desde` mais cedo"


def test_janela_sem_dado_cai_em_hoje():
    """Sem histórico nenhum, âncora volta a ser hoje — não pode quebrar."""
    m = _mod()
    assert m._janela(5, (None, None)) == date.today() - timedelta(days=5)


if __name__ == "__main__":
    falhas = 0
    for nome, fn in sorted(
            (n, f) for n, f in globals().items() if n.startswith("test_")):
        try:
            fn()
            print(f"PASS   {nome}")
        except AssertionError as erro:
            falhas += 1
            print(f"FAIL   {nome}\n        {erro}")
        except Exception as erro:  # noqa: BLE001
            falhas += 1
            print(f"ERROR  {nome}\n        {type(erro).__name__}: {erro}")
    print("PASS" if not falhas else f"FAIL ({falhas})")
    sys.exit(1 if falhas else 0)
