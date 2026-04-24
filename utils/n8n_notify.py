"""
utils/n8n_notify.py — Notificações executivas de coleta para N8N via webhook.

Gera um resumo executivo com:
  • Indicadores da coleta (volume, duração, preço médio Midea)
  • Top 5 quedas e altas de preço (deduplicado por produto, com keyword)
  • Ganhos de buybox da Midea (quem foi deslocado)
  • Perdas de buybox da Midea (quem assumiu)
  • Filtro de variações suspeitas (×10 parser errors)

Configure no .env:
    N8N_WEBHOOK_URL=http://localhost:5678/webhook/coleta
    N8N_TELEGRAM_CHAT_ID=123456789

No N8N, Parse Mode do nó Telegram deve ser: HTML
"""

import html
import os
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from loguru import logger

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass


# Filtros de variação de preço — evita ruído de parser errors e SKUs diferentes
_PRICE_MIN_DELTA_PCT   = 3.0    # variação mínima para aparecer
_PRICE_MIN_DELTA_REAIS = 50.0   # mínimo em R$ para aparecer
_PRICE_SUSPICIOUS_PCT  = 50.0   # acima disso é sinalizado com ⚠️
_PRICE_IGNORE_PCT      = 150.0  # acima disso é descartado (certamente erro)


# ---------------------------------------------------------------------------
# Helpers de webhook
# ---------------------------------------------------------------------------

def _webhook_url() -> Optional[str]:
    return os.getenv("N8N_WEBHOOK_URL", "").strip() or None


def _chat_id() -> str:
    return os.getenv("N8N_TELEGRAM_CHAT_ID", "").strip()


def _send(payload: Dict[str, Any]) -> bool:
    url = _webhook_url()
    if not url:
        return False
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.warning(f"[N8N] Falha ao notificar: {exc}")
        return False


def _esc(text: str) -> str:
    """Escape mínimo para HTML do Telegram (apenas <, >, &)."""
    return html.escape(str(text or ""))


def _fmt_brl(value: float) -> str:
    """Formata valor em R$ com separador de milhar BR (ex: 2850 -> R$ 2.850)."""
    try:
        return f"R$ {value:,.0f}".replace(",", ".")
    except (ValueError, TypeError):
        return "R$ ?"


# ---------------------------------------------------------------------------
# Normalização e deduplicação
# ---------------------------------------------------------------------------

def _normalize_current(records: List[Dict]) -> List[Dict]:
    """Converte do formato interno do bot para o formato DB (mesmas chaves Supabase)."""
    out = []
    for r in records:
        out.append({
            "plataforma":    r.get("Plataforma")       or r.get("plataforma"),
            "produto":       r.get("Produto / SKU")    or r.get("produto"),
            "marca":         r.get("Marca Monitorada") or r.get("marca"),
            "preco":         r.get("Preço (R$)")       or r.get("preco"),
            "posicao_geral": r.get("Posição Geral")    or r.get("posicao_geral"),
            "keyword":       r.get("Keyword Buscada")  or r.get("keyword"),
            "categoria":     r.get("Categoria Keyword") or r.get("categoria"),
        })
    return out


def _dedup_best_position(records: List[Dict]) -> Dict[Tuple[str, str], Dict]:
    """
    Agrupa por (plataforma, produto) mantendo a linha com menor posição_geral
    (mais visível). Usado para comparar preços sem duplicar quando o mesmo
    produto aparece em várias keywords.
    """
    out: Dict[Tuple[str, str], Dict] = {}
    for rec in records:
        plat = rec.get("plataforma") or ""
        prod = rec.get("produto") or ""
        if not plat or not prod:
            continue
        key = (plat, prod)
        existing = out.get(key)

        pos_new = rec.get("posicao_geral")
        pos_old = existing.get("posicao_geral") if existing else None

        # Mantém se não existe, ou se a nova posição é melhor (menor)
        if (
            not existing
            or (pos_new is not None and (pos_old is None or pos_new < pos_old))
        ):
            out[key] = rec
    return out


def _top_by_keyword(records: List[Dict]) -> Dict[Tuple[str, str], Dict]:
    """
    Retorna o produto na posição 1 para cada (plataforma, keyword).
    Base para a análise de buybox.
    """
    out: Dict[Tuple[str, str], Dict] = {}
    for rec in records:
        plat = rec.get("plataforma") or ""
        kw   = rec.get("keyword") or ""
        pos  = rec.get("posicao_geral")
        if not plat or not kw or pos is None:
            continue
        try:
            pos_int = int(pos)
        except (ValueError, TypeError):
            continue
        if pos_int != 1:
            continue
        # Mantém o primeiro encontrado (caso haja duplicata, fica com o primeiro)
        key = (plat, kw)
        if key not in out:
            out[key] = rec
    return out


# ---------------------------------------------------------------------------
# Supabase — busca coleta anterior
# ---------------------------------------------------------------------------

def _get_previous_collection(turno: str, data_str: str) -> List[Dict[str, Any]]:
    """Busca os registros do dia anterior (mesmo turno) para comparação."""
    try:
        from utils.supabase_client import _get_client
        client = _get_client()
        if not client:
            return []

        resp = (
            client.table("coletas")
            .select("data")
            .eq("turno", turno)
            .lt("data", data_str)
            .order("data", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return []

        prev_date = rows[0]["data"]

        resp = (
            client.table("coletas")
            .select("plataforma,marca,produto,preco,posicao_geral,keyword,categoria")
            .eq("turno", turno)
            .eq("data", prev_date)
            .limit(5000)
            .execute()
        )
        return resp.data or []

    except Exception as exc:
        logger.warning(f"[N8N] Erro ao buscar coleta anterior: {exc}")
        return []


# ---------------------------------------------------------------------------
# Análise de mudanças
# ---------------------------------------------------------------------------

def _compute_changes(
    current_raw: List[Dict],
    previous: List[Dict],
) -> Dict[str, Any]:
    """
    Análise executiva comparando coleta atual vs anterior.

    Retorna:
      price_changes — top movimentos de preço (dedup por produto, com keyword e categoria)
      buybox_gains  — Midea assumiu posição 1 (quem deslocou)
      buybox_losses — Midea perdeu posição 1 (quem assumiu)
      summary       — indicadores agregados
    """
    current = _normalize_current(current_raw)

    curr_by_product = _dedup_best_position(current)
    prev_by_product = _dedup_best_position(previous)

    curr_top_kw = _top_by_keyword(current)
    prev_top_kw = _top_by_keyword(previous)

    # ── Variações de preço ──────────────────────────────────────────────────
    price_changes: List[Dict] = []
    for key, rec in curr_by_product.items():
        prev = prev_by_product.get(key)
        if not prev:
            continue

        p_cur_raw  = rec.get("preco")
        p_prev_raw = prev.get("preco")
        if not p_cur_raw or not p_prev_raw:
            continue

        try:
            p_cur  = float(p_cur_raw)
            p_prev = float(p_prev_raw)
        except (ValueError, TypeError):
            continue

        if p_prev <= 0:
            continue

        delta = p_cur - p_prev
        pct   = delta / p_prev * 100

        if abs(delta) < _PRICE_MIN_DELTA_REAIS or abs(pct) < _PRICE_MIN_DELTA_PCT:
            continue
        if abs(pct) >= _PRICE_IGNORE_PCT:
            continue  # certamente erro de parser ou SKU trocado

        price_changes.append({
            "plataforma":  rec.get("plataforma"),
            "produto":     (rec.get("produto") or "")[:75],
            "marca":       rec.get("marca") or "",
            "categoria":   rec.get("categoria") or "",
            "keyword":     rec.get("keyword") or "",
            "preco_antes": round(p_prev, 2),
            "preco_atual": round(p_cur, 2),
            "delta":       round(delta, 2),
            "pct":         round(pct, 1),
            "suspeito":    abs(pct) >= _PRICE_SUSPICIOUS_PCT,
        })

    # ── Buybox Midea — análise por keyword ─────────────────────────────────
    buybox_gains:  List[Dict] = []
    buybox_losses: List[Dict] = []

    all_keys = set(curr_top_kw.keys()) | set(prev_top_kw.keys())
    for key in all_keys:
        curr_first = curr_top_kw.get(key)
        prev_first = prev_top_kw.get(key)
        if not curr_first or not prev_first:
            continue

        curr_marca = (curr_first.get("marca") or "").lower()
        prev_marca = (prev_first.get("marca") or "").lower()
        curr_is_midea = "midea" in curr_marca
        prev_is_midea = "midea" in prev_marca

        # Só importa quando há troca envolvendo Midea
        if curr_is_midea and not prev_is_midea:
            buybox_gains.append({
                "plataforma":        key[0],
                "keyword":           key[1],
                "produto_midea":     (curr_first.get("produto") or "")[:65],
                "deslocou_marca":    prev_first.get("marca") or "?",
                "deslocou_produto":  (prev_first.get("produto") or "")[:50],
            })
        elif prev_is_midea and not curr_is_midea:
            buybox_losses.append({
                "plataforma":        key[0],
                "keyword":           key[1],
                "produto_midea":     (prev_first.get("produto") or "")[:65],
                "assumiu_marca":     curr_first.get("marca") or "?",
                "assumiu_produto":   (curr_first.get("produto") or "")[:50],
            })

    # ── Indicadores agregados ───────────────────────────────────────────────
    midea_precos_atuais = [
        float(r["preco"]) for r in curr_by_product.values()
        if r.get("preco") and "midea" in (r.get("marca") or "").lower()
    ]
    midea_precos_antes = [
        float(r["preco"]) for r in prev_by_product.values()
        if r.get("preco") and "midea" in (r.get("marca") or "").lower()
    ]
    avg_midea_atual = sum(midea_precos_atuais) / len(midea_precos_atuais) if midea_precos_atuais else 0
    avg_midea_antes = sum(midea_precos_antes) / len(midea_precos_antes) if midea_precos_antes else 0
    avg_delta_pct = (
        (avg_midea_atual - avg_midea_antes) / avg_midea_antes * 100
        if avg_midea_antes > 0 else 0
    )

    # Ordena variações por magnitude
    price_changes.sort(key=lambda x: abs(x["pct"]), reverse=True)

    return {
        "price_changes":   price_changes,
        "buybox_gains":    buybox_gains,
        "buybox_losses":   buybox_losses,
        "summary": {
            "avg_midea_atual":  round(avg_midea_atual, 2),
            "avg_midea_antes":  round(avg_midea_antes, 2),
            "avg_delta_pct":    round(avg_delta_pct, 1),
            "total_movimentos": len(price_changes),
            "buybox_gains":     len(buybox_gains),
            "buybox_losses":    len(buybox_losses),
            "midea_skus":       len(midea_precos_atuais),
            "has_previous":     bool(previous),
        },
    }


# ---------------------------------------------------------------------------
# Formatação das mensagens (HTML do Telegram)
# ---------------------------------------------------------------------------

def _fmt_start(platforms: List[str], turno: str, data_str: str) -> str:
    plat_str = ", ".join(p.upper() for p in platforms)
    hora = datetime.now().strftime("%H:%M")
    return (
        f"🚀 <b>Coleta iniciada</b>\n"
        f"📅 {_esc(data_str)} — {_esc(turno)} ({hora})\n"
        f"🌐 Plataformas: {_esc(plat_str)} ({len(platforms)})"
    )


def _fmt_end(
    platforms: List[str],
    turno: str,
    data_str: str,
    total: int,
    per_platform: Dict[str, int],
    duration_min: float,
    changes: Dict[str, Any],
) -> str:
    summary = changes.get("summary", {})
    price_ch = changes.get("price_changes", [])
    gains    = changes.get("buybox_gains", [])
    losses   = changes.get("buybox_losses", [])

    divider = "━━━━━━━━━━━━━━━━━━"
    lines: List[str] = []

    # Cabeçalho
    lines.append(f"✅ <b>COLETA — {_esc(turno)} {_esc(data_str)}</b>")
    lines.append(
        f"⏱ {int(duration_min)} min  •  📊 <b>{total:,}</b>"
        .replace(",", ".") + f" registros"
    )
    for plat, count in sorted(per_platform.items(), key=lambda x: -x[1]):
        lines.append(f"   └ {_esc(plat)}: {count:,}".replace(",", "."))

    # Indicadores
    if summary.get("has_previous"):
        lines.append("")
        lines.append(divider)
        lines.append("📈 <b>INDICADORES</b>")
        if summary["midea_skus"] > 0:
            arrow = "▲" if summary["avg_delta_pct"] > 0 else "▼" if summary["avg_delta_pct"] < 0 else "—"
            lines.append(
                f"• Preço médio Midea: {_fmt_brl(summary['avg_midea_atual'])} "
                f"({arrow} {abs(summary['avg_delta_pct'])}%)"
            )
        lines.append(f"• Movimentos de preço: {summary['total_movimentos']}")
        lines.append(
            f"• Buybox Midea: <b>+{summary['buybox_gains']}</b> ganhos"
            f"  •  <b>-{summary['buybox_losses']}</b> perdas"
        )

    # Preços — quedas
    quedas = [c for c in price_ch if c["delta"] < 0][:5]
    if quedas:
        lines.append("")
        lines.append(divider)
        lines.append("🔻 <b>MAIORES QUEDAS</b>")
        for i, ch in enumerate(quedas, 1):
            flag = " ⚠️" if ch["suspeito"] else ""
            lines.append(
                f"{i}. <b>{_esc(ch['marca'])}</b> — {_esc(ch['produto'])}{flag}"
            )
            lines.append(
                f"   {_esc(ch['plataforma'])} • {_esc(ch['categoria'] or 'geral')}"
            )
            lines.append(
                f"   {_fmt_brl(ch['preco_antes'])} → <b>{_fmt_brl(ch['preco_atual'])}</b> "
                f"({ch['pct']:+.1f}%)"
            )
            lines.append(f"   🔎 <i>{_esc(ch['keyword'])}</i>")

    # Preços — altas
    altas = [c for c in price_ch if c["delta"] > 0][:5]
    if altas:
        lines.append("")
        lines.append(divider)
        lines.append("🔺 <b>MAIORES ALTAS</b>")
        for i, ch in enumerate(altas, 1):
            flag = " ⚠️" if ch["suspeito"] else ""
            lines.append(
                f"{i}. <b>{_esc(ch['marca'])}</b> — {_esc(ch['produto'])}{flag}"
            )
            lines.append(
                f"   {_esc(ch['plataforma'])} • {_esc(ch['categoria'] or 'geral')}"
            )
            lines.append(
                f"   {_fmt_brl(ch['preco_antes'])} → <b>{_fmt_brl(ch['preco_atual'])}</b> "
                f"({ch['pct']:+.1f}%)"
            )
            lines.append(f"   🔎 <i>{_esc(ch['keyword'])}</i>")

    # Buybox — ganhos
    if gains:
        lines.append("")
        lines.append(divider)
        lines.append(f"🏆 <b>BUYBOX — GANHOS MIDEA ({len(gains)})</b>")
        for i, g in enumerate(gains[:8], 1):
            lines.append(f"{i}. <b>{_esc(g['plataforma'])}</b> • <i>{_esc(g['keyword'])}</i>")
            lines.append(f"   ✅ {_esc(g['produto_midea'])}")
            lines.append(f"   📤 Deslocou: {_esc(g['deslocou_marca'])} ({_esc(g['deslocou_produto'])})")

    # Buybox — perdas
    if losses:
        lines.append("")
        lines.append(divider)
        lines.append(f"⚠️ <b>BUYBOX — PERDAS MIDEA ({len(losses)})</b>")
        for i, l in enumerate(losses[:8], 1):
            lines.append(f"{i}. <b>{_esc(l['plataforma'])}</b> • <i>{_esc(l['keyword'])}</i>")
            lines.append(f"   ❌ {_esc(l['produto_midea'])}")
            lines.append(f"   📥 Assumiu: <b>{_esc(l['assumiu_marca'])}</b> ({_esc(l['assumiu_produto'])})")

    if not summary.get("has_previous"):
        lines.append("")
        lines.append(divider)
        lines.append("ℹ️ <i>Primeira coleta deste turno — sem dados anteriores para comparação.</i>")
    elif not price_ch and not gains and not losses:
        lines.append("")
        lines.append(divider)
        lines.append("✨ <i>Sem movimentações relevantes desde a última coleta.</i>")

    msg = "\n".join(lines)

    # Telegram tem limite de 4096 chars
    if len(msg) > 4000:
        msg = msg[:3950] + "\n\n<i>... mensagem truncada.</i>"

    return msg


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def notify_start(platforms: List[str], turno: str, data_str: str) -> None:
    """Notifica início da coleta."""
    if not _webhook_url():
        return
    _send({
        "event":     "start",
        "chat_id":   _chat_id(),
        "message":   _fmt_start(platforms, turno, data_str),
        "turno":     turno,
        "data":      data_str,
        "platforms": platforms,
    })
    logger.debug("[N8N] Notificação de início enviada.")


def notify_end(
    all_records: List[Dict[str, Any]],
    platforms: List[str],
    turno: str,
    data_str: str,
    start_time: float,
) -> None:
    """Notifica fim da coleta com resumo executivo."""
    if not _webhook_url():
        return

    duration_min = (time.time() - start_time) / 60

    per_platform: Dict[str, int] = defaultdict(int)
    for r in all_records:
        plat = r.get("Plataforma") or r.get("plataforma") or "?"
        per_platform[plat] += 1

    previous = _get_previous_collection(turno, data_str)
    changes  = _compute_changes(all_records, previous)

    msg = _fmt_end(
        platforms=platforms,
        turno=turno,
        data_str=data_str,
        total=len(all_records),
        per_platform=dict(per_platform),
        duration_min=duration_min,
        changes=changes,
    )

    _send({
        "event":          "end",
        "chat_id":        _chat_id(),
        "message":        msg,
        "turno":          turno,
        "data":           data_str,
        "platforms":      platforms,
        "total":          len(all_records),
        "per_platform":   dict(per_platform),
        "duration_min":   round(duration_min, 1),
        "summary":        changes.get("summary", {}),
        "price_changes":  changes.get("price_changes", [])[:10],
        "buybox_gains":   changes.get("buybox_gains", []),
        "buybox_losses":  changes.get("buybox_losses", []),
    })
    logger.debug("[N8N] Notificação de fim enviada.")
