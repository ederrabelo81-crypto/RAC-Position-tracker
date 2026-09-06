"""Regressão — o slider de dias TEM de mexer no histórico do painel do seller.

Bug relatado (06/09/2026): "ao mudar a quantidade de dias os dados históricos
parecem não mudar". Causa raiz — a janela era `date.today() - dias` com teto
fixo de 60 dias, enquanto o fato tinha só 8 dias de histórico (28/08 a 04/09)
e a coleta atrasava 2 dias:

  * qualquer janela ≥ ~10 dias já continha TODO o dado, então arrastar o slider
    de 14 para 60 não mudava uma linha na tela — o sintoma exato;
  * o atraso da coleta comia o fim baixo do slider (dias contados de um "hoje"
    que ainda não tinha coletado).

A correção ancora a janela no último dia COM dado (`janela`), amarra o teto do
slider ao histórico que existe (`dias_max`) e conta o intervalo com `dias - 1`,
porque o filtro do painel (`.gte("data", desde)`) é inclusivo — sem o `- 1` o
último notch ficaria inerte (`dias=7` e `dias=8` mostrariam os mesmos 8 dias).

Estes testes fixam as duas funções puras. São **genuinamente herméticos**:
importam só `seller_app/janela.py`, que não depende de streamlit/pandas/supabase
nem sobe o runtime do painel — rodam em qualquer ambiente, com ou sem o
dashboard instalado (por isso NÃO há `importorskip` aqui).

Uso:
    python tests/test_seller_app_janela.py     # standalone (PASS/FAIL + exit)
    pytest tests/test_seller_app_janela.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Import direto do módulo puro pelo caminho — sem tocar em `seller_app/app.py`
# (que arrastaria streamlit/pandas/supabase e o `st.set_page_config`).
_spec = importlib.util.spec_from_file_location(
    "_seller_janela", ROOT / "seller_app" / "janela.py")
_janela_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_janela_mod)
dias_max = _janela_mod.dias_max
janela = _janela_mod.janela


# O intervalo que reproduz o bug: 8 dias de dado, 2 dias de atraso.
MIN_DATA, MAX_DATA = date(2026, 8, 28), date(2026, 9, 4)
INTERVALO = (MIN_DATA, MAX_DATA)
SPAN = (MAX_DATA - MIN_DATA).days + 1  # 8


def _dias_de_dado_cobertos(dias: int) -> int:
    """Quantos dias COM dado a janela de `dias` mostraria, dado o filtro
    inclusivo `.gte("data", desde)` sobre o intervalo real [MIN_DATA, MAX_DATA].
    É a medida do que o usuário vê na tela — não a data-âncora crua."""
    desde = janela(dias, INTERVALO)
    inicio = max(desde, MIN_DATA)
    if inicio > MAX_DATA:
        return 0
    return (MAX_DATA - inicio).days + 1


def test_dias_max_amarrado_ao_historico_real():
    """Teto do slider = span do dado, não 60 fixo. Aqui: 8 dias → teto 8."""
    assert dias_max(INTERVALO) == 8, (
        "com 8 dias de dado o slider não pode ir a 60 — a metade de cima "
        "seria inerte e o histórico não mudaria, que é o bug")


def test_dias_max_limites():
    """Piso 4 (garante min<max no slider) e teto 60 (dado longo)."""
    assert dias_max((None, None)) == 60, "sem dado, teto genérico"
    assert dias_max((date(2026, 9, 4), date(2026, 9, 4))) == 4, "1 dia → piso 4"
    assert dias_max((date(2020, 1, 1), date(2026, 9, 4))) == 60, "muito dado → 60"


def test_janela_conta_dias_inclusive():
    """`dias` dias na tela, não `dias + 1`: o filtro `.gte` é inclusivo.

    Sem o `- 1`, "3 dias" mostraria 4 dias — e o docstring prometeria o
    contrário. Ancorada no último dia com dado (04/09), a janela de N dias
    começa em `MAX_DATA - (N-1)`.
    """
    assert janela(3, INTERVALO) == MAX_DATA - timedelta(days=2)
    assert janela(8, INTERVALO) == MAX_DATA - timedelta(days=7)
    assert _dias_de_dado_cobertos(3) == 3, "3 dias = 3 dias de dado real"


def test_todo_notch_muda_o_que_aparece_inclusive_o_ultimo():
    """O coração do bug: CADA notch precisa mudar o que a tela mostra.

    A versão anterior só garantia `desde` distinto — mas com o off-by-one o
    último notch (`dias=7` e `dias=8` com 8 dias de dado) renderizava o MESMO
    histórico, deixando o sintoma vivo no topo do slider. Aqui a asserção é
    sobre o que o usuário VÊ: os dias de dado cobertos têm de crescer de forma
    estrita em todo o curso do slider, sem platô no topo.
    """
    cobertos = [_dias_de_dado_cobertos(d) for d in range(3, dias_max(INTERVALO) + 1)]
    assert cobertos == sorted(cobertos), "mais dias nunca mostra menos"
    assert len(set(cobertos)) == len(cobertos), (
        "cada notch mostra um nº de dias diferente — nenhum notch inerte, "
        f"nem o último (cobertos={cobertos})")
    assert cobertos[-1] == SPAN, "o topo do slider cobre o histórico inteiro, exato"


def test_janela_monotonica():
    """Mais dias → começa mais cedo (âncora fixa no último dia com dado)."""
    desdes = [janela(d, INTERVALO) for d in range(3, dias_max(INTERVALO) + 1)]
    assert desdes == sorted(desdes, reverse=True)
    assert len(set(desdes)) == len(desdes)


def test_janela_sem_dado_cai_em_hoje():
    """Sem histórico nenhum, âncora volta a ser hoje — não pode quebrar."""
    assert janela(5, (None, None)) == date.today() - timedelta(days=4)


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
