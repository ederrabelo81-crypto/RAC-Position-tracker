"""Lógica pura da janela de dias do painel do seller — sem Streamlit, sem rede.

Isolada do `app.py` de propósito: são funções puras (entram os dias pedidos e
o intervalo de dados disponível, sai a data-âncora da consulta). Fora do
módulo do Streamlit elas ficam testáveis **sem** subir o runtime do painel nem
importar `pandas`/`supabase` — é o que deixa `tests/test_seller_app_janela.py`
genuinamente hermético e rodável em qualquer ambiente, com ou sem streamlit
instalado (antes, importar o `app.py` inteiro arrastava `st.set_page_config` e
os decoradores de cache, e o `importorskip("streamlit")` acabava pulando em
silêncio os testes de lógica pura onde o painel não está instalado).
"""
from __future__ import annotations

from datetime import date, timedelta


def dias_max(intervalo: tuple[date | None, date | None]) -> int:
    """Teto do slider = dias de histórico que EXISTEM (limitado a [4, 60]).

    Fixar o teto em 60 fazia a metade de cima do slider ser inerte quando o
    histórico é curto — a origem do sintoma relatado. Amarrar o teto ao span
    real deixa todo o curso do slider mexer no dado, e o teto cresce sozinho
    conforme a coleta acumula dias. O piso 4 garante `min < max` para o
    `st.slider` (o mínimo é 3) mesmo com um ou dois dias só de dado.
    """
    min_data, max_data = intervalo
    if not min_data or not max_data:
        return 60
    span = (max_data - min_data).days + 1
    return max(4, min(60, span))


def janela(dias: int, intervalo: tuple[date | None, date | None]) -> date:
    """`desde` da janela, ancorado no último dia COM dado, não em hoje.

    Ancorar em `max_data` remove a zona morta que o atraso da coleta abria no
    fim baixo do slider: "3 dias" passa a valer 3 dias de dado real, não 3
    dias contados a partir de um hoje que ainda não coletou. Sem histórico
    nenhum, cai no comportamento antigo (âncora em hoje) para não quebrar.

    O `- 1` é deliberado: o filtro do painel é `.gte("data", desde)`, um
    intervalo INCLUSIVO `[desde, max_data]`. Com `max_data - dias` a janela
    cobriria `dias + 1` dias — "3 dias" mostraria 4, e o último notch do slider
    ficaria inerte (com 8 dias de histórico, `dias=7` e `dias=8` renderizariam
    os mesmos 8 dias). `dias - 1` faz cada notch valer exatamente `dias` dias
    de dado, e o topo do slider passa a cobrir o histórico inteiro sem sobra.
    """
    _, max_data = intervalo
    ancora = max_data or date.today()
    return ancora - timedelta(days=dias - 1)
