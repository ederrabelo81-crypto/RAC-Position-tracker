#!/usr/bin/env python3
"""
pricetrack_discovery.py — Descoberta SÓ LEITURA do export NDJSON do PriceTrack.

Fase 0 do painel "Diagnóstico" (docs/diagnostico_fase0.md). Responde, a partir
do arquivo bruto da API, o que o painel precisa saber antes de qualquer DDL:

  * nomes de todas as colunas do NDJSON e 3 linhas de exemplo;
  * valores distintos de marketplace, categoria e subcategoria (com contagem);
  * subcategorias e famílias dentro das categorias pedidas (padrão: AR CONDICIONADO);
  * se existe algum campo de característica (BTU, Inverter/On-Off,
    Quente/Frio, voltagem) — e, se não existir, quanto dá para tirar do título;
  * ofertas (SKU × seller distintos) por marketplace nas categorias pedidas —
    o mesmo número do bloco 1 do Diagnóstico do PriceTrack.

**Nunca grava no banco.** Só escreve arquivos locais: o NDJSON baixado e o
relatório em `logs/pricetrack_discovery_<data>_<versao>.md/.json`.

Lê o .ndjson.gz em streaming (linha a linha), sem carregar o arquivo inteiro
em memória.

Uso (no PC coletor, com PRICETRACK_API_KEY no .env):
    python scripts/pricetrack_discovery.py                       # ontem, export V2
    python scripts/pricetrack_discovery.py --data 2026-11-03 --versao v1
    python scripts/pricetrack_discovery.py --arquivo imports/pricetrack/api/raw/offers-2026-11-03.ndjson.gz
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

from loguru import logger

_OUT_DIR = _PROJECT_ROOT / "imports" / "pricetrack" / "api" / "descoberta"
_LOG_DIR = _PROJECT_ROOT / "logs"

EXPORT_V1_PATH = "/exports-external/collects-offers"
EXPORT_V2_PATH = "/exports-external/collects-offers-v2"

#: A V2 lê do Redis, que só guarda os últimos 90 dias (contando hoje).
_V2_RETENCAO_DIAS = 90

# Campo lógico → grafias aceitas. O NDJSON vem em snake_case; o OpenAPI
# documenta camelCase. Aceitamos as duas.
_CAMPOS: Dict[str, tuple] = {
    "marketplace": ("marketplace",),
    "category": ("category", "productCategory", "product_category"),
    "subcategory": ("subcategory", "productSubcategory", "product_subcategory"),
    "family": ("family", "productFamily", "product_family"),
    "brand": ("brand", "productBrand", "product_brand"),
    "sku": ("sku", "productSku", "product_sku"),
    "seller": ("seller",),
    "status": ("status",),
    "title": ("title",),
    "product_name": ("product_name", "productName"),
    "screenshot_url": ("screenshot_url", "screenshotUrl"),
    "collection_hour": ("collection_hour", "collectionHour"),
}

#: Pedaço do NOME de coluna que sugere característica do produto.
_CHAVES_CARACTERISTICA = re.compile(
    r"btu|capac|volt|tens[aã]o|inverter|tecnolog|ciclo|quente|frio|potenc|attribute|spec|caracter",
    re.IGNORECASE,
)

#: Características que dá para tirar do TEXTO (título/nome do produto).
_PADROES_TEXTO: Dict[str, re.Pattern] = {
    "btu": re.compile(r"\b(\d{1,2}[.\s]?\d{3})\s*btu", re.IGNORECASE),
    "inverter": re.compile(r"\binverter\b", re.IGNORECASE),
    "on_off": re.compile(r"\bon[\s/-]?off\b|\bconvencional\b", re.IGNORECASE),
    "quente_frio": re.compile(r"quente\s*(?:e|/|-)?\s*frio|\bq/f\b", re.IGNORECASE),
    "so_frio": re.compile(r"s[óo]\s*frio", re.IGNORECASE),
    "voltagem": re.compile(r"\b(?:110|127|220)\s*v\b|bivolt", re.IGNORECASE),
}

_TOP_N = 40


def _pick(row: Dict[str, Any], campo: str) -> str:
    """Valor textual do campo lógico, testando as grafias conhecidas."""
    for chave in _CAMPOS[campo]:
        valor = row.get(chave)
        if valor is not None and str(valor).strip() != "":
            return str(valor).strip()
    return ""


def iter_ndjson_gz(path: Path) -> Iterator[Dict[str, Any]]:
    """Itera as linhas de um .ndjson.gz uma a uma (streaming).

    Linha que não é JSON válido é pulada e contada no log — não aborta a
    descoberta, mas também não some em silêncio.
    """
    invalidas = 0
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for linha in fh:
            linha = linha.strip()
            if not linha:
                continue
            try:
                obj = json.loads(linha)
            except json.JSONDecodeError:
                invalidas += 1
                continue
            if isinstance(obj, dict):
                yield obj
    if invalidas:
        logger.warning(f"{invalidas} linha(s) não-JSON ignoradas em {path.name}")


def analisar(linhas: Iterable[Dict[str, Any]], categorias: List[str]) -> Dict[str, Any]:
    """Resume o export bruto. Função pura: não toca rede nem banco.

    Args:
        linhas: registros do NDJSON (dicts).
        categorias: categorias-alvo, comparadas em maiúsculas e sem espaços
            nas pontas (ex.: ["AR CONDICIONADO"]).

    Returns:
        Dicionário serializável em JSON com colunas, exemplos, distribuições e
        ofertas por marketplace dentro das categorias-alvo.
    """
    alvo = {c.strip().upper() for c in categorias if c.strip()}
    colunas: Counter = Counter()
    nao_nulas: Counter = Counter()
    exemplos: List[Dict[str, Any]] = []
    marketplaces: Counter = Counter()
    categorias_todas: Counter = Counter()
    subcategorias_todas: Counter = Counter()
    status_todos: Counter = Counter()

    sub_alvo: Counter = Counter()
    familia_alvo: Counter = Counter()
    hora_alvo: Counter = Counter()
    status_alvo: Counter = Counter()
    com_print_alvo = 0
    texto_alvo: Counter = Counter()
    btu_valores: Counter = Counter()
    sem_sku_alvo = 0
    sem_seller_alvo = 0
    title_nulo_alvo = 0
    ofertas: Dict[str, Set[str]] = defaultdict(set)
    ofertas_disp: Dict[str, Set[str]] = defaultdict(set)
    total = 0
    total_alvo = 0

    for row in linhas:
        total += 1
        for chave, valor in row.items():
            colunas[chave] += 1
            if valor is not None and valor != "":
                nao_nulas[chave] += 1
        if len(exemplos) < 3:
            exemplos.append(row)

        mkt = _pick(row, "marketplace")
        cat = _pick(row, "category").upper()
        status = _pick(row, "status").upper()
        marketplaces[mkt] += 1
        categorias_todas[cat] += 1
        subcategorias_todas[f"{cat} › {_pick(row, 'subcategory').upper()}"] += 1
        status_todos[status] += 1

        if cat not in alvo:
            continue
        total_alvo += 1
        sub_alvo[_pick(row, "subcategory").upper()] += 1
        familia_alvo[_pick(row, "family")] += 1
        hora_alvo[_pick(row, "collection_hour")] += 1
        status_alvo[status] += 1
        if _pick(row, "screenshot_url"):
            com_print_alvo += 1
        if not _pick(row, "title"):
            title_nulo_alvo += 1

        texto = f"{_pick(row, 'title')} {_pick(row, 'product_name')}"
        for nome, padrao in _PADROES_TEXTO.items():
            achado = padrao.search(texto)
            if achado:
                texto_alvo[nome] += 1
                if nome == "btu":
                    btu_valores[re.sub(r"\D", "", achado.group(1))] += 1

        sku = _pick(row, "sku")
        seller = _pick(row, "seller")
        if not sku:
            sem_sku_alvo += 1
        if not seller:
            sem_seller_alvo += 1
        chave_oferta = f"{sku}|{seller}"
        ofertas[mkt].add(chave_oferta)
        if status == "AVAILABLE":
            ofertas_disp[mkt].add(chave_oferta)

    def _pct(n: int, d: int) -> float:
        return round(100.0 * n / d, 1) if d else 0.0

    return {
        "linhas_total": total,
        "categorias_alvo": sorted(alvo),
        "linhas_alvo": total_alvo,
        "colunas": [
            {"coluna": c, "presente_em": n, "nao_nula_pct": _pct(nao_nulas[c], total)}
            for c, n in sorted(colunas.items())
        ],
        "colunas_candidatas_caracteristica": sorted(
            c for c in colunas if _CHAVES_CARACTERISTICA.search(c)
        ),
        "exemplos": exemplos,
        "marketplaces": marketplaces.most_common(),
        "categorias": categorias_todas.most_common(),
        "subcategorias": subcategorias_todas.most_common(),
        "status": status_todos.most_common(),
        "alvo": {
            "subcategorias": sub_alvo.most_common(),
            "familias": familia_alvo.most_common(_TOP_N),
            "familias_distintas": len(familia_alvo),
            "horas_coleta": sorted(hora_alvo.items()),
            "status": status_alvo.most_common(),
            "com_print_pct": _pct(com_print_alvo, total_alvo),
            "title_nulo_pct": _pct(title_nulo_alvo, total_alvo),
            "sem_sku": sem_sku_alvo,
            "sem_seller": sem_seller_alvo,
            "caracteristica_no_texto_pct": {
                nome: _pct(texto_alvo[nome], total_alvo) for nome in _PADROES_TEXTO
            },
            "btu_no_texto": btu_valores.most_common(_TOP_N),
            "ofertas_por_marketplace": sorted(
                (
                    {"marketplace": m, "ofertas": len(s), "ofertas_available": len(ofertas_disp[m])}
                    for m, s in ofertas.items()
                ),
                key=lambda d: -d["ofertas"],
            ),
        },
    }


def render_markdown(r: Dict[str, Any], origem: str) -> str:
    """Relatório legível do resultado de :func:`analisar`."""
    a = r["alvo"]
    cats = ", ".join(r["categorias_alvo"])
    out: List[str] = [
        "# Descoberta do export PriceTrack",
        "",
        f"- Origem: `{origem}`",
        f"- Linhas no arquivo: **{r['linhas_total']:,}**",
        f"- Linhas em {cats}: **{r['linhas_alvo']:,}**",
        "",
        "## Colunas do NDJSON",
        "",
        "| coluna | linhas com a chave | não nula % |",
        "|---|---:|---:|",
    ]
    out += [f"| `{c['coluna']}` | {c['presente_em']:,} | {c['nao_nula_pct']} |" for c in r["colunas"]]
    cand = r["colunas_candidatas_caracteristica"]
    out += [
        "",
        "Colunas que parecem característica (BTU/voltagem/tecnologia/ciclo): "
        + (", ".join(f"`{c}`" for c in cand) if cand else "**nenhuma**"),
        "",
        "## 3 linhas de exemplo",
        "",
        "```json",
        *[json.dumps(e, ensure_ascii=False) for e in r["exemplos"]],
        "```",
        "",
        "## Marketplaces (todas as categorias)",
        "",
        "| marketplace | linhas |",
        "|---|---:|",
        *[f"| {m or '(vazio)'} | {n:,} |" for m, n in r["marketplaces"]],
        "",
        "## Categorias",
        "",
        "| categoria | linhas |",
        "|---|---:|",
        *[f"| {c or '(vazio)'} | {n:,} |" for c, n in r["categorias"]],
        "",
        "## Categoria › subcategoria",
        "",
        "| categoria › subcategoria | linhas |",
        "|---|---:|",
        *[f"| {c} | {n:,} |" for c, n in r["subcategorias"]],
        "",
        f"## Dentro de {cats}",
        "",
        f"- Com print (`screenshot_url` preenchido): {a['com_print_pct']}%",
        f"- `title` nulo: {a['title_nulo_pct']}% (no export V2 o esperado é 100%)",
        f"- Sem SKU: {a['sem_sku']:,} · sem seller: {a['sem_seller']:,}",
        f"- Status: {', '.join(f'{s or chr(8212)}={n:,}' for s, n in a['status'])}",
        f"- Famílias distintas: {a['familias_distintas']:,}",
        "",
        "### Subcategorias",
        "",
        "| subcategoria | linhas |",
        "|---|---:|",
        *[f"| {s or '(vazio)'} | {n:,} |" for s, n in a["subcategorias"]],
        "",
        "### Características encontradas no texto (título + nome do produto)",
        "",
        "| característica | % das linhas |",
        "|---|---:|",
        *[f"| {k} | {v} |" for k, v in a["caracteristica_no_texto_pct"].items()],
        "",
        "BTU lido no texto: " + ", ".join(f"{b}={n:,}" for b, n in a["btu_no_texto"]),
        "",
        "### Ofertas (SKU × seller distintos) por marketplace",
        "",
        "| marketplace | ofertas | só AVAILABLE |",
        "|---|---:|---:|",
        *[
            f"| {o['marketplace'] or '(vazio)'} | {o['ofertas']:,} | {o['ofertas_available']:,} |"
            for o in a["ofertas_por_marketplace"]
        ],
        "",
        "### Famílias mais frequentes",
        "",
        *[f"- {f or '(vazio)'}: {n:,}" for f, n in a["familias"]],
        "",
        "### Linhas por hora de coleta",
        "",
        ", ".join(f"{h or '—'}h={n:,}" for h, n in a["horas_coleta"]),
        "",
    ]
    return "\n".join(out)


# ── Rede: criar/reaproveitar export, polling e download ─────────────────────

def _versao_efetiva(versao: str, dia: date, hoje: Optional[date] = None) -> str:
    """'auto' → v2 se a data está na janela de 90 dias do Redis, senão v1."""
    if versao != "auto":
        return versao
    hoje = hoje or date.today()
    return "v2" if (hoje - dia).days < _V2_RETENCAO_DIAS else "v1"


def _baixar_export(dia: date, versao: str, destino: Path, timeout_min: int) -> Path:
    """Cria (ou reaproveita) um export de ofertas do dia e baixa o arquivo.

    Só usa endpoints de leitura de dados da API do PriceTrack; o único efeito
    externo é o próprio export, que ocupa 1 dos 3 slots da organização até
    terminar.
    """
    from pricetrack_api import ExportRequest, PriceTrackClient, PriceTrackSettings
    from pricetrack_api.exceptions import PriceTrackExportLimitError

    settings = PriceTrackSettings.from_env()
    client = PriceTrackClient(settings)
    path = EXPORT_V2_PATH if versao == "v2" else EXPORT_V1_PATH
    request = ExportRequest(dia.isoformat())

    job = None
    while job is None:
        try:
            job = client._create_export(path, request)  # noqa: SLF001 — reuso do cliente
        except PriceTrackExportLimitError:
            ativos = [j for j in client.list_exports() if j.is_active]
            logger.warning(
                f"3 exports já em andamento na organização ({len(ativos)} ativos: "
                f"{', '.join(j.export_id[:8] for j in ativos)}). Esperando 60s."
            )
            time.sleep(60)

    logger.info(f"Export {versao.upper()} {job.export_id} criado para {dia}. Aguardando…")
    inicio = time.monotonic()
    espera = 5.0
    while not job.is_terminal:
        if time.monotonic() - inicio > timeout_min * 60:
            raise TimeoutError(
                f"Export {job.export_id} não terminou em {timeout_min} min "
                f"(último status {job.status}). Ele continua rodando na API; "
                f"rode de novo mais tarde com --export-id {job.export_id}."
            )
        time.sleep(espera)
        espera = min(espera * 1.5, 60.0)
        job = client.get_export(job.export_id)
        logger.info(f"  status={job.status} progresso={job.progress}")

    return _baixar_job(client, job, destino)


def _baixar_job(client: Any, job: Any, destino: Path) -> Path:
    if job.status != "DONE":
        raise RuntimeError(f"Export {job.export_id} terminou em {job.status}: {job.raw}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    client.download_export(job, destino)
    logger.info(f"Linhas segundo a API: {job.row_count} · arquivo: {destino}")
    return destino


def _baixar_por_id(export_id: str, destino: Path) -> Path:
    from pricetrack_api import PriceTrackClient, PriceTrackSettings

    client = PriceTrackClient(PriceTrackSettings.from_env())
    return _baixar_job(client, client.get_export(export_id), destino)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", help="Data da coleta (YYYY-MM-DD). Padrão: ontem.")
    parser.add_argument("--versao", choices=("v1", "v2", "auto"), default="v2",
                        help="Endpoint do export (padrão v2; auto = v2 nos últimos 90 dias).")
    parser.add_argument("--categorias", default="AR CONDICIONADO",
                        help="Categorias-alvo separadas por vírgula.")
    parser.add_argument("--arquivo", type=Path,
                        help="Analisa um .ndjson.gz já baixado, sem chamar a API.")
    parser.add_argument("--export-id", help="Baixa um export já existente pelo id.")
    parser.add_argument("--timeout-min", type=int, default=90,
                        help="Tempo máximo de espera do export (padrão 90).")
    args = parser.parse_args(argv)

    dia = date.fromisoformat(args.data) if args.data else date.today() - timedelta(days=1)
    versao = _versao_efetiva(args.versao, dia)
    categorias = [c for c in args.categorias.split(",") if c.strip()]

    if args.arquivo:
        arquivo = args.arquivo
        rotulo = "arquivo"
    else:
        arquivo = _OUT_DIR / f"offers-{dia.isoformat()}-{versao}.ndjson.gz"
        rotulo = versao
        if args.export_id:
            _baixar_por_id(args.export_id, arquivo)
        elif arquivo.exists():
            logger.info(f"Reaproveitando download anterior: {arquivo}")
        else:
            _baixar_export(dia, versao, arquivo, args.timeout_min)

    logger.info(f"Lendo {arquivo} em streaming…")
    resultado = analisar(iter_ndjson_gz(arquivo), categorias)

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    base = _LOG_DIR / f"pricetrack_discovery_{dia.isoformat()}_{rotulo}"
    base.with_suffix(".json").write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    base.with_suffix(".md").write_text(render_markdown(resultado, str(arquivo)), encoding="utf-8")
    logger.success(f"Relatório: {base.with_suffix('.md')} (+ .json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
