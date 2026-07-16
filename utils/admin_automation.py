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
    9. resolver_pendentes    — RPC resolver_coletas_pendentes (migrations 004+007)
   10. refresh_cache         — RPC refresh_filter_options (MV, CONCURRENTLY)

Performance/timeouts (migration 007): índices em coletas.produto e nas linhas
ainda não-normalizadas, refresh da MV via CONCURRENTLY e statement_timeout de
120s só no service_role removem os timeouts (57014) que faziam a pipeline
terminar PARTIAL. Ver docs/migrations/007_admin_automation_perf.sql.

Concorrência (migration 008): o runner serializa execuções com o mutex
admin_automation_lock (TTL anti-deadlock) — runs concorrentes pulam em vez de
travar um no outro. Ver docs/migrations/008_admin_automation_concurrency.sql.

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
    "sku_backfill":          "🔢 Backfill de SKU (sync + propostas)",
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
    # Incremental: só varre coletas.id > watermark (índice pkey) em vez da tabela
    # inteira — o anti-join/DISTINCT sobre 620k+ linhas estourava o statement
    # timeout (57014). p_since_id=None (full_scan) mantém a varredura completa.
    resp = client.rpc(
        "seed_depara_nomes_novos", {"p_since_id": ctx.get("since_id")}
    ).execute()
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
    # Dry-run: a heurística é pura e roda na simulação; a chamada LLM não é
    # feita (custo) — os nomes que iriam para ela são reportados em
    # `llm_pendentes` para a contagem refletir o fluxo real.
    politica = os.getenv("ADMIN_AUTO_RESIDUAL_POLICY", "terminal").strip().lower()
    llm_error: Optional[str] = None
    llm_usado = False
    llm_pendentes = 0
    heuristica_aplicada = 0
    if residuais:
        if _llm_available():
            if ctx["dry_run"]:
                llm_pendentes = len(residuais)
            else:
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
        "llm_pendentes": llm_pendentes,
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
                 f"restam {max(restantes, 0):,}"
                 + (f" ({llm_pendentes:,} iriam ao LLM — não simulado)"
                    if llm_pendentes else "")),
        details=details,
        error=None if erros == 0 else f"{erros} erro(s) ao aplicar resoluções",
    )


# ---------------------------------------------------------------------------
# Etapa 9 — backfill de SKU (sync de-para → coletas + propostas por atributos)
#
# Fecha o gap "MAPEADO sem sku_resolvido" medido na validação de 09/07/2026
# (70.441 linhas / 195 títulos na janela 01/06–09/07):
#   • resolver_coletas_pendentes só propaga o de-para para linhas com
#     estado_match IS NULL — linhas resolvidas ANTES de o SKU ser cravado no
#     de-para ficavam órfãs para sempre (frente SYNC);
#   • a auto-resolução (etapa 8) nunca crava SKU (p_sku=None, política
#     conservadora) e o `familia` do de-para usa outro namespace que o do
#     catálogo, então o SKU nunca era derivado (frente PROPOSTAS).
# ---------------------------------------------------------------------------

def _fetch_depara_mapeado(client) -> List[Dict[str, Any]]:
    """Carrega o de-para MAPEADO (paginado) com os campos do backfill."""
    rows: List[Dict[str, Any]] = []
    offset = 0
    while True:
        resp = (client.table("produtos_depara_nome")
                .select("nome_coletado,familia,sku,marca_norm")
                .eq("estado", "MAPEADO")
                .order("nome_coletado")
                .range(offset, offset + _PAGE - 1)
                .execute())
        batch = resp.data or []
        rows.extend(batch)
        if len(batch) < _PAGE:
            break
        offset += _PAGE
    return rows


def _fetch_catalog_rows(client) -> List[Dict[str, Any]]:
    """produtos_catalogo com as colunas que o sku_matcher precisa (241 linhas)."""
    resp = (client.table("produtos_catalogo")
            .select("sku,marca,capacidade_btu,ciclo,familia_linha,voltagem,ativo")
            .execute())
    return resp.data or []


def compute_sku_proposals(
    depara_rows: List[Dict[str, Any]],
    catalog_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Propostas de SKU para nomes MAPEADO sem SKU no de-para.

    Reusa `utils.sku_matcher` (de-para v2, funções puras): entra na lista
    apenas o que resolve com confiança ALTA — 1 SKU unívoco na família-linha,
    com desempate por voltagem. Regra de ouro preservada: nada de chute;
    ambíguos ficam fora (e continuam visíveis como pendência no de-para).

    Args:
        depara_rows: dicts com nome_coletado, familia, sku, marca_norm
        catalog_rows: dicts de produtos_catalogo (ver _fetch_catalog_rows)

    Returns:
        Lista de propostas: nome, familia (atual, preservada), sku_proposto,
        metodo e motivo (auditoria).
    """
    from utils.sku_matcher import build_catalog, resolve_sku

    catalog = build_catalog(catalog_rows)
    out: List[Dict[str, Any]] = []
    for row in depara_rows:
        if (row.get("sku") or "").strip():
            continue
        nome = row["nome_coletado"]
        res = resolve_sku(nome, row.get("marca_norm"), catalog)
        if res.estado == "MAPEADO" and res.sku_v2 and res.confianca == "alta":
            out.append({
                "nome": nome,
                "familia": row.get("familia"),
                "sku_proposto": res.sku_v2,
                "metodo": res.metodo,
                "motivo": res.motivo,
            })
    return out


def get_sku_backfill_proposals(client) -> List[Dict[str, Any]]:
    """Fila de propostas de SKU para a página 🧬 (mesma lógica da etapa)."""
    pendentes = [r for r in _fetch_depara_mapeado(client)
                 if not (r.get("sku") or "").strip()]
    if not pendentes:
        return []
    return compute_sku_proposals(pendentes, _fetch_catalog_rows(client))


def apply_sku_resolution(client, nome: str, familia: Optional[str],
                         sku: str, marca: Optional[str]) -> Dict[str, Any]:
    """Grava o SKU no de-para e re-propaga (mesma RPC do editor da página 🧬).

    A RPC valida o SKU contra produtos_catalogo e atualiza TODAS as linhas de
    coletas/rac_monitoramento do título (sem o guard de estado_match IS NULL
    do resolver — é isso que fecha as linhas órfãs).
    """
    resp = client.rpc("admin_normalizar_nome", {
        "p_nome":    nome,
        "p_estado":  "MAPEADO",
        "p_familia": familia,
        "p_sku":     sku,
        "p_marca":   marca,
    }).execute()
    return resp.data if isinstance(resp.data, dict) else {}


def _step_sku_backfill(client, ctx: Dict[str, Any]) -> StepResult:
    if not _env_flag("ADMIN_SKU_BACKFILL", True):
        return StepResult("sku_backfill",
                          summary="desligado (ADMIN_SKU_BACKFILL=off)", details={})

    depara = _fetch_depara_mapeado(client)
    com_sku = [r for r in depara if (r.get("sku") or "").strip()]
    sem_sku = [r for r in depara if not (r.get("sku") or "").strip()]
    max_apply = _env_int("ADMIN_SKU_BACKFILL_MAX", 500)

    # ── 1. SYNC: de-para já tem SKU, coletas ainda tem linhas órfãs ──────
    sync_nomes = sync_linhas = erros = 0
    for row in com_sku:
        if sync_nomes >= max_apply:
            break
        nome = row["nome_coletado"]
        try:
            # Gate barato: existe ao menos 1 linha órfã (sku_resolvido NULL)?
            # Usa o índice parcial idx_coletas_produto_orphan — o count="exact"
            # com head=True varria todas as ~5k linhas do produto (heap scan de
            # ~3,5s por nome) e estourava o timeout, retornando 500/corpo vazio
            # ("JSON could not be generated"). limit(1000) para o cursor cedo e
            # dá um número suficiente para o preview do dry-run.
            orfas = (client.table("coletas")
                     .select("id")
                     .eq("produto", nome)
                     .is_("sku_resolvido", "null")
                     .limit(1000)
                     .execute().data or [])
            pend = len(orfas)
            if pend == 0:
                continue
            if ctx["dry_run"]:
                sync_nomes += 1
                sync_linhas += pend
                continue
            payload = apply_sku_resolution(
                client, nome, row.get("familia"), row["sku"], row.get("marca_norm"))
            sync_nomes += 1
            sync_linhas += int(payload.get("coletas_atualizadas", 0) or 0)
        except Exception as exc:
            erros += 1
            logger.warning(f"[AdminAuto] sku_backfill sync falhou p/ '{nome[:60]}': {exc}")

    # ── 2. PROPOSTAS: derivação por atributos (confiança alta) ───────────
    # Aplicação automática é GATED (default: report-only) — respeita a decisão
    # de não cravar SKU em massa antes do dedup do catálogo (migration 009,
    # FASE 1.b). Aprovação humana 1-clique fica na página 🧬 Família & SKU.
    propostas: List[Dict[str, Any]] = []
    try:
        propostas = compute_sku_proposals(sem_sku, _fetch_catalog_rows(client))
    except Exception as exc:
        erros += 1
        logger.warning(f"[AdminAuto] sku_backfill propostas falharam: {exc}")

    auto_apply = _env_flag("ADMIN_SKU_BACKFILL_APPLY", False)
    aplicadas = 0
    if auto_apply:
        for p in propostas:
            if aplicadas >= max_apply:
                break
            # Dry-run simula a aplicação (conta sem gravar), como nas demais
            # etapas — senão o dry-run reportaria 0 com a flag ligada.
            if ctx["dry_run"]:
                aplicadas += 1
                continue
            try:
                apply_sku_resolution(client, p["nome"], p["familia"],
                                     p["sku_proposto"], None)
                aplicadas += 1
            except Exception as exc:
                erros += 1
                logger.warning(
                    f"[AdminAuto] sku_backfill apply falhou p/ '{p['nome'][:60]}': {exc}")

    details = {
        "depara_mapeado": len(depara),
        "com_sku": len(com_sku),
        "sem_sku": len(sem_sku),
        "sync_nomes": sync_nomes,
        "sync_linhas_coletas": sync_linhas,
        "propostas": len(propostas),
        "propostas_aplicadas": aplicadas,
        "auto_apply": auto_apply,
        "amostra_propostas": propostas[:10],
        "erros": erros,
        "dry_run": ctx["dry_run"],
    }
    return StepResult(
        "sku_backfill",
        ok=erros == 0,
        summary=(f"sync {sync_nomes} nome(s) → {sync_linhas:,} linha(s) · "
                 f"{len(propostas)} proposta(s) de SKU"
                 + (f" · {aplicadas} aplicadas" if auto_apply
                    else " (aprovação na página 🧬)")),
        details=details,
        error=None if erros == 0 else f"{erros} erro(s) no backfill",
    )


# ---------------------------------------------------------------------------
# Etapas 10-11 — RPCs de propagação e cache
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
    # A MV mv_filter_options_90d recalcula 5 array_agg(DISTINCT ...) sobre ~90
    # dias de `coletas` (varredura de 620k+ linhas, dezenas de segundos). Não
    # cabe no statement_timeout do PostgREST no volume atual, então um 57014
    # aqui NÃO deve derrubar o run inteiro para `partial` — é só o cache de
    # filtros do dashboard (tolerante a defasagem; a MV é reaproveitada até o
    # próximo refresh que conseguir concluir). Erros reais (não-timeout) ainda
    # sobem como falha da etapa.
    try:
        client.rpc("refresh_filter_options").execute()
        return StepResult("refresh_cache", summary="materialized view atualizada", details={})
    except Exception as exc:
        msg = str(exc)
        if "57014" in msg or "statement timeout" in msg.lower():
            logger.warning(
                "[AdminAuto] refresh_cache: MV excedeu o statement_timeout — "
                "pulado (cache de filtros segue com o snapshot anterior). "
                "Refresh completo exige rodar sob service_role (120s) ou "
                "agendar via pg_cron."
            )
            return StepResult(
                "refresh_cache",
                ok=True,
                summary="pulado — MV excedeu o statement_timeout (cache mantém snapshot anterior)",
                details={"skipped": "statement_timeout"},
            )
        raise


_STEP_FUNCS: Dict[str, Callable] = {
    "data_cleanup":          _step_data_cleanup,
    "price_validation":      _step_price_validation,
    "normalize_products":    _step_normalize_products,
    "normalize_brands":      _step_normalize_brands,
    "recalc_unknown_brands": _step_recalc_unknown_brands,
    "normalize_platforms":   _step_normalize_platforms,
    "seed_depara":           _step_seed_depara,
    "auto_resolve_depara":   _step_auto_resolve_depara,
    "sku_backfill":          _step_sku_backfill,
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


def _get_last_effective_run(client=None) -> Optional[Dict[str, Any]]:
    """Último run REAL (dry_run=False) — Supabase → fallback JSONL local."""
    client = client or _get_client()
    if client is not None:
        try:
            resp = (client.table(_RUNS_TABLE).select("started_at,status,dry_run")
                    .eq("dry_run", False)
                    .order("started_at", desc=True).limit(1).execute())
            if resp.data:
                return resp.data[0]
        except Exception:
            pass
    for run in reversed(_read_local_runs()):
        if not run.get("dry_run"):
            return run
    return None


def should_run(client=None, min_hours: float = 24.0) -> bool:
    """True quando o último run real é mais antigo que `min_hours`.

    Dry-runs são ignorados: uma simulação recente não substitui (nem deve
    adiar/forçar) a manutenção de verdade.
    """
    last = _get_last_effective_run(client)
    if not last:
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
            "novos_coletas", "novos_rac", "resolvidas",
            "sync_linhas_coletas", "propostas_aplicadas")
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


# TTL do mutex (migration 008): se um run morrer no meio, o lock expira sozinho
# e o próximo run consegue prosseguir. Folgado o bastante p/ a pipeline inteira.
_LOCK_TTL_SECONDS = 900


def _try_acquire_lock(client, run_id: str, trigger: str) -> Tuple[bool, bool]:
    """
    Claim do mutex admin_automation_lock — serializa execuções concorrentes.

    Runs concorrentes (cliques repetidos em "Executar agora", manual sobrepondo
    pos_coleta) travavam em REFRESH CONCURRENTLY / seed_depara e estouravam o
    statement_timeout. O mutex deixa só um run por vez.

    Returns:
        (acquired, lock_backed)
        acquired    — True se este run pode prosseguir.
        lock_backed — False se a RPC não existe (migration 008 não aplicada);
                      o chamador segue fail-open (comportamento antigo) e NÃO
                      tenta liberar nada.
    """
    try:
        resp = client.rpc("admin_automation_try_lock", {
            "p_holder": run_id, "p_trigger": trigger, "p_ttl_seconds": _LOCK_TTL_SECONDS,
        }).execute()
        return bool(resp.data), True
    except Exception as exc:
        logger.warning(f"[AdminAuto] Mutex indisponível ({exc}) — seguindo sem serialização.")
        return True, False


def _release_lock(client, run_id: str) -> None:
    try:
        client.rpc("admin_automation_unlock", {"p_holder": run_id}).execute()
    except Exception as exc:
        logger.debug(f"[AdminAuto] Falha ao liberar mutex (TTL cobre): {exc}")


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

    # Serializa execuções (migration 008): dry-run é read-only e não disputa
    # locks; os demais pegam o mutex e pulam se outro run já estiver rodando —
    # evita que runs concorrentes travem em REFRESH CONCURRENTLY / seed_depara.
    holds_lock = False
    if not dry_run:
        acquired, lock_backed = _try_acquire_lock(client, run_id, trigger)
        if not acquired:
            logger.info(
                f"[AdminAuto] Run {run_id[:8]} pulado — outra execução em "
                f"andamento (mutex admin_automation_lock)."
            )
            report["status"] = "skipped"
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            report["duration_s"] = round(time.time() - t0, 1)
            return report  # skip de mutex não é persistido (evita ruído no histórico)
        holds_lock = lock_backed

    try:
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
    finally:
        if holds_lock:
            _release_lock(client, run_id)
