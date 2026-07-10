"""Testes das guardas de plausibilidade de preço do dashboard (`app.py`).

Cobrem as duas funções puras que sanitizam o preço ANTES de qualquer `min()`
nas visões de buy box / menor preço:

  • ``_is_placeholder_price`` — pega o "preço de gaveta" alto (…999,00 / …9999)
    que a loja usa quando o item está indisponível.
  • ``_is_implausible_price`` — pega o erro oposto: valor baixo demais para ser
    um ar-condicionado real (< R$1.000), tipicamente um acessório/suporte
    capturado no lugar do aparelho. É o bug do R$339 da TCL virando "vencedor"
    de uma linha do Daily Price Vision.

Rodar:
    python tests/test_price_guards.py     # standalone (PASS/FAIL + exit code)
    pytest tests/test_price_guards.py -q  # via pytest
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402  — importável sem renderizar (entry sob __main__)


# ── _is_implausible_price ──────────────────────────────────────────────────
IMPLAUSIBLE_CASES = [
    # (valor, esperado, descrição)
    (339.0,   True,  "bug real: suporte TCL R$339 vira 'vencedor' da linha"),
    (0.0,     True,  "preço zerado"),
    (99.90,   True,  "acessório/controle remoto"),
    (999.99,  True,  "logo abaixo do piso"),
    (1000.0,  False, "exatamente no piso — plausível (limite inclusivo)"),
    (1100.0,  False, "janela 7.500 BTU mais barata do mercado"),
    (1897.93, False, "preço real de AC (da própria imagem do report)"),
    (12999.0, False, "AC premium caro — alto, mas plausível"),
    (None,    False, "None nunca é 'implausível' (é ausência de dado)"),
    (float("nan"), False, "NaN nunca é 'implausível'"),
    ("abc",   False, "texto não numérico é ignorado, não filtrado"),
]


def test_is_implausible_price():
    for value, expected, desc in IMPLAUSIBLE_CASES:
        got = app._is_implausible_price(value)
        assert got is expected, f"{desc}: {value!r} → {got}, esperado {expected}"


def test_placeholder_and_implausible_are_complementary():
    """Um preço real de AC não é pego por NENHUMA das duas guardas."""
    for real in (1100.0, 1897.93, 2184.05, 5490.0):
        assert not app._is_placeholder_price(real)
        assert not app._is_implausible_price(real)


def test_min_plausible_floor_matches_domain():
    """O piso documentado bate com a regra de negócio (R$1.000)."""
    assert app.MIN_PLAUSIBLE_PRICE_BRL == 1000.0


def _run_standalone() -> int:
    failures = 0
    for value, expected, desc in IMPLAUSIBLE_CASES:
        got = app._is_implausible_price(value)
        ok = got is expected
        print(f"[{'PASS' if ok else 'FAIL'}] {value!r:>14} → {got!s:<5} ({desc})")
        failures += not ok
    for real in (1100.0, 1897.93, 2184.05, 5490.0):
        ok = not app._is_placeholder_price(real) and not app._is_implausible_price(real)
        print(f"[{'PASS' if ok else 'FAIL'}] real {real:>10} passa nas duas guardas")
        failures += not ok
    ok = app.MIN_PLAUSIBLE_PRICE_BRL == 1000.0
    print(f"[{'PASS' if ok else 'FAIL'}] piso == 1000.0")
    failures += not ok
    print("\n" + ("✅ TODOS OS TESTES PASSARAM" if not failures
                  else f"❌ {failures} FALHA(S)"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
