"""
utils/url_state.py — recorte do painel na URL (Fase 2 de UX, Nov/2026).

Até aqui o recorte (período, canal, plataformas, marcas) vivia só na sessão do
navegador: o alerta do Telegram dizia "ML degradado" e o leitor abria o painel
na home, nos últimos 7 dias, e refazia o filtro à mão. Agora o recorte vai na
query string — ``/buy-box?de=2026-11-01&ate=2026-11-08&plat=Magalu`` — e:

* o painel aplica a URL ao abrir (``decode_filters``) e reescreve a URL a cada
  mudança (``encode_filters``): copiar a barra de endereço = compartilhar o
  recorte;
* os alertas montam o link com ``dashboard_link`` (base em
  ``RAC_DASHBOARD_URL``; sem ela o alerta sai como antes, sem link).

Período padrão (últimos 7 dias até hoje) NÃO vai para a URL: link sem ``de``/
``ate`` é "rolante" — aberto amanhã, mostra os 7 dias de amanhã. Quem quer um
período fixo (o do alerta) manda as datas explicitamente.

Módulo puro (sem Streamlit): ``tests/test_url_state.py``.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import urlencode

#: Período padrão do painel (dias até hoje, inclusive o começo).
PERIODO_PADRAO_DIAS = 7

_SEP = ","


def periodo_padrao(hoje: date) -> tuple:
    """(início, fim) do período padrão do painel."""
    return hoje - timedelta(days=PERIODO_PADRAO_DIAS), hoje


def _lista(valor: Any) -> List[str]:
    """Aceita 'a,b', ['a','b'] ou ['a,b'] (o st.query_params devolve str)."""
    if valor is None:
        return []
    itens = valor if isinstance(valor, (list, tuple)) else [valor]
    out: List[str] = []
    for item in itens:
        out.extend(p.strip() for p in str(item).split(_SEP) if p.strip())
    return list(dict.fromkeys(out))


def _data(valor: Any) -> Optional[date]:
    if isinstance(valor, (list, tuple)):
        valor = valor[0] if valor else None
    try:
        return date.fromisoformat(str(valor)) if valor else None
    except ValueError:
        return None


def encode_filters(filtros: Mapping[str, Any], hoje: date,
                   fontes_padrao: Iterable[str] = ()) -> Dict[str, str]:
    """Recorte → parâmetros de URL (só o que difere do padrão).

    Args:
        filtros: ``inicio``, ``fim`` (date), ``canais``, ``plataformas``,
            ``marcas``, ``fontes`` (listas), ``comparar`` (bool).
        hoje: Dia de hoje (BRT) — decide se o período é o padrão rolante.
        fontes_padrao: Fontes ligadas por padrão (omitidas da URL).
    """
    qp: Dict[str, str] = {}
    ini, fim = filtros.get("inicio"), filtros.get("fim")
    if ini and fim and (ini, fim) != periodo_padrao(hoje):
        qp["de"], qp["ate"] = ini.isoformat(), fim.isoformat()
    for chave, campo in (("canal", "canais"), ("plat", "plataformas"), ("marca", "marcas")):
        valores = [v for v in (filtros.get(campo) or []) if v]
        if valores:
            qp[chave] = _SEP.join(valores)
    fontes = list(filtros.get("fontes") or [])
    if fontes and sorted(fontes) != sorted(fontes_padrao):
        qp["fonte"] = _SEP.join(fontes)
    if filtros.get("comparar"):
        qp["comparar"] = "1"
    return qp


def decode_filters(qp: Mapping[str, Any], validos: Mapping[str, Iterable[str]],
                   hoje: date) -> Dict[str, Any]:
    """Parâmetros de URL → recorte, descartando o que não é opção válida.

    Valor fora das opções some em silêncio de propósito: o multiselect do
    Streamlit LEVANTA erro se o session_state tiver valor fora das opções, e
    um link antigo (plataforma renomeada) não pode derrubar a página.

    Args:
        qp: ``st.query_params`` (ou dict equivalente).
        validos: ``{"canais": [...], "plataformas": [...], "marcas": [...],
            "fontes": [...]}``.
        hoje: Limite superior das datas (período no futuro é cortado em hoje).

    Returns:
        Só as chaves presentes e válidas na URL.
    """
    out: Dict[str, Any] = {}
    ini, fim = _data(qp.get("de")), _data(qp.get("ate"))
    if ini and fim:
        if ini > fim:
            ini, fim = fim, ini
        fim = min(fim, hoje)
        if ini <= fim:
            out["inicio"], out["fim"] = ini, fim
    for chave, campo in (("canal", "canais"), ("plat", "plataformas"),
                         ("marca", "marcas"), ("fonte", "fontes")):
        if chave in qp:
            permitidos = set(validos.get(campo) or [])
            valores = [v for v in _lista(qp.get(chave)) if v in permitidos]
            if valores:
                out[campo] = valores
    if str(qp.get("comparar", "")).strip() in ("1", "true", "sim"):
        out["comparar"] = True
    return out


def dashboard_link(pagina: str = "", filtros: Optional[Mapping[str, Any]] = None,
                   hoje: Optional[date] = None, base: Optional[str] = None) -> Optional[str]:
    """Link para uma página do painel com o recorte embutido.

    Args:
        pagina: ``url_path`` da página (``""`` = home/Cockpit). Os caminhos
            válidos são os de ``app._slug`` — ``tests/test_url_state.py``
            confere os usados pelos alertas.
        filtros: Recorte (mesmo formato de ``encode_filters``).
        hoje: Referência do período padrão (default: hoje).
        base: URL do painel; default ``RAC_DASHBOARD_URL``.

    Returns:
        A URL, ou ``None`` sem ``RAC_DASHBOARD_URL`` (o alerta segue sem link).
    """
    base = (base if base is not None else os.getenv("RAC_DASHBOARD_URL", "")).strip()
    if not base:
        return None
    url = base.rstrip("/") + ("/" + pagina.strip("/") if pagina else "/")
    qp = encode_filters(filtros or {}, hoje or date.today())
    return url + ("?" + urlencode(qp) if qp else "")


def recortes_padrao(hoje: date) -> Dict[str, Dict[str, Any]]:
    """Recortes salvos que sempre existem (definidos em código, não somem).

    Os salvos pelo usuário moram num arquivo do servidor e somem a cada
    reinício do Streamlit Cloud; estes garantem que o atalho do dia a dia
    nunca desapareça.
    """
    return {
        "📅 Últimos 7 dias · tudo": {
            "inicio": hoje - timedelta(days=7), "fim": hoje,
            "canais": [], "plataformas": [], "marcas": [], "comparar": False},
        "🛒 Marketplaces · 7 dias": {
            "inicio": hoje - timedelta(days=7), "fim": hoje,
            "canais": ["Marketplaces"], "plataformas": [], "marcas": [], "comparar": False},
        "🔵 Midea · 30 dias": {
            "inicio": hoje - timedelta(days=30), "fim": hoje,
            "canais": [], "plataformas": [], "marcas": ["Midea"], "comparar": False},
        "⏱️ Ontem e hoje": {
            "inicio": hoje - timedelta(days=1), "fim": hoje,
            "canais": [], "plataformas": [], "marcas": [], "comparar": False},
    }


def linha_telegram(pagina: str = "", filtros: Optional[Mapping[str, Any]] = None,
                   rotulo: str = "Abrir no painel", hoje: Optional[date] = None) -> str:
    """Linha HTML (Telegram) com o link do painel, ou ``""`` sem ``RAC_DASHBOARD_URL``.

    O alerta continua idêntico quando a variável não existe — o link é um
    extra, nunca a condição para o alerta sair.
    """
    import html

    url = dashboard_link(pagina, filtros, hoje=hoje)
    if not url:
        return ""
    return f'🔗 <a href="{html.escape(url, quote=True)}">{html.escape(rotulo)}</a>'
