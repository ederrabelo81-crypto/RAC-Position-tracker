"""
utils/n8n_notify.py — Notificações de coleta para N8N via webhook.

Envia eventos de início e fim de coleta para o N8N, que repassa
ao Telegram (ou outro canal configurado no workflow).

Configure no .env:
    N8N_WEBHOOK_URL=http://localhost:5678/webhook/coleta
    N8N_TELEGRAM_CHAT_ID=123456789   # ou @seucanal

Se N8N_WEBHOOK_URL não estiver configurado, as notificações são ignoradas silenciosamente.
A coleta principal nunca é bloqueada por falhas neste módulo.
"""

import os
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from loguru import logger

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _webhook_url() -> Optional[str]:
    return os.getenv("N8N_WEBHOOK_URL", "").strip() or None


def _chat_id() -> str:
    return os.getenv("N8N_TELEGRAM_CHAT_ID", "").strip()


def _send(payload: Dict[str, Any]) -> bool:
    """POST para o webhook N8N. Silencia exceções — nunca quebra a coleta."""
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


# ---------------------------------------------------------------------------
# Comparação com coleta anterior
# ---------------------------------------------------------------------------

def _get_previous_collection(turno: str, data_str: str) -> List[Dict[str, Any]]:
    """Busca os registros do turno anterior ao data_str no Supabase."""
    try:
        from utils.supabase_client import _get_client
        client = _get_client()
        if not client:
            return []

        # Descobre qual foi a data mais recente para este turno antes de hoje
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

        # Busca todos os registros daquele dia/turno
        resp = (
            client.table("coletas")
            .select("plataforma,marca,produto,preco,posicao_geral")
            .eq("turno", turno)
            .eq("data", prev_date)
            .limit(3000)
            .execute()
        )
        return resp.data or []

    except Exception as exc:
        logger.warning(f"[N8N] Erro ao buscar coleta anterior: {exc}")
        return []


def _normalize_current(records: List[Dict]) -> List[Dict]:
    """Converte registros do formato interno para o formato DB (mesmas chaves do Supabase)."""
    out = []
    for r in records:
        out.append({
            "plataforma":   r.get("Plataforma")      or r.get("plataforma"),
            "produto":      r.get("Produto / SKU")   or r.get("produto"),
            "marca":        r.get("Marca Monitorada") or r.get("marca"),
            "preco":        r.get("Preço (R$)")       or r.get("preco"),
            "posicao_geral": r.get("Posição Geral")  or r.get("posicao_geral"),
        })
    return out


def _compute_changes(
    current_raw: List[Dict],
    previous: List[Dict],
) -> Dict[str, Any]:
    """
    Compara coleta atual com a anterior.
    Retorna as top variações de preço e mudanças de buybox da Midea.
    """
    current = _normalize_current(current_raw)

    # Índice por (plataforma, produto) — registra só o primeiro (melhor posição)
    prev_idx: Dict[tuple, Dict] = {}
    for rec in previous:
        key = (rec.get("plataforma") or "", rec.get("produto") or "")
        if key not in prev_idx:
            prev_idx[key] = rec

    price_changes: List[Dict] = []
    buybox_changes: List[Dict] = []

    for rec in current:
        plat    = rec.get("plataforma") or ""
        produto = rec.get("produto") or ""
        marca   = rec.get("marca") or ""

        prev = prev_idx.get((plat, produto))
        if not prev:
            continue

        preco_atual = rec.get("preco")
        preco_antes = prev.get("preco")
        pos_atual   = rec.get("posicao_geral")
        pos_antes   = prev.get("posicao_geral")

        # ── Variação de preço ──────────────────────────────────────────────
        if preco_atual and preco_antes:
            try:
                p_cur  = float(preco_atual)
                p_prev = float(preco_antes)
                if p_prev > 0:
                    delta = p_cur - p_prev
                    pct   = delta / p_prev * 100
                    if abs(delta) >= 20 and abs(pct) >= 1.5:
                        price_changes.append({
                            "plataforma":   plat,
                            "produto":      produto[:65],
                            "marca":        marca,
                            "preco_antes":  round(p_prev, 2),
                            "preco_atual":  round(p_cur, 2),
                            "delta":        round(delta, 2),
                            "pct":          round(pct, 1),
                        })
            except (ValueError, TypeError):
                pass

        # ── Mudança de buybox — somente Midea ─────────────────────────────
        if marca and "midea" in marca.lower() and pos_atual is not None and pos_antes is not None:
            try:
                p_cur  = int(pos_atual)
                p_prev = int(pos_antes)
                if p_prev != p_cur and (p_prev == 1 or p_cur == 1):
                    buybox_changes.append({
                        "plataforma": plat,
                        "produto":    produto[:65],
                        "pos_antes":  p_prev,
                        "pos_atual":  p_cur,
                        "perdeu":     p_prev == 1 and p_cur != 1,
                    })
            except (ValueError, TypeError):
                pass

    # Top 10 por maior variação percentual absoluta
    price_changes.sort(key=lambda x: abs(x["pct"]), reverse=True)
    return {
        "price_changes":  price_changes[:10],
        "buybox_changes": buybox_changes[:10],
    }


# ---------------------------------------------------------------------------
# Formatação das mensagens Telegram
# ---------------------------------------------------------------------------

def _fmt_start(platforms: List[str], turno: str, data_str: str) -> str:
    plat_str = ", ".join(p.upper() for p in platforms)
    hora = datetime.now().strftime("%H:%M")
    return (
        f"🚀 *Coleta iniciada*\n"
        f"📅 {data_str} — {turno} ({hora})\n"
        f"🌐 Plataformas: {plat_str} ({len(platforms)})"
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
    lines = [
        f"✅ *Coleta concluída — {turno} {data_str}*",
        f"⏱ Duração: {int(duration_min)} min",
        "",
        "📊 *Amostras coletadas:*",
    ]

    for plat, count in sorted(per_platform.items(), key=lambda x: -x[1]):
        lines.append(f"  • {plat}: {count}")
    lines.append(f"  *Total: {total} registros*")

    # ── Preços ──────────────────────────────────────────────────────────────
    price_ch = changes.get("price_changes", [])
    if price_ch:
        lines += ["", "💰 *Movimentações de preço:*"]
        quedas   = [c for c in price_ch if c["delta"] < 0][:3]
        altas    = [c for c in price_ch if c["delta"] > 0][:3]
        for ch in quedas:
            lines.append(
                f"  ↓ {ch['produto']} — {ch['plataforma']}"
                f"  R\\${ch['preco_antes']:.0f}→R\\${ch['preco_atual']:.0f}"
                f" ({ch['pct']:+.1f}%)"
            )
        for ch in altas:
            lines.append(
                f"  ↑ {ch['produto']} — {ch['plataforma']}"
                f"  R\\${ch['preco_antes']:.0f}→R\\${ch['preco_atual']:.0f}"
                f" ({ch['pct']:+.1f}%)"
            )
    else:
        lines += ["", "💰 Preços: sem variações significativas"]

    # ── Buybox Midea ─────────────────────────────────────────────────────────
    buybox_ch = changes.get("buybox_changes", [])
    if buybox_ch:
        lines += ["", "📦 *Buybox Midea:*"]
        for ch in buybox_ch:
            icon = "❌ Perdeu" if ch["perdeu"] else "✅ Ganhou"
            lines.append(
                f"  {icon} pos\\.1 — {ch['produto']} ({ch['plataforma']})"
                f"  pos\\. {ch['pos_antes']}→{ch['pos_atual']}"
            )
    else:
        lines += ["", "📦 Buybox Midea: sem mudanças de posição 1"]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def notify_start(platforms: List[str], turno: str, data_str: str) -> None:
    """Chama no início da coleta, antes do loop de scrapers."""
    if not _webhook_url():
        return
    _send({
        "event":    "start",
        "chat_id":  _chat_id(),
        "message":  _fmt_start(platforms, turno, data_str),
        "turno":    turno,
        "data":     data_str,
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
    """Chama após upload Supabase. Computa diferenças e envia resumo completo."""
    if not _webhook_url():
        return

    duration_min = (time.time() - start_time) / 60

    per_platform: Dict[str, int] = defaultdict(int)
    for r in all_records:
        plat = r.get("Plataforma") or r.get("plataforma") or "?"
        per_platform[plat] += 1

    # Busca coleta anterior para comparação (só se Supabase disponível)
    previous = _get_previous_collection(turno, data_str)
    changes  = _compute_changes(all_records, previous) if previous else {
        "price_changes": [], "buybox_changes": []
    }

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
        "price_changes":  changes["price_changes"],
        "buybox_changes": changes["buybox_changes"],
    })
    logger.debug("[N8N] Notificação de fim enviada.")
