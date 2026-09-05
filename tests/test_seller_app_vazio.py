"""Regressão — o painel do seller não pode MORRER quando a consulta volta vazia.

Bug de produção (05/09/2026): o app de `seller_app/app.py` (publicado como
`sellers_app/streamlit_app.py`) parou de inicializar. O log do Streamlit Cloud
não mostrava traceback nenhum — o container subia, o uvicorn subia, e a página
morria depois, no script.

Causa: `pd.DataFrame(<lista vazia>)` não tem só zero linhas — tem zero
COLUNAS. Quando o `SELLER` do secrets nomeava um seller sem linha na janela, o
`fato` voltava sem esquema e a primeira aba a tocar uma coluna estourava
`KeyError: 'virou_no_turno'`, derrubando a página inteira. O próprio comentário
do código prometia o contrário ("Sem KPI, mas as abas CONTINUAM: a de Cobertura
é justamente o que separa 'não houve oferta' de 'a coleta não rodou'").

O gatilho foi a canonização de sellers: "Comprebel" virou variante de
"Bel Micro" e "GoCompras" de "Denteck" (`utils/seller_names.py`), então um
secret travado na grafia velha passou a buscar um seller que não existe mais —
e o PostgREST devolve `[]` com HTTP 200, sem erro e sem log.

Hermético: os três carregadores entram por dublê, nada toca o Supabase.

Uso:
    python tests/test_seller_app_vazio.py     # standalone (PASS/FAIL + exit code)
    pytest tests/test_seller_app_vazio.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APP = ROOT / "seller_app" / "app.py"

pytest.importorskip("streamlit", reason="dashboard não instalado neste ambiente")
from streamlit.testing.v1 import AppTest  # noqa: E402

# Duas linhas de mercado bastam: o seletor e o ranking só precisam existir.
MERCADO = [
    dict(data="2026-09-04", plataforma="Magalu", seller_canonical="Web Continental",
         produtos_detidos=40, produtos_universo=661, viradas_a_favor=2,
         share_buybox_pct=6.05),
    dict(data="2026-09-04", plataforma="Magalu", seller_canonical="Bel Micro",
         produtos_detidos=12, produtos_universo=661, viradas_a_favor=0,
         share_buybox_pct=1.82),
]
COBERTURA = [
    dict(data="2026-09-04", turno=t, plataforma="Magalu", observado=True,
         linhas=10, ofertas=8, heartbeat_ok=True, job_id="rac_local_tarde",
         atualizado_em=None)
    for t in ("Abertura", "Tarde", "Fechamento")
]
# Uma oferta com os tipos como o PostgREST entrega: `numeric` chega STRING.
FATO = [dict(
    data="2026-09-04", turno="Tarde", plataforma="Magalu", superficie="marketplace",
    offer_key="v1|mgl:123|belmicro", marketplace_product_id="123",
    produto="Ar Condicionado Midea 12000 BTUs", marca="Midea", preco="2184.05",
    posicao_melhor=3, posicao_mediana="4.0", keywords_presente=2,
    detentor_buybox=True, detentor_anterior=None, virou_no_turno=False,
    qtd_sellers=7, tipo_seller="3P", identidade_suspeita=False)]


def _rodar(seller: str, fato: list[dict]) -> AppTest:
    """Executa o app com os carregadores dublados e o secret `SELLER` travado."""
    dubles = f"""
import pandas as _pd
carregar_mercado = lambda desde: _tipar(_pd.DataFrame({MERCADO!r}))
carregar = lambda s, d: (
    _tipar(_quadro({fato!r}, _SELECT_FATO)),
    _tipar(_pd.DataFrame([m for m in {MERCADO!r} if m["seller_canonical"] == s])),
    _pd.DataFrame({COBERTURA!r}))
carregar_perdidos = lambda s, d: _tipar(_quadro([], _SELECT_PERDIDOS))
main()
"""
    fonte = APP.read_text(encoding="utf-8").replace(
        'if __name__ == "__main__":\n    main()', dubles)
    at = AppTest.from_string(fonte, default_timeout=120)
    at.secrets["SUPABASE_URL"] = "https://exemplo.supabase.co"
    at.secrets["SUPABASE_ANON_KEY"] = "chave-anon-de-teste"
    at.secrets["SELLER"] = seller
    return at.run()


def test_quadro_vazio_carrega_o_esquema():
    """O esqueleto é a correção: sem ele todo `df["coluna"]` adiante é KeyError."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_seller_app", APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    vazio = mod._quadro([], mod._SELECT_FATO)
    assert vazio.empty, "sem linha nenhuma, como a consulta devolveu"
    assert list(vazio.columns) == mod._SELECT_FATO.split(","), (
        "as colunas do dataframe têm de ser as MESMAS pedidas no select — "
        "é o que mantém o esqueleto e o PostgREST de acordo")
    # As três colunas que a página toca fora de qualquer guarda de `vazio`.
    for coluna in ("virou_no_turno", "detentor_buybox", "identidade_suspeita"):
        assert coluna in vazio.columns


def test_seller_sem_linha_nao_derruba_a_pagina():
    """O caso que quebrou produção: secret travado num canônico aposentado."""
    at = _rodar("Comprebel", [])
    assert not at.exception, (
        "a página estourou com a consulta vazia: "
        + "; ".join(str(e.value) for e in at.exception))
    assert len(at.tabs) == 5, "as 5 abas continuam — a de Cobertura é a resposta"
    assert any("Sem oferta registrada" in w.value for w in at.warning)


def test_seller_sem_linha_aponta_o_secret_desatualizado():
    """Cobertura não distingue 'nome errado' de 'não coletou' — a tela tem de dizer."""
    at = _rodar("Comprebel", [])
    erros = " ".join(e.value for e in at.error)
    assert "SELLER" in erros and "Comprebel" in erros, (
        "sem esta mensagem o sintoma fica indistinguível de 'a coleta não rodou' "
        "e o conserto (Settings → Secrets) não aparece em lugar nenhum")
    legendas = " ".join(c.value for c in at.caption)
    assert "Web Continental" in legendas, "listar os nomes válidos é o caminho de saída"


def test_seller_com_dado_segue_renderizando():
    """A correção não pode custar nada ao caminho normal."""
    at = _rodar("Bel Micro", FATO)
    assert not at.exception, "; ".join(str(e.value) for e in at.exception)
    assert len(at.tabs) == 5
    assert not at.error
    assert not any("Sem oferta registrada" in w.value for w in at.warning)


if __name__ == "__main__":
    falhas = 0
    for nome, fn in sorted(
            (n, f) for n, f in globals().items() if n.startswith("test_")):
        try:
            fn()
            print(f"PASS  {nome}")
        except AssertionError as erro:
            falhas += 1
            print(f"FAIL  {nome}: {erro}")
    print("PASS" if not falhas else f"FAIL ({falhas})")
    sys.exit(1 if falhas else 0)
