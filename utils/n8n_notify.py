"""
utils/n8n_notify.py — Notificações executivas de coleta para N8N via webhook.

Gera um resumo executivo com:
  • Indicadores da coleta (volume, duração)
  • Matriz de preço médio Midea (Linha × Capacidade) — High Wall Inverter
  • Ranking top 5 por keyword estratégica (9k/12k High Wall Inverter)
  • Top 5 quedas e altas de preço (deduplicado por produto, com keyword)
  • Ganhos/perdas de buybox Midea com contexto de concorrência
  • Filtro de variações suspeitas (parser errors)

Configure no .env:
    N8N_WEBHOOK_URL=http://localhost:5678/webhook/coleta   (opcional)
    N8N_TELEGRAM_CHAT_ID=123456789
    TELEGRAM_BOT_TOKEN=7730291785:AAF...                   (fallback direto)

Se N8N_WEBHOOK_URL não estiver configurado ou o webhook falhar, envia
diretamente pela API do Telegram usando TELEGRAM_BOT_TOKEN.
"""

import html
import os
import re
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

# Matriz Midea — High Wall Inverter
_MIDEA_LINES = ["AI Ecomaster", "AI Airvolution", "Lite"]
_MIDEA_BTUS  = [9000, 12000, 18000]

# Keywords estratégicas para o ranking top 5
_RANKING_KEYWORDS = [
    "ar condicionado",
    "ar condicionado inverter",
    "ar condicionado split inverter",
]
_RANKING_BTUS = [9000, 12000]

# Padrões para rejeitar produtos que NÃO são High Wall (para matriz/ranking)
_NON_HIGHWALL = [
    "portátil", "portatil", "portable",
    "cassete", "cassette", "casset",
    "piso teto", "piso-teto", "piso/teto",
    "bi split", "bi-split", "bisplit",
    "multi split", "multi-split", "multisplit",
    "janela", "window",
    "vrf", "vrv",
    "duto",
]

# Regex de BTU (mesma lógica do supabase_client)
_BTU_RE = re.compile(
    r'(\d{1,2})[.,](\d{3})\s*btu|(\d{4,6})\s*btu',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers de webhook
# ---------------------------------------------------------------------------

def _webhook_url() -> Optional[str]:
    return os.getenv("N8N_WEBHOOK_URL", "").strip() or None


def _chat_id() -> str:
    return os.getenv("N8N_TELEGRAM_CHAT_ID", "").strip()


def _bot_token() -> Optional[str]:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None


def _send_direct_telegram(message: str) -> bool:
    """Envia diretamente pela API do Telegram, sem passar pelo N8N."""
    token = _bot_token()
    chat_id = _chat_id()
    if not token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=15)
        resp.raise_for_status()
        logger.info("[Telegram] Mensagem enviada diretamente via Bot API")
        return True
    except Exception as exc:
        logger.warning(f"[Telegram] Falha no envio direto: {exc}")
        return False


def _send(payload: Dict[str, Any]) -> bool:
    """Tenta N8N webhook; se falhar, envia direto pela API do Telegram."""
    url = _webhook_url()
    if url:
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            logger.info("[N8N] Notificação enviada via webhook")
            return True
        except Exception as exc:
            logger.warning(f"[N8N] Webhook falhou ({exc}), tentando Telegram direto...")

    # Fallback: envio direto ao Telegram
    return _send_direct_telegram(payload.get("message", ""))


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
# Detecção de linha, capacidade e tipo
# ---------------------------------------------------------------------------

def _detect_midea_line(produto: str) -> Optional[str]:
    """Detecta linha Midea: AI Ecomaster / AI Airvolution / Lite."""
    p = (produto or "").lower()
    if "ecomaster" in p:
        return "AI Ecomaster"
    if "airvolution" in p or "air volution" in p:
        return "AI Airvolution"
    # "Lite" é genérico — exige que seja Midea sem outra linha identificada
    if "lite" in p and "ecomaster" not in p and "airvolution" not in p:
        return "Lite"
    return None


def _detect_btu(produto: str) -> Optional[int]:
    """Extrai BTU do nome do produto. Retorna int ou None."""
    m = _BTU_RE.search(produto or "")
    if not m:
        return None
    if m.group(3):
        return int(m.group(3))
    return int(m.group(1)) * 1000 + int(m.group(2))


def _is_highwall(produto: str) -> bool:
    """True se o produto parece ser High Wall (split de parede tradicional)."""
    p = (produto or "").lower()
    return not any(excl in p for excl in _NON_HIGHWALL)


# ---------------------------------------------------------------------------
# Matriz de preços Midea (Linha × Capacidade)
# ---------------------------------------------------------------------------

def _compute_midea_matrix(
    curr_by_product: Dict[Tuple[str, str], Dict],
    prev_by_product: Dict[Tuple[str, str], Dict],
) -> Dict[Tuple[str, int], Dict]:
    """
    Agrupa produtos Midea por (Linha, BTU) e calcula preço médio atual e anterior.
    Só considera High Wall Inverter.
    """
    def _bucket(records: Dict[Tuple[str, str], Dict]) -> Dict[Tuple[str, int], List[float]]:
        acc: Dict[Tuple[str, int], List[float]] = defaultdict(list)
        for rec in records.values():
            marca = (rec.get("marca") or "").lower()
            if "midea" not in marca:
                continue
            produto = rec.get("produto") or ""
            if not _is_highwall(produto):
                continue
            linha = _detect_midea_line(produto)
            btu   = _detect_btu(produto)
            if not linha or btu not in _MIDEA_BTUS:
                continue
            if linha not in _MIDEA_LINES:
                continue
            try:
                preco = float(rec.get("preco"))
                if preco > 0:
                    acc[(linha, btu)].append(preco)
            except (ValueError, TypeError):
                pass
        return acc

    curr_buckets = _bucket(curr_by_product)
    prev_buckets = _bucket(prev_by_product)

    matrix: Dict[Tuple[str, int], Dict] = {}
    for linha in _MIDEA_LINES:
        for btu in _MIDEA_BTUS:
            key = (linha, btu)
            curr_prices = curr_buckets.get(key, [])
            prev_prices = prev_buckets.get(key, [])
            avg_curr = sum(curr_prices) / len(curr_prices) if curr_prices else None
            avg_prev = sum(prev_prices) / len(prev_prices) if prev_prices else None
            delta_pct = None
            if avg_curr and avg_prev and avg_prev > 0:
                delta_pct = (avg_curr - avg_prev) / avg_prev * 100
            matrix[key] = {
                "avg_curr":  round(avg_curr, 2) if avg_curr else None,
                "avg_prev":  round(avg_prev, 2) if avg_prev else None,
                "delta_pct": round(delta_pct, 1) if delta_pct is not None else None,
                "n_curr":    len(curr_prices),
            }
    return matrix


# ---------------------------------------------------------------------------
# Ranking top 5 por keyword (High Wall Inverter 9k/12k)
# ---------------------------------------------------------------------------

def _match_target_keyword(keyword_value: str) -> Optional[str]:
    """
    Retorna a target keyword que melhor bate com o valor coletado,
    ou None se não bater com nenhuma. Prioriza match mais específico.
    """
    kw = (keyword_value or "").lower().strip()
    if not kw:
        return None
    # Ordena do mais específico pro mais genérico para match em ordem
    for target in sorted(_RANKING_KEYWORDS, key=len, reverse=True):
        if kw == target or kw.startswith(target + " ") or kw == target:
            return target
    # Match exato simples
    if kw in _RANKING_KEYWORDS:
        return kw
    return None


def _compute_keyword_ranking(current_records: List[Dict]) -> Dict[Tuple[str, str], List[Dict]]:
    """
    Para cada (plataforma, target_keyword), retorna até 5 produtos ordenados
    por posição geral. Filtra apenas High Wall 9k/12k.
    """
    # Agrupa por (plataforma, target_keyword) mantendo menor posição por produto
    grouped: Dict[Tuple[str, str], Dict[str, Dict]] = defaultdict(dict)

    for rec in current_records:
        plat    = rec.get("plataforma") or ""
        kw_raw  = rec.get("keyword") or ""
        produto = rec.get("produto") or ""
        pos_raw = rec.get("posicao_geral")

        if not plat or not produto or pos_raw is None:
            continue

        target_kw = _match_target_keyword(kw_raw)
        if not target_kw:
            continue

        btu = _detect_btu(produto)
        if btu not in _RANKING_BTUS:
            continue
        if not _is_highwall(produto):
            continue

        try:
            pos = int(pos_raw)
        except (ValueError, TypeError):
            continue

        group_key = (plat, target_kw)
        existing = grouped[group_key].get(produto)
        if not existing or pos < (existing.get("posicao_geral") or 999):
            grouped[group_key][produto] = rec

    # Ordena cada grupo por posição e corta top 5
    result: Dict[Tuple[str, str], List[Dict]] = {}
    for key, prod_dict in grouped.items():
        items = sorted(
            prod_dict.values(),
            key=lambda r: int(r.get("posicao_geral") or 999),
        )
        result[key] = items[:5]
    return result


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

    # ── Matriz Midea (Linha × Capacidade) ──────────────────────────────────
    midea_matrix = _compute_midea_matrix(curr_by_product, prev_by_product)

    # ── Ranking top 5 por keyword estratégica ──────────────────────────────
    keyword_ranking = _compute_keyword_ranking(current)

    # ── Indicadores agregados ───────────────────────────────────────────────
    total_midea_skus = sum(
        1 for r in curr_by_product.values()
        if r.get("preco") and "midea" in (r.get("marca") or "").lower()
    )

    # Ordena variações por magnitude
    price_changes.sort(key=lambda x: abs(x["pct"]), reverse=True)

    return {
        "price_changes":    price_changes,
        "buybox_gains":     buybox_gains,
        "buybox_losses":    buybox_losses,
        "midea_matrix":     midea_matrix,
        "keyword_ranking":  keyword_ranking,
        "summary": {
            "total_movimentos": len(price_changes),
            "buybox_gains":     len(buybox_gains),
            "buybox_losses":    len(buybox_losses),
            "midea_skus":       total_midea_skus,
            "has_previous":     bool(previous),
        },
    }


# ---------------------------------------------------------------------------
# Formatação das mensagens (HTML do Telegram)
# ---------------------------------------------------------------------------

def _fmt_start(platforms: List[str], turno: str, data_str: str) -> str:
    from utils.text import now_brt
    plat_str = ", ".join(p.upper() for p in platforms)
    hora = now_brt().strftime("%H:%M")
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
    summary         = changes.get("summary", {})
    price_ch        = changes.get("price_changes", [])
    gains           = changes.get("buybox_gains", [])
    losses          = changes.get("buybox_losses", [])
    midea_matrix    = changes.get("midea_matrix", {})
    keyword_ranking = changes.get("keyword_ranking", {})

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

    # Matriz Midea High Wall Inverter (Linha × Capacidade)
    has_matrix_data = any(
        v.get("avg_curr") is not None for v in midea_matrix.values()
    )
    if has_matrix_data:
        lines.append("")
        lines.append(divider)
        lines.append("💰 <b>PREÇO MÉDIO MIDEA — HIGH WALL INVERTER</b>")
        # Tabela em <pre> (fonte monoespaçada)
        table = []
        table.append(f"{'Linha':<15}{'9k':>11}{'12k':>11}{'18k':>11}")
        for linha in _MIDEA_LINES:
            row_cells = [f"{linha:<15}"]
            for btu in _MIDEA_BTUS:
                cell = midea_matrix.get((linha, btu), {})
                avg = cell.get("avg_curr")
                delta = cell.get("delta_pct")
                if avg is None:
                    row_cells.append(f"{'—':>11}")
                else:
                    val = f"{int(avg):,}".replace(",", ".")
                    if delta is not None and abs(delta) >= 1:
                        arrow = "▲" if delta > 0 else "▼"
                        row_cells.append(f"{val}{arrow}".rjust(11))
                    else:
                        row_cells.append(val.rjust(11))
            table.append("".join(row_cells))
        lines.append(f"<pre>{_esc(chr(10).join(table))}</pre>")

    # Indicadores resumo
    if summary.get("has_previous"):
        lines.append("")
        lines.append(divider)
        lines.append("📈 <b>INDICADORES</b>")
        lines.append(f"• Movimentos de preço: {summary['total_movimentos']}")
        lines.append(
            f"• Buybox Midea: <b>+{summary['buybox_gains']}</b> ganhos"
            f"  •  <b>-{summary['buybox_losses']}</b> perdas"
        )
        lines.append(f"• SKUs Midea coletados: {summary['midea_skus']}")

    # Ranking Top 5 por keyword estratégica (High Wall Inverter 9k/12k)
    if keyword_ranking:
        # Ordena: por keyword (mais específica primeiro) e depois plataforma
        sorted_keys = sorted(
            keyword_ranking.keys(),
            key=lambda k: (-len(k[1]), k[0], k[1]),
        )
        lines.append("")
        lines.append(divider)
        lines.append("📊 <b>RANKING TOP 5 — HIGH WALL INVERTER 9k/12k</b>")
        for (plat, kw) in sorted_keys[:6]:  # máx 6 combinações para caber
            products = keyword_ranking.get((plat, kw), [])
            if not products:
                continue
            lines.append("")
            lines.append(f"🔎 <b>{_esc(plat)}</b> — <i>\"{_esc(kw)}\"</i>")
            for prod in products:
                pos = prod.get("posicao_geral") or "?"
                marca = (prod.get("marca") or "?")[:10]
                produto_nome = (prod.get("produto") or "")[:40]
                preco = prod.get("preco")
                try:
                    preco_str = _fmt_brl(float(preco)) if preco else "—"
                except (ValueError, TypeError):
                    preco_str = "—"
                is_midea = "midea" in (prod.get("marca") or "").lower()
                icon = "🟢" if is_midea else "⚪"
                lines.append(
                    f"  {icon} #{pos} <b>{_esc(marca)}</b> "
                    f"{_esc(produto_nome)} — {preco_str}"
                )

    # Preços — quedas
    quedas = [c for c in price_ch if c["delta"] < 0][:3]
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
    altas = [c for c in price_ch if c["delta"] > 0][:3]
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
