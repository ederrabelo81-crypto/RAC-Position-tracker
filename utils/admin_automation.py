"""
utils/admin_automation.py — Motor de automação da área ADMIN do dashboard.

Executa, sem nenhuma interação humana, todas as rotinas que antes dependiam
de cliques nas páginas "🧹 Data Cleanup", "🔤 Normalize SKUs" e
"🧬 Família & SKU" do Streamlit:

    1. data_cleanup          — remove registros não-AC (iPhone, fralda…)
    2. price_validation      — remove preços suspeitos (bug ×10 por teto BTU)
    3. normalize_products    — re-normaliza `coletas.produto`
    4. normalize_brands      — consolida variantes de marca (Springer → Midea)
    5. recalc_unknown_brands — re-extrai marca de registros 'Desconhecida'
    6. normalize_platforms   — corrige typos de plataforma/seller
    7. seed_depara           — insere nomes novos no de-para (RPC, REVISAR)
    8. auto_resolve_depara   — resolve a fila REVISAR em 3 camadas:
                               regras → LLM (Claude) → heurística terminal
    9. resolver_pendentes    — RPC resolver_coletas_pendentes (migration 004)
   10. refresh_cache         — RPC refresh_filter_options (materialized view)

Gatilhos: pós-coleta (main.py), cron (scripts/admin_auto.py) e auto-run na
página "🤖 Automação" do dashboard. Varredura é incremental por watermark de
`coletas.id` (full scan via `full_scan=True` / `--full`).

Auditoria: cada execução é gravada em `admin_automation_runs` (migration 006)
e espelhada em logs/admin_automation.jsonl (fallback quando o DB não tem a
tabela). Resumo vai para o Telegram quando há mudanças ou erros.

Env vars (todas opcionais):
    ADMIN_AUTOMATION            — "off" desativa o hook pós-coleta do main.py
    ADMIN_AUTO_LLM              — "off" pula a camada LLM da fila REVISAR
    ADMIN_AUTO_LLM_MODEL        — modelo Anthropic (default: claude-opus-4-8)
    ADMIN_AUTO_LLM_MAX_NAMES    — máx. de nomes por run na camada LLM (400)
    ADMIN_AUTO_RESIDUAL_POLICY  — "terminal" (default) classifica residuais
                                  via heurística; "keep" mantém em REVISAR
    ADMIN_AUTO_RESOLVE_MAX      — máx. de resoluções aplicadas por run (1000)
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from loguru import logger

from utils.supabase_client import _get_client
from utils.text import is_valid_product

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOCAL_LOG = _PROJECT_ROOT / "logs" / "admin_automation.jsonl"
_RUNS_TABLE = "admin_automation_runs"
_PAGE = 1000

# Ordem canônica de execução. Rótulos usados no dashboard e no Telegram.
STEP_LABELS: Dict[str, str] = {
    "data_cleanup":          "🧹 Limpeza de registros não-AC",
    "price_validation":      "💰 Validação de preços (teto BTU)",
    "normalize_products":    "🔤 Normalização de nomes de produto",
    "normalize_brands":      "🏷️ Normalização de marcas",
    "recalc_unknown_brands": "🔄 Recálculo de marcas Desconhecidas",
    "normalize_platforms":   "🏪 Normalização de plataformas/sellers",
    "seed_depara":           "🌱 Seed de nomes novos no de-para",
    "auto_resolve_depara":   "🧬 Auto-resolução da fila REVISAR",
    "resolver_pendentes":    "🔗 Propagação de-para → coletas (RPC)",
    "refresh_cache":         "♻️ Refresh do cache de filtros",
}
STEP_ORDER: List[str] = list(STEP_LABELS.keys())


def _env_flag(name: str, default: bool = True) -> bool:
    """True/False a partir de env var ("off"/"0"/"false" desligam)."""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw not in ("off", "0", "false", "no")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Resultado de cada etapa
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    name: str
    ok: bool = True
    duration_s: float = 0.0
    summary: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": STEP_LABELS.get(self.name, self.name),
            "ok": self.ok,
            "duration_s": round(self.duration_s, 1),
            "summary": self.summary,
            "details": self.details,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Etapas 1-6 — manutenção da tabela coletas (reusa supabase_maintenance)
# ---------------------------------------------------------------------------

def _step_data_cleanup(client, ctx: Dict[str, Any]) -> StepResult:
    from utils.supabase_maintenance import delete_invalid_from_supabase
    r = delete_invalid_from_supabase(dry_run=ctx["dry_run"], since_id=ctx["since_id"])
    return StepResult(
        "data_cleanup",
        ok=r.get("errors", 0) == 0,
        summary=f"{r['scanned']:,} varridos · {r['invalid']:,} inválidos · {r['deleted']:,} deletados",
        details=r,
        error=None if r.get("errors", 0) == 0 else f"{r['errors']} erro(s) ao deletar",
    )


def _step_price_validation(client, ctx: Dict[str, Any]) -> StepResult:
    from utils.supabase_maintenance import scan_fix_bad_prices_in_supabase
    r = scan_fix_bad_prices_in_supabase(dry_run=ctx["dry_run"], since_id=ctx["since_id"])
    r.pop("examples", None)  # exemplos só interessam no fluxo interativo
    return StepResult(
        "price_validation",
        ok=r.get("errors", 0) == 0,
        summary=f"{r['scanned']:,} varridos · {r['suspicious']:,} suspeitos · {r['deleted']:,} deletados",
        details=r,
        error=None if r.get("errors", 0) == 0 else f"{r['errors']} erro(s) ao deletar",
    )


def _step_normalize_products(client, ctx: Dict[str, Any]) -> StepResult:
    from utils.supabase_maintenance import normalize_all_products_in_supabase
    r = normalize_all_products_in_supabase(
        dry_run=ctx["dry_run"], preview_limit=0, since_id=ctx["since_id"]
    )
    r.pop("preview", None)
    return StepResult(
        "normalize_products",
        ok=r.get("errors", 0) == 0,
        summary=(f"{r['scanned']:,} varridos · {r['changed']:,} desatualizados · "
                 f"{r['updated']:,} renomeados · {r.get('deduped', 0):,} duplicatas removidas"),
        details=r,
        error=None if r.get("errors", 0) == 0 else f"{r['errors']} erro(s) ao atualizar",
    )


def _step_normalize_brands(client, ctx: Dict[str, Any]) -> StepResult:
    from utils.supabase_maintenance import normalize_brands_in_supabase
    r = normalize_brands_in_supabase(dry_run=ctx["dry_run"])
    return StepResult(
        "normalize_brands",
        ok=r.get("errors", 0) == 0,
        summary=f"{r['total_updated']:,} registros consolidados",
        details=r,
        error=None if r.get("errors", 0) == 0 else f"{r['errors']} erro(s)",
    )


def _step_recalc_unknown_brands(client, ctx: Dict[str, Any]) -> StepResult:
    from utils.supabase_maintenance import recalculate_unknown_brands_in_supabase
    r = recalculate_unknown_brands_in_supabase(dry_run=ctx["dry_run"], since_id=ctx["since_id"])
    r.pop("preview", None)
    return StepResult(
        "recalc_unknown_brands",
        ok=r.get("errors", 0) == 0,
        summary=f"{r['scanned']:,} 'Desconhecida' varridos · {r['updated']:,} identificados",
        details=r,
        error=None if r.get("errors", 0) == 0 else f"{r['errors']} erro(s)",
    )


def _step_normalize_platforms(client, ctx: Dict[str, Any]) -> StepResult:
    from utils.supabase_maintenance import normalize_platforms_sellers_in_supabase
    r = normalize_platforms_sellers_in_supabase(dry_run=ctx["dry_run"])
    return StepResult(
        "normalize_platforms",
        ok=r.get("errors", 0) == 0,
        summary=f"{r['total_updated']:,} registros corrigidos",
        details=r,
        error=None if r.get("errors", 0) == 0 else f"{r['errors']} erro(s)",
    )


# ---------------------------------------------------------------------------
# Etapa 7 — seed de nomes novos no de-para (RPC migration 006)
# ---------------------------------------------------------------------------

def _step_seed_depara(client, ctx: Dict[str, Any]) -> StepResult:
    if ctx["dry_run"]:
        return StepResult("seed_depara", summary="dry-run — seed pulado", details={})
    resp = client.rpc("seed_depara_nomes_novos").execute()
    data = resp.data if isinstance(resp.data, dict) else (resp.data[0] if resp.data else {})
    novos = int(data.get("novos_coletas", 0) or 0) + int(data.get("novos_rac", 0) or 0)
    return StepResult(
        "seed_depara",
        summary=f"{novos:,} nome(s) novo(s) adicionados ao de-para",
        details=data,
    )


# ---------------------------------------------------------------------------
# Etapa 8 — auto-resolução da fila REVISAR (regras → LLM → heurística)
# ---------------------------------------------------------------------------

# Estados válidos do de-para (espelha _ESTADOS_RESOLVIDOS do app.py)
_ESTADOS = ("MAPEADO", "FORA_ESCOPO", "NAO_AC", "REVISAR")

_LLM_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "itens": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i":      {"type": "integer"},
                    "estado": {"type": "string",
                               "enum": ["MAPEADO", "FORA_ESCOPO", "NAO_AC", "REVISAR"]},
                    "marca":  {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "btu":    {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                    "ciclo":  {"type": "string", "enum": ["F", "QF"]},
                },
                "required": ["i", "estado", "marca", "btu", "ciclo"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["itens"],
    "additionalProperties": False,
}

_LLM_SYSTEM = (
    "Você classifica títulos de anúncios de marketplaces brasileiros para um "
    "monitor de ar-condicionado Split Hi-Wall residencial (9k-30k BTUs).\n"
    "Estados possíveis:\n"
    "- NAO_AC: não é um aparelho de ar-condicionado (peça, acessório, suporte, "
    "controle, climatizador, ventilador, eletrodoméstico, etc.)\n"
    "- FORA_ESCOPO: é ar-condicionado, mas janela/portátil/cassete/piso-teto/"
    "multi-split, OU capacidade fora de 9.000-30.000 BTUs, OU marca fora do "
    "catálogo (catálogo: MIDEA, LG, SAMSUNG, ELECTROLUX, ELGIN, PHILCO, GREE, "
    "TCL, AGRATTO, HISENSE)\n"
    "- MAPEADO: Split Hi-Wall de marca do catálogo com BTU identificável. "
    "Informe marca (UPPER, do catálogo), btu (inteiro, ex: 12000) e ciclo "
    "('QF' se quente/frio, senão 'F')\n"
    "- REVISAR: somente se for impossível decidir.\n"
    "Responda para TODOS os itens, na mesma ordem, usando o índice `i` recebido."
)


def _residual_heuristic(nome: str) -> Tuple[str, str]:
    """
    Política terminal para nomes que nem as regras nem o LLM resolveram.

    - Não passa no filtro AC (`is_valid_product`) → NAO_AC.
    - Parece AC mas sem marca/BTU de catálogo identificáveis → FORA_ESCOPO
      (não-mapeável: família exige marca de catálogo + BTU).
    """
    if not is_valid_product(nome):
        return "NAO_AC", "heurística: não passa no filtro de produto AC"
    return "FORA_ESCOPO", "heurística: AC não-mapeável (sem marca/BTU de catálogo)"


def _validate_llm_item(item: Dict[str, Any],
                       catalog_brands: Set[str],
                       catalog_btus: Set[int]) -> Optional[Tuple[str, Optional[str], Optional[str]]]:
    """
    Valida uma classificação do LLM e devolve (estado, familia, marca_norm).

    Regras de segurança (o LLM propõe, o catálogo dispõe):
      - MAPEADO exige marca ∈ catálogo e btu ∈ capacidades do catálogo;
        caso contrário a proposta é descartada (None → mantém REVISAR).
      - FORA_ESCOPO/NAO_AC aplicam direto (família nula).
      - REVISAR → None (sem mudança).
    """
    estado = item.get("estado")
    if estado not in _ESTADOS or estado == "REVISAR":
        return None
    if estado in ("FORA_ESCOPO", "NAO_AC"):
        marca = (item.get("marca") or "").strip().upper() or None
        return estado, None, marca
    # MAPEADO — exige marca/BTU válidos para montar a família genérica
    marca = (item.get("marca") or "").strip().upper()
    btu = item.get("btu")
    ciclo = item.get("ciclo") if item.get("ciclo") in ("F", "QF") else "F"
    if marca not in catalog_brands or not isinstance(btu, int) or btu not in catalog_btus:
        return None
    return "MAPEADO", f"{marca}-{btu}-{ciclo}", marca


def _llm_available() -> bool:
    """Camada LLM ativa: flag ligada + ANTHROPIC_API_KEY + pacote instalado."""
    if not _env_flag("ADMIN_AUTO_LLM", default=True):
        return False
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def _classify_residuals_llm(
    residuals: List[str],
    catalog_brands: Set[str],
    catalog_btus: Set[int],
) -> Tuple[Dict[str, Tuple[str, Optional[str], Optional[str]]], Optional[str]]:
    """
    Classifica nomes residuais com a API Anthropic (structured outputs).

    Pressupõe `_llm_available()` True. Nomes que o modelo devolve como
    REVISAR (ou com MAPEADO inválido) ficam fora do retorno — permanecem na
    fila e o próximo ciclo re-tenta.

    Returns:
        (resolvidos, erro)
        resolvidos = {nome: (estado, familia, marca_norm)}
        erro = mensagem se a camada foi interrompida no meio (rede/auth/parse)
    """
    import anthropic

    model = os.getenv("ADMIN_AUTO_LLM_MODEL", "").strip() or "claude-opus-4-8"
    max_names = _env_int("ADMIN_AUTO_LLM_MAX_NAMES", 400)
    batch_size = 40

    todo = residuals[:max_names]  # excedente fica REVISAR p/ o próximo run
    resolved: Dict[str, Tuple[str, Optional[str], Optional[str]]] = {}
    llm_client = anthropic.Anthropic()

    for start in range(0, len(todo), batch_size):
        batch = todo[start:start + batch_size]
        payload = "\n".join(f"{i}. {nome[:300]}" for i, nome in enumerate(batch))
        try:
            resp = llm_client.messages.create(
                model=model,
                max_tokens=4000,
                system=_LLM_SYSTEM,
                output_config={"format": {"type": "json_schema", "schema": _LLM_SCHEMA}},
                messages=[{"role": "user", "content": payload}],
            )
            if resp.stop_reason == "refusal":
                raise RuntimeError("classificação recusada pelo modelo")
            text = next(b.text for b in resp.content if b.type == "text")
            itens = json.loads(text).get("itens", [])
        except Exception as exc:  # rede/auth/parse — interrompe a camada LLM
            error = f"LLM falhou no lote {start // batch_size + 1}: {exc}"
            logger.warning(f"[AdminAuto] {error}")
            return resolved, error

        for item in itens:
            idx = item.get("i")
            if not isinstance(idx, int) or not (0 <= idx < len(batch)):
                continue
            validated = _validate_llm_item(item, catalog_brands, catalog_btus)
            if validated:
                resolved[batch[idx]] = validated

    return resolved, None


def _apply_resolution(client, nome: str, estado: str,
                      familia: Optional[str], marca: Optional[str]) -> Dict[str, Any]:
    """Grava via RPC admin_normalizar_nome (propaga p/ coletas e rac_monitoramento)."""
    resp = client.rpc("admin_normalizar_nome", {
        "p_nome":    nome,
        "p_estado":  estado,
        "p_familia": familia,
        "p_sku":     None,   # política conservadora: SKU nunca é cravado aqui
        "p_marca":   marca,
    }).execute()
    return resp.data if isinstance(resp.data, dict) else {}


def _fetch_revisar(client) -> List[Dict[str, Any]]:
    """Carrega a fila REVISAR do de-para (paginado)."""
    rows: List[Dict[str, Any]] = []
    offset = 0
    while True:
        resp = (client.table("produtos_depara_nome")
                .select("nome_coletado,estado,familia,marca_norm")
                .eq("estado", "REVISAR")
                .order("nome_coletado")
                .range(offset, offset + _PAGE - 1)
                .execute())
        batch = resp.data or []
        rows.extend(batch)
        if len(batch) < _PAGE:
            break
        offset += _PAGE
    return rows


def _step_auto_resolve_depara(client, ctx: Dict[str, Any]) -> StepResult:
    from scripts.montar_depara import (
        FORA_TIPO_REGEX,
        NAO_AC_REGEX,
        load_catalog_btus,
        load_catalog_familias,
    )
    from utils.depara_resolver import CATALOG_BRANDS, resolve_depara

    fila = _fetch_revisar(client)
    if not fila:
        return StepResult("auto_resolve_depara", summary="fila REVISAR vazia", details={"fila": 0})

    catalog_familias = load_catalog_familias(client)
    catalog_btus = load_catalog_btus(client)
    max_apply = _env_int("ADMIN_AUTO_RESOLVE_MAX", 1000)

    # ── Camada 1: guardas + matcher forte (mesma lógica do auto_resolver) ──
    propostas: Dict[str, Tuple[str, Optional[str], Optional[str], str]] = {}
    residuais: List[str] = []
    for row in fila:
        nome = row["nome_coletado"]
        if any(p.search(nome) for p in NAO_AC_REGEX):
            propostas[nome] = ("NAO_AC", None, None, "regra: padrão não-AC")
            continue
        if any(p.search(nome) for p in FORA_TIPO_REGEX):
            propostas[nome] = ("FORA_ESCOPO", None, None, "regra: tipo fora do escopo")
            continue
        res = resolve_depara(nome, row.get("marca_norm"), catalog_familias, catalog_btus)
        if res.estado != "REVISAR":
            propostas[nome] = (res.estado, res.familia, res.marca_norm, f"regra: {res.reason}")
        else:
            residuais.append(nome)

    # ── Camadas 2 e 3: residuais que as regras não decidiram ──
    #
    # Com LLM disponível: o modelo classifica (cap por run); o que ele devolve
    # como REVISAR — ou o que excedeu o cap/falhou — permanece na fila e o
    # próximo ciclo re-tenta. Sem LLM: a heurística terminal zera a fila
    # (política "terminal", default) ou mantém REVISAR (política "keep").
    politica = os.getenv("ADMIN_AUTO_RESIDUAL_POLICY", "terminal").strip().lower()
    llm_error: Optional[str] = None
    llm_usado = False
    heuristica_aplicada = 0
    if residuais and not ctx["dry_run"]:
        if _llm_available():
            llm_usado = True
            resolvidos_llm, llm_error = _classify_residuals_llm(
                residuais, CATALOG_BRANDS, catalog_btus
            )
            for nome, (estado, familia, marca) in resolvidos_llm.items():
                propostas[nome] = (estado, familia, marca, "llm")
        elif politica == "terminal":
            for nome in residuais:
                estado, reason = _residual_heuristic(nome)
                propostas[nome] = (estado, None, None, reason)
                heuristica_aplicada += 1

    # ── Aplicação via RPC ──
    por_camada = {"regra": 0, "llm": 0, "heurística": 0}
    aplicadas = erros = coletas_tot = 0
    for nome, (estado, familia, marca, reason) in propostas.items():
        if aplicadas >= max_apply:
            break
        camada = "heurística" if reason.startswith("heurística") else (
            "llm" if reason == "llm" else "regra")
        if ctx["dry_run"]:
            por_camada[camada] += 1
            aplicadas += 1
            continue
        try:
            payload = _apply_resolution(client, nome, estado, familia, marca)
            coletas_tot += int(payload.get("coletas_atualizadas", 0) or 0)
            por_camada[camada] += 1
            aplicadas += 1
        except Exception as exc:
            erros += 1
            logger.warning(f"[AdminAuto] Falha ao resolver '{nome[:60]}': {exc}")

    restantes = len(fila) - aplicadas
    details = {
        "fila": len(fila),
        "aplicadas": aplicadas,
        "por_camada": por_camada,
        "heuristica": heuristica_aplicada,
        "restantes_revisar": max(restantes, 0),
        "coletas_atualizadas": coletas_tot,
        "erros": erros,
        "llm_usado": llm_usado,
        "llm_error": llm_error,
        "politica_residual": politica,
        "dry_run": ctx["dry_run"],
    }
    return StepResult(
        "auto_resolve_depara",
        ok=erros == 0,
        summary=(f"fila {len(fila):,} · resolvidos {aplicadas:,} "
                 f"(regras {por_camada['regra']:,} · llm {por_camada['llm']:,} · "
                 f"heurística {por_camada['heurística']:,}) · "
                 f"restam {max(restantes, 0):,}"),
        details=details,
        error=None if erros == 0 else f"{erros} erro(s) ao aplicar resoluções",
    )


# ---------------------------------------------------------------------------
# Etapas 9-10 — RPCs de propagação e cache
# ---------------------------------------------------------------------------

def _step_resolver_pendentes(client, ctx: Dict[str, Any]) -> StepResult:
    if ctx["dry_run"]:
        return StepResult("resolver_pendentes", summary="dry-run — RPC pulada", details={})
    resp = client.rpc("resolver_coletas_pendentes").execute()
    data = resp.data
    row = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
    return StepResult(
        "resolver_pendentes",
        summary=(f"resolvidas {row.get('resolvidas', 0):,} · "
                 f"tier_a {row.get('tier_a', 0):,} · tier_b {row.get('tier_b', 0):,}"),
        details=dict(row),
    )


def _step_refresh_cache(client, ctx: Dict[str, Any]) -> StepResult:
    if ctx["dry_run"]:
        return StepResult("refresh_cache", summary="dry-run — refresh pulado", details={})
    client.rpc("refresh_filter_options").execute()
    return StepResult("refresh_cache", summary="materialized view atualizada", details={})


_STEP_FUNCS: Dict[str, Callable] = {
    "data_cleanup":          _step_data_cleanup,
    "price_validation":      _step_price_validation,
    "normalize_products":    _step_normalize_products,
    "normalize_brands":      _step_normalize_brands,
    "recalc_unknown_brands": _step_recalc_unknown_brands,
    "normalize_platforms":   _step_normalize_platforms,
    "seed_depara":           _step_seed_depara,
    "auto_resolve_depara":   _step_auto_resolve_depara,
    "resolver_pendentes":    _step_resolver_pendentes,
    "refresh_cache":         _step_refresh_cache,
}


# ---------------------------------------------------------------------------
# Watermark incremental (coletas.id)
# ---------------------------------------------------------------------------

def _current_max_id(client) -> Optional[int]:
    try:
        resp = (client.table("coletas").select("id")
                .order("id", desc=True).limit(1).execute())
        if resp.data:
            return int(resp.data[0]["id"])
    except Exception as exc:
        logger.warning(f"[AdminAuto] Não foi possível ler max(id) de coletas: {exc}")
    return None


def get_last_watermark(client=None) -> Optional[int]:
    """Watermark do último run bem-sucedido (Supabase → fallback JSONL)."""
    client = client or _get_client()
    if client is not None:
        try:
            resp = (client.table(_RUNS_TABLE)
                    .select("watermark_id")
                    .in_("status", ["ok", "partial"])
                    .eq("dry_run", False)
                    .not_.is_("watermark_id", "null")
                    .order("started_at", desc=True)
                    .limit(1).execute())
            if resp.data:
                return int(resp.data[0]["watermark_id"])
        except Exception:
            pass
    for run in reversed(_read_local_runs()):
        if run.get("status") in ("ok", "partial") and not run.get("dry_run") \
                and run.get("watermark_id"):
            return int(run["watermark_id"])
    return None


# ---------------------------------------------------------------------------
# Persistência / histórico
# ---------------------------------------------------------------------------

def _read_local_runs() -> List[Dict[str, Any]]:
    if not _LOCAL_LOG.exists():
        return []
    runs = []
    try:
        for line in _LOCAL_LOG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                runs.append(json.loads(line))
    except Exception:
        pass
    return runs


def _persist_run(client, report: Dict[str, Any]) -> None:
    # Espelho local (sempre)
    try:
        _LOCAL_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _LOCAL_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(report, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        logger.warning(f"[AdminAuto] Falha ao gravar log local: {exc}")

    if client is None:
        return
    try:
        client.table(_RUNS_TABLE).insert({
            "run_id":       report["run_id"],
            "trigger":      report["trigger"],
            "status":       report["status"],
            "dry_run":      report["dry_run"],
            "started_at":   report["started_at"],
            "finished_at":  report["finished_at"],
            "duration_s":   report["duration_s"],
            "errors":       report["errors"],
            "watermark_id": report.get("watermark_id"),
            "steps":        report["steps"],
            "totals":       report.get("totals", {}),
        }).execute()
    except Exception as exc:
        logger.warning(
            f"[AdminAuto] Falha ao gravar em {_RUNS_TABLE} ({exc}). "
            "Aplique docs/migrations/006_admin_automation.sql."
        )


def get_last_run(client=None) -> Optional[Dict[str, Any]]:
    """Último run registrado (Supabase → fallback JSONL local)."""
    client = client or _get_client()
    if client is not None:
        try:
            resp = (client.table(_RUNS_TABLE).select("*")
                    .order("started_at", desc=True).limit(1).execute())
            if resp.data:
                return resp.data[0]
        except Exception:
            pass
    runs = _read_local_runs()
    return runs[-1] if runs else None


def get_run_history(client=None, limit: int = 20) -> List[Dict[str, Any]]:
    """Histórico de runs, mais recente primeiro."""
    client = client or _get_client()
    if client is not None:
        try:
            resp = (client.table(_RUNS_TABLE).select("*")
                    .order("started_at", desc=True).limit(limit).execute())
            if resp.data is not None:
                return resp.data
        except Exception:
            pass
    return list(reversed(_read_local_runs()))[:limit]


def should_run(client=None, min_hours: float = 24.0) -> bool:
    """True quando o último run (não dry-run) é mais antigo que `min_hours`."""
    last = get_last_run(client)
    if not last:
        return True
    if last.get("dry_run"):
        return True
    try:
        started = datetime.fromisoformat(str(last["started_at"]).replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - started).total_seconds() / 3600.0
        return age_h >= min_hours
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Notificação Telegram
# ---------------------------------------------------------------------------

def _has_changes(report: Dict[str, Any]) -> bool:
    keys = ("deleted", "updated", "total_updated", "aplicadas", "deduped",
            "novos_coletas", "novos_rac", "resolvidas")
    for step in report.get("steps", []):
        d = step.get("details") or {}
        if any(int(d.get(k, 0) or 0) > 0 for k in keys):
            return True
    return False


def _build_telegram_message(report: Dict[str, Any]) -> str:
    icon = {"ok": "✅", "partial": "⚠️", "error": "❌"}.get(report["status"], "ℹ️")
    lines = [
        f"{icon} <b>Automação Admin</b> · {report['trigger']} · {report['status'].upper()}",
        f"⏱ {report['duration_s']:.0f}s · {report['errors']} erro(s)",
        "",
    ]
    for step in report.get("steps", []):
        mark = "✅" if step.get("ok") else "❌"
        label = step.get("label", step.get("name", "?"))
        lines.append(f"{mark} {label}: {step.get('summary', '')}")
    return "\n".join(lines)


def _notify(report: Dict[str, Any]) -> None:
    if report["dry_run"]:
        return
    if report["errors"] == 0 and not _has_changes(report):
        return  # silencioso quando no-op — evita spam 2×/dia
    try:
        from utils.n8n_notify import _send_direct_telegram
        _send_direct_telegram(_build_telegram_message(report))
    except Exception as exc:
        logger.warning(f"[AdminAuto] Notificação Telegram falhou: {exc}")


# ---------------------------------------------------------------------------
# Runner principal
# ---------------------------------------------------------------------------

def _build_status(steps: List[StepResult]) -> str:
    if not steps:
        return "skipped"
    fails = sum(1 for s in steps if not s.ok)
    if fails == 0:
        return "ok"
    return "error" if fails == len(steps) else "partial"


def run_admin_automation(
    trigger: str = "manual",
    dry_run: bool = False,
    steps: Optional[List[str]] = None,
    notify: bool = True,
    full_scan: bool = False,
    client=None,
) -> Dict[str, Any]:
    """
    Executa a pipeline completa de manutenção ADMIN, sem interação humana.

    Args:
        trigger:   Origem do run (pos_coleta, cron, dashboard_auto, manual…).
        dry_run:   Simula — conta tudo, não grava nada.
        steps:     Subconjunto de STEP_ORDER (default: todas as etapas).
        notify:    Envia resumo ao Telegram (quando há mudanças/erros).
        full_scan: Ignora o watermark e varre o histórico inteiro.
        client:    Client Supabase opcional (default: utils.supabase_client).

    Returns:
        Relatório do run (mesmo formato persistido em admin_automation_runs).
    """
    started = datetime.now(timezone.utc)
    t0 = time.time()
    run_id = str(uuid.uuid4())
    client = client or _get_client()

    report: Dict[str, Any] = {
        "run_id": run_id,
        "trigger": trigger,
        "dry_run": dry_run,
        "started_at": started.isoformat(),
        "finished_at": None,
        "duration_s": 0.0,
        "status": "skipped",
        "errors": 0,
        "watermark_id": None,
        "steps": [],
        "totals": {},
    }

    if client is None:
        logger.warning("[AdminAuto] Supabase não configurado — automação pulada.")
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _persist_run(None, report)
        return report

    selected = [s for s in (steps or STEP_ORDER) if s in _STEP_FUNCS]
    since_id = None if full_scan else get_last_watermark(client)
    new_watermark = _current_max_id(client)

    logger.info(
        f"[AdminAuto] Run {run_id[:8]} · trigger={trigger} · dry_run={dry_run} · "
        f"etapas={len(selected)} · since_id={since_id or 'FULL'}"
    )

    ctx = {"dry_run": dry_run, "since_id": since_id}
    results: List[StepResult] = []
    for name in selected:
        t_step = time.time()
        try:
            result = _STEP_FUNCS[name](client, ctx)
        except Exception as exc:
            result = StepResult(name, ok=False, summary="falhou", error=str(exc))
            logger.error(f"[AdminAuto] Etapa {name} falhou: {exc}")
        result.duration_s = time.time() - t_step
        results.append(result)
        log = logger.success if result.ok else logger.warning
        log(f"[AdminAuto] {STEP_LABELS.get(name, name)} — {result.summary} "
            f"({result.duration_s:.1f}s)")

    report["steps"] = [r.as_dict() for r in results]
    report["errors"] = sum(1 for r in results if not r.ok)
    report["status"] = _build_status(results)
    report["duration_s"] = round(time.time() - t0, 1)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    if not dry_run and report["status"] in ("ok", "partial"):
        report["watermark_id"] = new_watermark

    _persist_run(client, report)
    if notify:
        _notify(report)

    logger.success(
        f"[AdminAuto] Run {run_id[:8]} concluído · status={report['status']} · "
        f"{report['duration_s']:.0f}s · {report['errors']} erro(s)"
    )
    return report
