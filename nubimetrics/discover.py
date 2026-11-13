"""Descoberta das tools do MCP da Nubimetrics → catálogo + MAPPING.md.

Primeira tarefa obrigatória do módulo (seção 1.1 do prompt):

1. ``tools/list`` → ``catalog/tools.json`` (nome, descrição, inputSchema);
2. uma chamada de teste por tool (``MLB1646``, mês atual) →
   ``catalog/samples/<tool>.json`` (resposta bruta);
3. ``catalog/MAPPING.md``: tool × fonte da seção 2 (A–O), campos, grão,
   paginação e limites.

Nada é simulado: tool cujo argumento obrigatório não dá para inferir NÃO é
chamada — vai para o MAPPING como "precisa de args" e aceita override via
``--args args.json`` (``{"<tool>": {...}}``). Tool com cara de escrita
(create/update/delete...) também não é chamada sem ``--incluir-escrita``.

Uso::

    python -m nubimetrics.discover                 # lista + amostras + MAPPING
    python -m nubimetrics.discover --so-listar     # só tools.json + MAPPING
    python -m nubimetrics.discover --args nubimetrics/catalog/args.json
    python -m nubimetrics.discover --offline       # refaz o MAPPING do catálogo salvo
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

CATALOG_DIR = Path(__file__).parent / "catalog"
SAMPLES_DIR = CATALOG_DIR / "samples"
CATEGORY_ID = "MLB1646"
SITE_ID = "MLB"

_WRITE_VERBS = re.compile(
    r"(^|_|-)(create|update|delete|remove|add|set|save|edit|put|post|patch|"
    r"insert|subscribe|unsubscribe|crear|eliminar|borrar|agregar|actualizar)(_|-|$)",
    re.I,
)

# Fontes da seção 2 do prompt → palavras que, no nome/descrição da tool,
# indicam a fonte. Heurístico: o MAPPING marca "a confirmar" e é revisado à mão.
SOURCES: Dict[str, Tuple[str, List[str]]] = {
    "A": ("Visão global (evolução das categorias)", ["global", "category_evolution", "evolucion", "evolução", "market_overview", "company_market", "companymarket"]),
    "B": ("Produtos mais vendidos", ["best_sell", "bestsell", "top_items", "itemranking", "item_ranking", "mas_vendid", "mais_vendid", "top_products"]),
    "C": ("Rankings de mercado (demanda/anúncios/catálogo/marcas/vendedores/lojas)", ["ranking", "brand_rank", "seller_rank", "sellerranking", "market_rank"]),
    "D": ("Analisar uma categoria (subcategorias)", ["category_detail", "categorydetail", "subcategor", "category_analysis", "analyze_category"]),
    "E": ("Evolução Brandshare (13 meses)", ["brandshare", "brand_share", "dashboardbrand", "share_evolution"]),
    "F": ("Relatórios dinâmicos (publicações/categorias/atributos)", ["report", "itemreport", "attribute", "atributo", "publication_report", "dynamic"]),
    "G": ("Top buscas do mês", ["search_term", "keyword", "trend", "bytrends", "busca", "busqueda", "queries"]),
    "H": ("Forecasting", ["forecast", "projection", "prediction", "previs"]),
    "I": ("Explorador de anúncios/categorias", ["explorer", "search_publication", "search_item", "publicationsexplorer", "categoryexplorer", "find"]),
    "J": ("Grupo de vendedores / analisar concorrente", ["competitor", "seller_analysis", "seller_group", "analysisbycompetitor", "dashboardbycompetitor", "seller_kpi", "seller_detail"]),
    "K": ("Lojas oficiais", ["official_store", "officialstore", "tienda_oficial", "loja_oficial"]),
    "L": ("Grupo de anúncios", ["publication_group", "item_group", "dashboardbypublication", "items_group"]),
    "M": ("Meu negócio (conta própria)", ["my_", "own_", "conversion_efficiency", "conversionefficiency", "my_business", "account", "sales_performance"]),
    "N": ("Oportunidades", ["opportunit", "oportunidad"]),
    "O": ("Reportes Estratégicos IA", ["promoteai", "ai_report", "strategic", "estrateg", "insight"]),
}

_PAGINATION_KEYS = re.compile(r"^(page|page_?size|per_?page|offset|limit|cursor|next|size|top|max_?results)$", re.I)


# ── lógica pura (testada em tests/test_nubimetrics_discover.py) ──────────────

def is_write_tool(tool: Dict[str, Any]) -> bool:
    """Tool com verbo de escrita no nome ou anotação ``readOnlyHint=False``."""
    ann = tool.get("annotations") or {}
    if ann.get("readOnlyHint") is True:
        return False
    if ann.get("destructiveHint") is True:
        return True
    return bool(_WRITE_VERBS.search(tool.get("name", "")))


def _guess_value(name: str, spec: Dict[str, Any], today: dt.date) -> Any:
    """Valor de teste para um parâmetro, ou ``...`` (Ellipsis) se não souber."""
    n = name.lower()
    typ = spec.get("type")
    enum = spec.get("enum")
    first_day = today.replace(day=1)
    if "category" in n or "categoria" in n:
        return [CATEGORY_ID] if typ == "array" else CATEGORY_ID
    if n in ("site", "site_id", "siteid", "country", "pais", "marketplace"):
        if enum:
            return SITE_ID if SITE_ID in enum else ("BR" if "BR" in enum else enum[0])
        return SITE_ID
    fmt = spec.get("format")
    if re.search(r"(date_?from|start|desde|from_?date|inicio|begin)", n):
        return first_day.isoformat() if fmt != "date-time" else f"{first_day}T00:00:00Z"
    if re.search(r"(date_?to|end|hasta|to_?date|fim|until)", n):
        return today.isoformat() if fmt != "date-time" else f"{today}T23:59:59Z"
    if n in ("month", "mes", "period", "periodo"):
        if enum:
            for cand in ("current_month", "month", "mes_actual", "current"):
                if cand in enum:
                    return cand
            return enum[0]
        return first_day.strftime("%Y-%m") if typ == "string" else today.month
    if n in ("year", "ano", "año", "anio"):
        return today.year if typ in ("integer", "number") else str(today.year)
    if _PAGINATION_KEYS.match(name):
        if re.search(r"size|limit|per|top|max", n):
            return 50
        return 1 if "page" in n else 0
    if enum:
        return enum[0]
    if "default" in spec:
        return spec["default"]
    return ...


def build_test_args(tool: Dict[str, Any], today: Optional[dt.date] = None) -> Tuple[Dict[str, Any], List[str]]:
    """Argumentos de teste a partir do ``inputSchema``.

    Preenche os obrigatórios e os opcionais reconhecíveis (categoria, site,
    período). Returns: ``(args, obrigatórios_sem_valor)``.
    """
    today = today or dt.date.today()
    schema = tool.get("inputSchema") or {}
    props: Dict[str, Any] = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    args: Dict[str, Any] = {}
    missing: List[str] = []
    for name, spec in props.items():
        val = _guess_value(name, spec or {}, today)
        if val is ...:
            if name in required:
                missing.append(name)
            continue
        recognizable = bool(re.search(r"categor|site|date|desde|hasta|start|end|month|mes|period|year|ano", name, re.I))
        if name in required or recognizable:
            args[name] = val
    return args, missing


def guess_sources(tool: Dict[str, Any]) -> List[str]:
    """Fontes A–O da seção 2 que o nome/descrição da tool sugerem."""
    hay = f"{tool.get('name', '')} {tool.get('description', '')}".lower()
    return [k for k, (_, kws) in SOURCES.items() if any(kw in hay for kw in kws)]


def pagination_params(tool: Dict[str, Any]) -> List[str]:
    props = (tool.get("inputSchema") or {}).get("properties") or {}
    return [p for p in props if _PAGINATION_KEYS.match(p)]


def granularity(tool: Dict[str, Any]) -> str:
    """Grão temporal pelo schema/descrição: dia, mês, ambos ou ?."""
    txt = json.dumps(tool.get("inputSchema") or {}).lower() + " " + (tool.get("description") or "").lower()
    day = bool(re.search(r"daily|diari|\bday\b|\bdia\b|date_from|date_to|desde|hasta|start_date", txt))
    month = bool(re.search(r"monthly|mensual|mensal|\bmonth\b|\bmes\b|period", txt))
    return {(True, True): "dia/mês", (True, False): "dia", (False, True): "mês"}.get((day, month), "?")


def first_record(payload: Any) -> Optional[Dict[str, Any]]:
    """Primeiro registro tabular de uma resposta (lista ou dict com lista)."""
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                return item
        return None
    if isinstance(payload, dict):
        for key in ("data", "items", "results", "rows", "records", "ranking", "result"):
            if isinstance(payload.get(key), list):
                return first_record(payload[key])
        for val in payload.values():
            if isinstance(val, list) and val and isinstance(val[0], dict):
                return val[0]
        return payload
    return None


def count_records(payload: Any) -> Optional[int]:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("data", "items", "results", "rows", "records", "ranking", "result"):
            if isinstance(payload.get(key), list):
                return len(payload[key])
    return None


def render_mapping(tools: List[Dict[str, Any]], samples: Dict[str, Dict[str, Any]], generated_at: str) -> str:
    """Monta o MAPPING.md (markdown) do catálogo + amostras."""
    lines = [
        "# MAPPING — tools do MCP Nubimetrics × fontes do módulo",
        "",
        f"Gerado por `python -m nubimetrics.discover` em {generated_at} a partir de "
        f"`tools/list` real ({len(tools)} tools). Categoria de teste `{CATEGORY_ID}`, mês atual.",
        "",
        "> A coluna **Fonte** é heurística (nome/descrição da tool) — **a confirmar** na revisão.",
        "",
        "## Tools",
        "",
        "| Tool | Fonte (seção 2) | Teste | Registros | Grão | Paginação | Campos retornados (1º registro) |",
        "|---|---|---|---|---|---|---|",
    ]
    covered: Dict[str, List[str]] = {k: [] for k in SOURCES}
    for tool in tools:
        name = tool["name"]
        srcs = guess_sources(tool)
        for s in srcs:
            covered[s].append(name)
        smp = samples.get(name, {})
        status = smp.get("status", "não chamada")
        rec = first_record(smp.get("payload"))
        fields = ", ".join(f"`{k}`" for k in list(rec)[:25]) if isinstance(rec, dict) else "—"
        if isinstance(rec, dict) and len(rec) > 25:
            fields += f" … (+{len(rec) - 25})"
        n = count_records(smp.get("payload"))
        pag = ", ".join(f"`{p}`" for p in pagination_params(tool)) or "—"
        lines.append(
            f"| `{name}` | {', '.join(srcs) or '?'} | {status} | {n if n is not None else '—'} "
            f"| {granularity(tool)} | {pag} | {fields} |"
        )
    lines += ["", "## Cobertura das fontes da seção 2", "", "| # | Fonte | Tool(s) | Situação |", "|---|---|---|---|"]
    for key, (label, _) in SOURCES.items():
        tl = covered[key]
        sit = "candidata — confirmar" if tl else "**indisponível via MCP**"
        lines.append(f"| {key} | {label} | {', '.join(f'`{t}`' for t in tl) or '—'} | {sit} |")
    lines += ["", "## Detalhe por tool", ""]
    for tool in tools:
        name = tool["name"]
        smp = samples.get(name, {})
        lines += [
            f"### `{name}`",
            "",
            (tool.get("description") or "_sem descrição_").strip(),
            "",
            "Parâmetros:",
            "",
        ]
        props = (tool.get("inputSchema") or {}).get("properties") or {}
        req = set((tool.get("inputSchema") or {}).get("required") or [])
        if not props:
            lines.append("- (nenhum)")
        for p, spec in props.items():
            spec = spec or {}
            extra = f" enum={spec['enum']}" if spec.get("enum") else ""
            desc = (spec.get("description") or "").replace("\n", " ")
            lines.append(f"- `{p}` ({spec.get('type', '?')}{', obrigatório' if p in req else ''}){extra} — {desc}")
        lines += ["", f"Teste: **{smp.get('status', 'não chamada')}**"]
        if smp.get("args") is not None:
            lines.append(f"Args: `{json.dumps(smp['args'], ensure_ascii=False)}`")
        if smp.get("error"):
            lines.append(f"Erro: `{smp['error'][:300]}`")
        lines.append("")
    return "\n".join(lines) + "\n"


# ── rede ────────────────────────────────────────────────────────────────────

async def _list_tools(session: Any) -> List[Dict[str, Any]]:
    from nubimetrics.client import to_jsonable

    tools: List[Dict[str, Any]] = []
    cursor = None
    while True:
        res = await (session.list_tools(cursor=cursor) if cursor else session.list_tools())
        tools += [to_jsonable(t) for t in res.tools]
        cursor = getattr(res, "nextCursor", None) or getattr(res, "next_cursor", None)
        if not cursor:
            return tools


async def _call_one(session: Any, tool: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    from nubimetrics.client import mascarar, redact, result_payload, to_jsonable

    try:
        res = await session.call_tool(tool["name"], args)
    except Exception as exc:  # noqa: BLE001 — registra e segue para a próxima tool
        return {"status": "erro", "args": args, "error": redact(f"{type(exc).__name__}: {exc}")}
    raw = to_jsonable(res)
    is_err = bool(raw.get("isError")) if isinstance(raw, dict) else False
    # Identidade da conta (account_id, seller_id, e-mail…) nunca vai para a
    # amostra versionada — revisão do PR #449.
    out = {"status": "erro da tool" if is_err else "ok", "args": args,
           "raw": mascarar(raw), "payload": mascarar(result_payload(res))}
    if is_err:
        out["error"] = redact(json.dumps(out["payload"], ensure_ascii=False)[:500])
    return out


# Ferramentas que gravam algo do lado da Nubimetrics (apesar de readOnlyHint).
NEVER_CALL = {"nubi_user_feedback"}
# Ordem: quem produz ids (grupos, membros, categoria-folha) antes de quem consome.
_ORDER = [
    "nubi_user_info", "nubi_seller_info", "meli_category_explorer",
    "nubi_competition_seller_groups", "nubi_competition_seller_group_members",
    "nubi_competition_publication_groups", "nubi_competition_publication_group_members",
]


def unwrap_schema(tool: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """Schema efetivo dos argumentos. O MCP da Nubimetrics embrulha tudo em
    ``{"inp": {...}}`` com ``$ref`` para ``$defs`` — devolve ``(schema, embrulhado)``."""
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties") or {}
    if list(props) == ["inp"]:
        inner = props["inp"]
        if "$ref" in inner:
            inner = (schema.get("$defs") or {}).get(inner["$ref"].split("/")[-1], {})
        return inner, True
    return schema, False


def plan_calls(tool: Dict[str, Any], ctx: Dict[str, Any], today: dt.date) -> List[Tuple[str, Dict[str, Any], List[str]]]:
    """Chamadas de teste de uma tool: ``[(rótulo, args, faltando)]``.

    Usa o contexto das chamadas anteriores (group_id, member_code, folha da
    categoria) para as tools encadeadas. Os args voltam já embrulhados em
    ``inp`` quando o schema pede.
    """
    inner, wrapped = unwrap_schema(tool)
    props = inner.get("properties") or {}
    required = set(inner.get("required") or [])
    month = today.replace(day=1)
    last_closed = (month - dt.timedelta(days=1)).replace(day=1)
    base: Dict[str, Any] = {}
    for name, spec in props.items():
        n = name.lower()
        if n == "category_id":
            base[name] = ctx.get("leaf_category", CATEGORY_ID) if tool["name"] == "meli_publication_explorer" else CATEGORY_ID
        elif n == "date":
            base[name] = (last_closed if "forecast" in tool["name"] else month).isoformat()
        elif n == "date_from":
            base[name] = (month.replace(year=month.year - 1) if "market_" in tool["name"] else month).isoformat()
        elif n == "date_to":
            base[name] = (month if "market_" in tool["name"] else today).isoformat()
        elif n == "group_id":
            key = "seller_group_id" if "seller" in tool["name"] else "publication_group_id"
            if key in ctx:
                base[name] = ctx[key]
        elif n == "seller_id_competitor" and "competitor_code" in ctx:
            base[name] = ctx["competitor_code"]
        elif n in ("member_code", "member_owner") and n in ctx:
            base[name] = ctx[n]
        elif n == "title":
            base[name] = "ar condicionado"
        elif n == "search_term" and tool["name"] == "meli_category_explorer":
            base[name] = "Ar Condicionado"
        elif n == "sort_by" and tool["name"] == "meli_publication_explorer":
            base[name] = "gmv"
        elif n == "days":
            base[name] = 30
    missing = [r for r in required if r not in base]
    variants: List[Tuple[str, Dict[str, Any]]] = [("", base)]
    topic = props.get("topic") or {}
    if topic.get("enum"):
        variants = [(t, {**base, "topic": t}) for t in topic["enum"]]
        missing = [m for m in missing if m != "topic"]
    return [(lbl, {"inp": a} if wrapped else a, missing) for lbl, a in variants]


def _harvest(name: str, payload: Any, ctx: Dict[str, Any]) -> None:
    """Guarda ids de uma resposta para as chamadas encadeadas seguintes."""
    rec = first_record(payload)
    if not isinstance(rec, dict):
        return
    if name == "nubi_competition_seller_groups" and "group_id" in rec:
        ctx["seller_group_id"] = rec["group_id"]
    elif name == "nubi_competition_publication_groups" and "group_id" in rec:
        ctx["publication_group_id"] = rec["group_id"]
    elif name == "nubi_competition_seller_group_members":
        rows = payload.get("members") if isinstance(payload, dict) else None
        rows = rows if isinstance(rows, list) else [rec]
        for r in rows:
            if isinstance(r, dict) and not r.get("is_self") and r.get("member_code"):
                ctx["competitor_code"] = r["member_code"]
                break
    elif name == "nubi_competition_publication_group_members":
        for k in ("member_code", "member_owner"):
            if rec.get(k):
                ctx[k] = rec[k]
    elif name == "meli_category_explorer":
        rows = payload.get("categories") if isinstance(payload, dict) else None
        for r in rows if isinstance(rows, list) else [rec]:
            path = str(r.get("category_path") or r.get("path") or "")
            if CATEGORY_ID in path:
                ctx["leaf_category"] = path.split("-")[-1]
                break


async def discover(only_list: bool, overrides: Dict[str, Dict[str, Any]], allow_write: bool) -> None:
    from nubimetrics.client import open_session

    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today()
    async with open_session() as session:
        tools = await _list_tools(session)
        logger.info(f"[Nubimetrics] tools/list: {len(tools)} tools")
        _write_json(CATALOG_DIR / "tools.json", tools)
        samples: Dict[str, Dict[str, Any]] = {}
        ctx: Dict[str, Any] = {}
        ordered = sorted(tools, key=lambda t: _ORDER.index(t["name"]) if t["name"] in _ORDER else len(_ORDER))
        for tool in ordered if not only_list else []:
            name = tool["name"]
            if (name in NEVER_CALL or is_write_tool(tool)) and not allow_write:
                samples[name] = {"status": "pulada (grava do lado da Nubimetrics)"}
                continue
            for label, args, missing in plan_calls(tool, ctx, today):
                key = f"{name}__{label}" if label else name
                if name in overrides:
                    args = overrides[name]
                    missing = []
                if missing:
                    samples[key] = {"status": f"sem args: {', '.join(missing)} (dependência sem dado)", "args": args}
                    continue
                logger.info(f"[Nubimetrics] chamando {key} {json.dumps(args, ensure_ascii=False)}")
                samples[key] = await _call_one(session, tool, args)
                _write_json(SAMPLES_DIR / f"{_safe(key)}.json", samples[key])
                if samples[key]["status"] == "ok":
                    _harvest(name, samples[key]["payload"], ctx)
    _write_json(CATALOG_DIR / "samples_status.json",
                {k: {kk: v for kk, v in s.items() if kk in ("status", "args", "error")} for k, s in samples.items()})
    _write_mapping(tools, samples)


def _load_offline() -> None:
    tools = json.loads((CATALOG_DIR / "tools.json").read_text(encoding="utf-8"))
    status_path = CATALOG_DIR / "samples_status.json"
    samples = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    for f in SAMPLES_DIR.glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        for t in tools:
            if _safe(t["name"]) == f.stem:
                samples[t["name"]] = data
    _write_mapping(tools, samples)


def _write_mapping(tools: List[Dict[str, Any]], samples: Dict[str, Dict[str, Any]]) -> None:
    now = dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    path = CATALOG_DIR / "MAPPING_auto.md"  # MAPPING.md é a versão revisada à mão
    path.write_text(render_mapping(tools, samples, now), encoding="utf-8")
    logger.success(f"[Nubimetrics] {path} gerado")


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--so-listar", action="store_true", help="só tools/list, sem chamadas de teste")
    ap.add_argument("--args", type=Path, help='JSON {"<tool>": {args}} que sobrepõe os args inferidos')
    ap.add_argument("--incluir-escrita", action="store_true", help="também chama tools com cara de escrita")
    ap.add_argument("--offline", action="store_true", help="refaz o MAPPING.md do catálogo já salvo")
    a = ap.parse_args(argv)
    if a.offline:
        _load_offline()
        return 0
    overrides = json.loads(a.args.read_text(encoding="utf-8")) if a.args else {}
    asyncio.run(discover(a.so_listar, overrides, a.incluir_escrita))
    return 0


if __name__ == "__main__":
    sys.exit(main())
