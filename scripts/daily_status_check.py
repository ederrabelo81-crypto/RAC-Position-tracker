"""
scripts/daily_status_check.py — Validação diária do status de cada plataforma.

Consulta o Supabase pelos registros do dia (ou turno específico) e gera um
relatório PASS/WARN/FAIL por plataforma comparado aos thresholds mínimos.
Verifica também o **histórico frio** (Parquet no Drive): se o dia não chegou
lá — ou chegou e não volta a sair — o relatório sai vermelho. Esse check roda
antes do Supabase e não depende dele: a redundância precisa ser verificável
justamente quando o banco está fora.
Também monitora o % de preenchimento dos campos de insight (buy_box_seller,
tipo_seller, qtd_sellers, reputacao_seller, avaliacao) por plataforma e emite
WARN quando a cobertura de um campo cai >50% vs a média dos últimos 7 dias —
pega regressão silenciosa de scraper antes de virar buraco no dashboard.
Envia o resumo via Telegram (N8N webhook ou Bot API direto).

Uso:
    # Status do dia atual (ambos os turnos)
    python scripts/daily_status_check.py

    # Status de um turno específico
    python scripts/daily_status_check.py --turno Abertura
    python scripts/daily_status_check.py --turno Fechamento

    # Sem envio de notificação (só imprime no terminal)
    python scripts/daily_status_check.py --no-notify

    # Dia retroativo
    python scripts/daily_status_check.py --data 2026-05-14

Exit code:
    0 — todas as plataformas críticas PASS e o dia no histórico frio
    1 — o DADO não chegou: plataforma crítica WARN/FAIL, ou histórico frio FAIL
    2 — Supabase não CONFIGURADO (SUPABASE_URL/KEY ausentes, pacote faltando)
    3 — Supabase rejeitou a CREDENCIAL (401/403, chave inválida ou revogada)
    4 — Supabase inacessível (rede, timeout, 5xx, projeto restrito por cota)

    Os códigos 2–4 são erros de *ferramenta*, não de coleta: dizem que o
    watchdog não conseguiu olhar, e não que a coleta falhou. Antes todos eles
    caíam no mesmo exit 2 do caso "não veio dado", e a causa real (ex: nove
    dias seguidos de 401 "Unregistered API key") ficava só no meio do log. Em
    qualquer um deles o alerta do Telegram sai, e o resultado do HISTÓRICO
    FRIO — que não depende do banco — continua sendo exibido.
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from config import ACTIVE_PLATFORMS
from utils.supabase_client import _get_client
from utils.text import now_brt


# ---------------------------------------------------------------------------
# Exit codes — "não consegui olhar" ≠ "olhei e não tinha dado"
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_DADO_AUSENTE = 1
EXIT_SUPABASE_CONFIG = 2
EXIT_SUPABASE_AUTH = 3
EXIT_SUPABASE_CONEXAO = 4


class SupabaseUnavailable(RuntimeError):
    """Falha ao falar com o Supabase, já classificada por natureza.

    Herda de ``RuntimeError`` de propósito: os checks best-effort (cobertura de
    insight, import PriceTrack) capturam ``RuntimeError`` e continuam
    degradando com elegância, sem precisar conhecer esta classe.

    Attributes:
        kind:   "config" | "auth" | "conexao" — decide exit code e mensagem.
        remedy: o que fazer a respeito, em uma linha.
    """

    def __init__(self, kind: str, message: str, remedy: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.remedy = remedy

    @property
    def exit_code(self) -> int:
        """Exit code correspondente à natureza da falha."""
        return _SUPABASE_FAILURE_KINDS.get(
            self.kind, _SUPABASE_FAILURE_KINDS["conexao"]
        )[0]

    @property
    def titulo(self) -> str:
        """Título curto para o terminal e para o alerta do Telegram."""
        return _SUPABASE_FAILURE_KINDS.get(
            self.kind, _SUPABASE_FAILURE_KINDS["conexao"]
        )[1]


#: kind → (exit code, título do alerta)
_SUPABASE_FAILURE_KINDS: Dict[str, Tuple[int, str]] = {
    "config":  (EXIT_SUPABASE_CONFIG,  "Supabase não configurado"),
    "auth":    (EXIT_SUPABASE_AUTH,    "Supabase rejeitou a credencial"),
    "conexao": (EXIT_SUPABASE_CONEXAO, "Supabase inacessível"),
}

# Marcadores de credencial rejeitada. O caso que motivou a separação foi o
# `401 Unregistered API key` — nove runs seguidos do watchdog em vermelho, todos
# reportando o mesmo exit 2 genérico de "erro de configuração".
_AUTH_MARKERS: Tuple[str, ...] = (
    "unregistered api key",
    "invalid api key",
    "no api key found",
    "invalid authentication",
    "jwt expired",
    "invalid jwt",
    "jwserror",
    "invalid claim",
    "permission denied",
    "401",
    "403",
)

# Marcadores de indisponibilidade (rede, servidor, cota) — nada a ver com a
# chave, e a remediação é outra.
_CONN_MARKERS: Tuple[str, ...] = (
    "timeout", "timed out", "connection", "getaddrinfo", "name or service",
    "temporary failure in name resolution", "max retries", "ssl",
    "502", "503", "504", "exceed_db_size_quota", "402",
)


def _classify_supabase_error(exc: Exception) -> SupabaseUnavailable:
    """Classifica uma exceção do Supabase como credencial × indisponibilidade.

    Args:
        exc: exceção capturada de uma chamada ao PostgREST.

    Returns:
        SupabaseUnavailable com ``kind`` preenchido. Erro não reconhecido cai
        em "conexao": é o palpite menos perigoso — sugere olhar o serviço em vez
        de mandar alguém trocar uma chave que talvez esteja correta.
    """
    texto = str(exc).lower()
    if any(marker in texto for marker in _AUTH_MARKERS):
        return SupabaseUnavailable(
            "auth",
            f"Supabase recusou a credencial: {exc}",
            "Gere uma nova chave service_role e atualize o secret "
            "SUPABASE_KEY (Settings → Secrets and variables → Actions) "
            "e o .env das máquinas coletoras.",
        )
    if any(marker in texto for marker in _CONN_MARKERS):
        return SupabaseUnavailable(
            "conexao",
            f"Supabase não respondeu: {exc}",
            "Verifique o status do projeto no painel do Supabase "
            "(pausado por inatividade? restrito por cota?).",
        )
    return SupabaseUnavailable(
        "conexao",
        f"Erro ao consultar o Supabase: {exc}",
        "Veja o traceback completo no log do run.",
    )


def _erro_supabase_nao_configurado() -> SupabaseUnavailable:
    """Falha padrão de quando `_get_client()` devolve None."""
    return SupabaseUnavailable(
        "config",
        "Supabase indisponível — SUPABASE_URL/SUPABASE_KEY ausentes ou "
        "pacote `supabase` não instalado",
        "Cadastre SUPABASE_URL e SUPABASE_KEY no .env (ou nos secrets do "
        "repositório) e confirme `pip install supabase`.",
    )


def _load_dealer_configs() -> Dict[str, Dict]:
    """Importa DEALER_CONFIGS sem triggar imports pesados (playwright, etc).

    scrapers/__init__.py importa eagerly todos os scrapers, e cada um traz
    Playwright via BaseScraper. Pulamos isso lendo o módulo direto pelo path.
    """
    import importlib.util
    dealers_path = Path(__file__).resolve().parent.parent / "scrapers" / "dealers.py"
    spec = importlib.util.spec_from_file_location("_dealers_isolated", dealers_path)
    if spec is None or spec.loader is None:
        return {}
    # NOTE: dealers.py também importa BaseScraper. Pra evitar isso, parseamos
    # manualmente o módulo procurando só o DEALER_CONFIGS dict.
    try:
        import ast
        tree = ast.parse(dealers_path.read_text(encoding="utf-8"))
        for node in tree.body:
            # `DEALER_CONFIGS: Dict[...] = {...}` é AnnAssign;
            # `DEALER_CONFIGS = {...}` é Assign.
            if isinstance(node, ast.AnnAssign):
                if (
                    isinstance(node.target, ast.Name)
                    and node.target.id == "DEALER_CONFIGS"
                    and node.value is not None
                ):
                    return ast.literal_eval(node.value)
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "DEALER_CONFIGS":
                        return ast.literal_eval(node.value)
    except Exception as exc:
        logger.warning(f"Falha ao extrair DEALER_CONFIGS via AST: {exc}")
    return {}


DEALER_CONFIGS = _load_dealer_configs()


# ---------------------------------------------------------------------------
# Thresholds — mínimo esperado de registros por plataforma+turno
# ---------------------------------------------------------------------------
# Valores conservadores baseados nos dias de coleta saudável (Mai/2026):
#   Abertura (2 páginas, prioridade alta+media): mais registros
#   Fechamento (1 página, prioridade alta):        menos registros
# Plataformas críticas (`critical=True`) entram no exit code 1 se falharem.

PLATFORM_NAME_MAP: Dict[str, str] = {
    "ml":              "Mercado Livre",
    "amazon":          "Amazon",
    "magalu":          "Magalu",
    "google_shopping": "Google Shopping",
    "leroy":           "Leroy Merlin",
    "casasbahia":      "Casas Bahia",
    "shopee":          "Shopee",
}

# Thresholds: (min_abertura, min_fechamento, critical)
PLATFORM_THRESHOLDS: Dict[str, Tuple[int, int, bool]] = {
    "Mercado Livre":    (800, 500,  True),
    "Amazon":           (800, 400,  True),
    "Magalu":           (400, 300,  True),
    "Google Shopping":  (200, 200,  False),  # roda só Fechamento normalmente
    "Leroy Merlin":     (200, 200,  True),
    "Casas Bahia":      (100, 50,   False),  # VTEX API + warm-up Akamai
    "Shopee":           (50,  30,   False),  # best-effort sem proxy — não-crítica
}

# Dealers: cada um tem uma keyword (o nome do site) e poucos itens por turno.
# Threshold uniforme baixo — apenas valida que coletou algo.
DEALER_THRESHOLD: Tuple[int, int, bool] = (3, 3, False)


# ---------------------------------------------------------------------------
# Canal de coleta — de qual máquina cada plataforma vem
# ---------------------------------------------------------------------------
# Sem isso o watchdog não distinguia "o scraper do ML quebrou" de "o PC que
# roda o ML estava desligado". Todo fim de semana o PC fica fora e o relatório
# saía com 4 FAIL críticos, exit 1 e run vermelho — 16 execuções seguidas
# vermelhas até 10/08/2026, quando o alerta deixou de ser lido justamente por
# ser sempre igual.
#
# A regra: se TODAS as plataformas de um canal vieram zeradas no turno, o
# diagnóstico é "canal offline" (um alerta, não N), e ele não conta como falha
# crítica de plataforma. Se ao menos uma coletou, o canal estava de pé — e aí
# quem veio zerada quebrou de verdade e continua crítica.
CHANNEL_ACTIONS = "GitHub Actions"
CHANNEL_LOCAL = "PC local (IP residencial)"

PLATFORM_CHANNEL: Dict[str, str] = {
    "Amazon":           CHANNEL_ACTIONS,
    "Leroy Merlin":     CHANNEL_ACTIONS,
    "Google Shopping":  CHANNEL_ACTIONS,
    "Mercado Livre":    CHANNEL_LOCAL,
    "Magalu":           CHANNEL_LOCAL,
    "Shopee":           CHANNEL_LOCAL,
    "Casas Bahia":      CHANNEL_LOCAL,
}


#: Hora BRT em que a coleta de cada turno já deveria ter terminado e subido.
#: A coleta roda 10:00 (Abertura) e 21:00 (Fechamento); a margem cobre a
#: duração da coleta e o upload.
_TURNO_DEADLINE_BRT = {"Abertura": 12, "Fechamento": 23}


def _turno_window_closed(
    turno: str, data_str: str, agora: Optional[datetime] = None
) -> bool:
    """
    True se a janela de coleta do turno já fechou.

    O watchdog agendado roda 20:30 BRT e avalia OS DOIS turnos — ou seja,
    avalia "Fechamento" antes das 21:00, quando é normal e esperado que não
    exista nenhum registro ainda. Sem esta checagem, um turno que ainda nem
    começou seria lido como apagão total e o alerta voltaria a ser vermelho
    todo santo dia, que é justamente o problema que este watchdog está
    tentando resolver.

    Args:
        turno: "Abertura" ou "Fechamento".
        data_str: data avaliada (YYYY-MM-DD).
        agora: instante de referência; default ``now_brt()`` (injetável em teste).

    Returns:
        True se a coleta daquele turno já deveria ter acontecido.
    """
    agora = agora or now_brt()
    try:
        dia = datetime.strptime(data_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return True  # data ilegível: não é motivo para suprimir alerta

    if dia < agora.date():
        return True   # dia passado: a janela fechou de qualquer forma
    if dia > agora.date():
        return False  # dia futuro: nada era esperado ainda

    return agora.hour >= _TURNO_DEADLINE_BRT.get(turno, 0)


def _offline_channels(
    counts: Dict[Tuple[str, str], int],
    turno: str,
    expected: List[str],
    janela_fechada: bool = True,
) -> set:
    """
    Identifica canais sem NENHUM registro no turno — máquina offline.

    Args:
        counts: mapa (plataforma, turno) → nº de registros.
        turno: turno avaliado ("Abertura" ou "Fechamento").
        expected: plataformas ativas hoje.

    Returns:
        Set de nomes de canal que não produziram nada. Um canal só entra aqui
        se TODAS as suas plataformas esperadas vieram zeradas — uma única
        plataforma com dado prova que a máquina rodou.
    """
    por_canal: Dict[str, List[int]] = {}
    for platform in expected:
        canal = PLATFORM_CHANNEL.get(platform)
        if canal is None:
            continue
        por_canal.setdefault(canal, []).append(counts.get((platform, turno), 0))

    offline = {
        canal for canal, valores in por_canal.items()
        if valores and not any(valores)
    }

    # Salvaguarda: se TODOS os canais vieram zerados, não houve "máquina
    # desligada" — houve apagão total de coleta ou de upload, que é justamente
    # o caso mais grave. Suprimir aqui faria o alerta silenciar exatamente
    # quando mais precisa gritar, então nenhum canal é considerado offline e
    # todas as falhas críticas voltam a contar para o exit code.
    if offline and len(offline) == len(por_canal):
        if not janela_fechada:
            # O turno ainda nem rodou (o watchdog agendado às 20:30 avalia o
            # Fechamento das 21:00). Zero aqui é o estado correto, não apagão.
            logger.info(
                f"[Watchdog] Turno '{turno}' ainda dentro da janela de coleta — "
                "zero registros é esperado, sem escalar para crítico."
            )
            return offline
        logger.error(
            "[Watchdog] TODOS os canais zerados — apagão total de coleta/upload, "
            "não é máquina desligada. Falhas críticas mantidas."
        )
        return set()

    return offline


# ---------------------------------------------------------------------------
# Coleta dos dados
# ---------------------------------------------------------------------------

def _expected_platforms() -> List[str]:
    """Lista nomes de plataformas esperadas hoje (ativas no config)."""
    expected: List[str] = []
    for key, active in ACTIVE_PLATFORMS.items():
        if not active:
            continue
        if key == "dealers":
            # dealers ativos = todos não-on_hold
            expected.extend(
                name for name, cfg in DEALER_CONFIGS.items()
                if not cfg.get("on_hold")
            )
        elif key in PLATFORM_NAME_MAP:
            expected.append(PLATFORM_NAME_MAP[key])
    return expected


def _fetch_counts(
    data_str: str, turno: Optional[str]
) -> Dict[Tuple[str, str], int]:
    """
    Busca contagens de registros no Supabase.

    Returns:
        Dict[(plataforma, turno), count]

    Raises:
        SupabaseUnavailable: já classificada em config/auth/conexao — é ela que
            define o exit code do script.
    """
    client = _get_client()
    if client is None:
        raise _erro_supabase_nao_configurado()

    query = client.table("coletas").select(
        "plataforma, turno"
    ).eq("data", data_str)

    if turno:
        query = query.eq("turno", turno)

    try:
        # Paginação manual: PostgREST limita a 1000 rows por default.
        # Aqui não importa o limite porque agregamos em Python depois.
        all_rows: List[Dict] = []
        page_size = 1000
        offset = 0
        while True:
            resp = query.range(offset, offset + page_size - 1).execute()
            rows = resp.data or []
            all_rows.extend(rows)
            if len(rows) < page_size:
                break
            offset += page_size
    except Exception as exc:
        raise _classify_supabase_error(exc)

    counts: Dict[Tuple[str, str], int] = {}
    for row in all_rows:
        key = (row.get("plataforma") or "?", row.get("turno") or "?")
        counts[key] = counts.get(key, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Cobertura dos campos de insight (buy box / seller / avaliação)
# ---------------------------------------------------------------------------
# Além da contagem de linhas, monitoramos o % de preenchimento dos campos de
# insight por plataforma. Uma queda brusca com volume normal indica regressão
# silenciosa de scraper (ex: fallback DOM que para de preencher tipo_seller).

_INSIGHT_FIELDS: Tuple[str, ...] = (
    "buy_box_seller",
    "tipo_seller",
    "qtd_sellers",
    "reputacao_seller",
    "avaliacao",
)

# Rótulos curtos para o resumo compacto (terminal + Telegram)
_INSIGHT_LABELS: Dict[str, str] = {
    "buy_box_seller":   "buybox",
    "tipo_seller":      "tipo",
    "qtd_sellers":      "qtd",
    "reputacao_seller": "reput",
    "avaliacao":        "aval",
}

# Dias de histórico usados como baseline de cobertura
_COVERAGE_BASELINE_DAYS = 7
# WARN quando a cobertura de hoje cai para menos da metade da média 7d
_COVERAGE_DROP_RATIO = 0.5
# Baseline abaixo disso = plataforma nunca preencheu o campo → sem alerta
_COVERAGE_MIN_BASELINE_PCT = 5.0


def _fetch_insight_rows(data_str: str, turno: Optional[str]) -> List[Dict]:
    """Busca linhas (data, plataforma, campos de insight) do dia + baseline.

    Uma única query paginada cobre [data − 7 dias, data]; a separação
    hoje × baseline é feita em Python.

    Args:
        data_str: data alvo no formato YYYY-MM-DD.
        turno:    filtro opcional de turno (Abertura/Fechamento).

    Returns:
        Lista de dicts com data, plataforma e os campos de _INSIGHT_FIELDS.

    Raises:
        SupabaseUnavailable: Supabase indisponível, colunas de insight ausentes
            (banco não migrado) ou falha de consulta. É subclasse de
            RuntimeError — o chamador trata como best-effort.
    """
    client = _get_client()
    if client is None:
        raise _erro_supabase_nao_configurado()

    target = datetime.strptime(data_str, "%Y-%m-%d").date()
    since = (target - timedelta(days=_COVERAGE_BASELINE_DAYS)).isoformat()

    cols = "data, plataforma, " + ", ".join(_INSIGHT_FIELDS)
    query = (
        client.table("coletas")
        .select(cols)
        .gte("data", since)
        .lte("data", data_str)
    )
    if turno:
        query = query.eq("turno", turno)

    try:
        all_rows: List[Dict] = []
        page_size = 1000
        offset = 0
        while True:
            resp = query.range(offset, offset + page_size - 1).execute()
            rows = resp.data or []
            all_rows.extend(rows)
            if len(rows) < page_size:
                break
            offset += page_size
    except Exception as exc:
        raise _classify_supabase_error(exc)
    return all_rows


def _coverage_tables(
    rows: List[Dict], data_str: str
) -> Tuple[
    Dict[str, Dict[str, float]],
    Dict[str, Dict[str, float]],
    Dict[str, int],
]:
    """Calcula % de preenchimento por plataforma × campo (hoje e baseline).

    Args:
        rows:     linhas vindas de _fetch_insight_rows.
        data_str: data alvo (separa hoje × baseline).

    Returns:
        Tupla (today, baseline, today_rows):
        - today:      plataforma → campo → % preenchido no dia alvo
        - baseline:   plataforma → campo → média das coberturas diárias dos
                      dias anteriores (dias sem coleta da plataforma não contam)
        - today_rows: plataforma → nº de linhas no dia alvo
    """
    daily: Dict[Tuple[str, str], Dict[str, int]] = {}
    for row in rows:
        plat = row.get("plataforma") or "?"
        day = str(row.get("data") or "?")
        bucket = daily.setdefault(
            (plat, day), {field: 0 for field in _INSIGHT_FIELDS} | {"n": 0}
        )
        bucket["n"] += 1
        for field in _INSIGHT_FIELDS:
            value = row.get(field)
            if value is not None and value != "":
                bucket[field] += 1

    today: Dict[str, Dict[str, float]] = {}
    today_rows: Dict[str, int] = {}
    base_acc: Dict[str, Dict[str, List[float]]] = {}
    for (plat, day), bucket in daily.items():
        n = bucket["n"]
        if n == 0:
            continue
        pcts = {field: bucket[field] / n * 100 for field in _INSIGHT_FIELDS}
        if day == data_str:
            today[plat] = pcts
            today_rows[plat] = n
        else:
            acc = base_acc.setdefault(plat, {field: [] for field in _INSIGHT_FIELDS})
            for field in _INSIGHT_FIELDS:
                acc[field].append(pcts[field])

    baseline = {
        plat: {
            field: (sum(vals) / len(vals) if vals else 0.0)
            for field, vals in fields.items()
        }
        for plat, fields in base_acc.items()
    }
    return today, baseline, today_rows


def _evaluate_coverage(
    today: Dict[str, Dict[str, float]],
    baseline: Dict[str, Dict[str, float]],
) -> List[Dict]:
    """Detecta queda de cobertura >50% vs a média dos últimos 7 dias.

    Plataformas sem histórico ou cujo baseline do campo é ~0% (nunca
    preencheram) não geram alerta — só regressão real interessa.

    Returns:
        Lista de dicts {platform, field, today_pct, base_pct} ordenada por
        plataforma.
    """
    warnings: List[Dict] = []
    for plat, fields in sorted(today.items()):
        base_fields = baseline.get(plat)
        if not base_fields:
            continue
        for field in _INSIGHT_FIELDS:
            base_pct = base_fields.get(field, 0.0)
            if base_pct < _COVERAGE_MIN_BASELINE_PCT:
                continue
            today_pct = fields.get(field, 0.0)
            if today_pct < base_pct * _COVERAGE_DROP_RATIO:
                warnings.append({
                    "platform":  plat,
                    "field":     field,
                    "today_pct": today_pct,
                    "base_pct":  base_pct,
                })
    return warnings


def _coverage_overall_line(
    today: Dict[str, Dict[str, float]], today_rows: Dict[str, int]
) -> str:
    """Linha compacta: cobertura média do dia por campo (ponderada por linhas)."""
    total = sum(today_rows.values())
    if not total:
        return ""
    parts: List[str] = []
    for field in _INSIGHT_FIELDS:
        filled = sum(
            today[plat][field] / 100 * today_rows[plat] for plat in today
        )
        parts.append(f"{_INSIGHT_LABELS[field]} {filled / total * 100:.0f}%")
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# PriceTrack — gap do import D-1 (docs/PRICETRACK_INSIGHTS.md §3 item 5)
# ---------------------------------------------------------------------------
# O import diário (06:00 BRT) traz os dados da VÉSPERA (D-1). Antes deste
# check, uma falha só era percebida como buraco de preço no dashboard — o
# auto-heal --gaps-only tentava de novo no dia seguinte sem avisar ninguém.

def _check_pricetrack_import(data_str: str) -> Dict:
    """Valida se o import D-1 do PriceTrack aconteceu e como terminou.

    Verdade primária: linhas em `pricetrack_daily` com collection_date = D-1.
    Diagnóstico: última entrada de `pricetrack_import_log` para `api-{D-1}`
    (o job grava SUCCESS/PARTIAL; em crash não grava nada — por isso a
    ausência de linhas E de log também é FAIL).

    Args:
        data_str: data alvo do relatório (YYYY-MM-DD); D-1 = véspera dela.

    Returns:
        Dict {status, d1, detail} com status ∈ {"PASS", "WARN", "FAIL"}.

    Raises:
        SupabaseUnavailable: Supabase indisponível ou erro de consulta
            (subclasse de RuntimeError — tratado como best-effort).
    """
    client = _get_client()
    if client is None:
        raise _erro_supabase_nao_configurado()

    target = datetime.strptime(data_str, "%Y-%m-%d").date()
    d1 = (target - timedelta(days=1)).isoformat()

    try:
        resp = (
            client.table("pricetrack_daily")
            .select("id", count="exact")
            .eq("collection_date", d1)
            .limit(1)
            .execute()
        )
        rows_d1 = resp.count or 0

        log_resp = (
            client.table("pricetrack_import_log")
            .select("status, rows_inserted, rows_rejected, import_finished")
            .eq("source_file", f"api-{d1}")
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
        log = (log_resp.data or [None])[0]
    except Exception as exc:
        raise _classify_supabase_error(exc)

    if rows_d1 > 0:
        detail = f"{rows_d1:,} linhas D-1 ({d1})".replace(",", ".")
        if log and log.get("status") != "SUCCESS":
            return {
                "status": "WARN", "d1": d1,
                "detail": f"{detail} — último import {log.get('status')}",
            }
        return {"status": "PASS", "d1": d1, "detail": detail}

    if log is None:
        return {
            "status": "FAIL", "d1": d1,
            "detail": f"sem dados nem log de import para {d1} "
                      "(job das 06:00 BRT não rodou?)",
        }
    return {
        "status": "FAIL", "d1": d1,
        "detail": f"import {log.get('status')} para {d1} "
                  f"({log.get('rows_inserted') or 0} inseridas) — "
                  "rode: python scripts/pricetrack_api_import.py --gaps-only",
    }


def _pricetrack_telegram_lines(pt_check: Dict) -> List[str]:
    """Bloco compacto do status do import PriceTrack para o Telegram."""
    import html as _html
    icon = _STATUS_ICON.get(pt_check["status"], "?")
    return [
        "<b>🗃 PriceTrack (D-1)</b>",
        f"  {icon} {_html.escape(pt_check['detail'])}",
        "",
    ]


# ---------------------------------------------------------------------------
# Histórico frio — o dia chegou ao Drive?
# ---------------------------------------------------------------------------
# Por que este check existe
# -------------------------
# O histórico no Drive foi montado em 26/07/2026 como redundância do Supabase e
# **nunca escreveu um único arquivo**: a coleta morria de UnboundLocalError na
# linha anterior à gravação. A pasta `coletas/` ficou lá, criada e vazia, por
# cinco dias, enquanto 12 coletas terminavam vermelhas sem que nada olhasse.
#
# O `daily_status_check` não pegava porque só sabia perguntar ao Supabase — e
# um backup que ninguém verifica não é backup, é intenção. Aqui a pergunta é a
# única que importa: **o dia de hoje está legível no destino frio?** Legível,
# não "existe": a leitura baixa e abre o Parquet, então corrupção e credencial
# revogada aparecem como FAIL em vez de virarem surpresa no dia do resgate.

#: Abaixo disto a partição existe mas está anêmica demais para ser o dia todo.
_HISTORY_MIN_ROWS = 100


def _check_history(data_str: str) -> Dict:
    """Valida que a coleta do dia chegou — e volta a sair — do histórico frio.

    Args:
        data_str: Dia a verificar (``YYYY-MM-DD``).

    Returns:
        Dict ``{status, backend, partitions, rows, detail}`` com status ∈
        {"PASS", "WARN", "FAIL"}.

    Note:
        Deliberadamente **não** consulta o Supabase. Este é o check da
        redundância: ele precisa continuar respondendo justamente nos dias em
        que o banco está fora — que são os dias em que o frio é tudo o que há.
    """
    from utils.history import (  # import tardio: pyarrow é pesado
        DATASET_COLETAS,
        GoogleDriveBackend,
        HistoryBackendError,
        HistoryStoreError,
        get_store,
        resolve_backend_name,
    )

    day = datetime.strptime(data_str, "%Y-%m-%d").date()
    esperado = resolve_backend_name()
    store = get_store()
    destino = store.backend.describe
    no_drive = isinstance(store.backend, GoogleDriveBackend)

    # `get_store()` cai para o disco quando o Drive não inicializa — de propósito,
    # para não perder o dia. Só que em silêncio isso vira exatamente o buraco que
    # este check existe para fechar: tudo "verde" com o backup em disco efêmero.
    if esperado == "drive" and not no_drive:
        return {
            "status": "FAIL", "backend": destino, "partitions": 0, "rows": 0,
            "detail": "Drive configurado mas indisponível — histórico caindo em "
                      f"{destino}. Verifique GDRIVE_* (docs/HISTORICO_DRIVE.md).",
        }

    try:
        keys = store.keys_in_range(DATASET_COLETAS, day, day)
    except (HistoryBackendError, HistoryStoreError) as exc:
        return {
            "status": "FAIL", "backend": destino, "partitions": 0, "rows": 0,
            "detail": f"falha ao listar o histórico em {destino}: {exc}",
        }

    if not keys:
        return {
            "status": "FAIL", "backend": destino, "partitions": 0, "rows": 0,
            "detail": f"nenhuma partição de {data_str} em {destino} — a coleta "
                      "não gravou o histórico (só o CSV, se tanto).",
        }

    try:
        df = store.read(DATASET_COLETAS, start=day, end=day, columns=["plataforma"])
    except (HistoryBackendError, HistoryStoreError) as exc:
        return {
            "status": "FAIL", "backend": destino, "partitions": len(keys), "rows": 0,
            "detail": f"{len(keys)} partição(ões) gravada(s) mas ilegível(is) "
                      f"em {destino}: {exc}",
        }

    ilegiveis = list(store.last_read_errors)
    linhas = len(df)

    if linhas == 0:
        return {
            "status": "FAIL", "backend": destino, "partitions": len(keys), "rows": 0,
            "detail": f"{len(keys)} partição(ões) em {destino}, 0 linhas legíveis "
                      f"({len(ilegiveis)} ilegível(is)).",
        }

    detalhe = f"{linhas:,} linhas em {len(keys)} partição(ões) → {destino}".replace(",", ".")

    if ilegiveis:
        return {
            "status": "WARN", "backend": destino, "partitions": len(keys),
            "rows": linhas,
            "detail": f"{detalhe} — {len(ilegiveis)} partição(ões) ilegível(is): "
                      + ", ".join(k for k, _ in ilegiveis[:3]),
        }
    if linhas < _HISTORY_MIN_ROWS:
        return {
            "status": "WARN", "backend": destino, "partitions": len(keys),
            "rows": linhas,
            "detail": f"{detalhe} — abaixo do mínimo de {_HISTORY_MIN_ROWS}.",
        }
    if not no_drive:
        # Backend local pedido explicitamente: grava, lê, mas some com a máquina.
        return {
            "status": "WARN", "backend": destino, "partitions": len(keys),
            "rows": linhas,
            "detail": f"{detalhe} — em disco, sem cópia fora da máquina.",
        }
    return {
        "status": "PASS", "backend": destino, "partitions": len(keys),
        "rows": linhas, "detail": detalhe,
    }


def _history_telegram_lines(hist: Dict) -> List[str]:
    """Bloco do status do histórico frio para o Telegram."""
    import html as _html
    icon = _STATUS_ICON.get(hist["status"], "?")
    return [
        "<b>🧊 Histórico frio (redundância)</b>",
        f"  {icon} {_html.escape(hist['detail'])}",
        "",
    ]


# ---------------------------------------------------------------------------
# Avaliação PASS/WARN/FAIL
# ---------------------------------------------------------------------------

def _evaluate(
    platform: str, turno: str, count: int
) -> Tuple[str, str, bool]:
    """
    Retorna (status, descrição_curta, é_crítica).
    status ∈ {"PASS", "WARN", "FAIL"}.
    """
    if platform in PLATFORM_THRESHOLDS:
        min_ab, min_fe, critical = PLATFORM_THRESHOLDS[platform]
    elif platform in DEALER_CONFIGS:
        min_ab, min_fe, critical = DEALER_THRESHOLD
    else:
        return "INFO", "fora do registry", False

    threshold = min_ab if turno == "Abertura" else min_fe

    if count == 0:
        return "FAIL", f"0 registros (esperado ≥{threshold})", critical
    if count < threshold:
        return "WARN", f"{count} <{threshold}", critical
    return "PASS", f"{count} ≥{threshold}", critical


def _build_report(
    data_str: str,
    turno_filter: Optional[str],
    counts: Dict[Tuple[str, str], int],
) -> Tuple[List[Dict], Dict[str, int]]:
    """
    Monta tabela de status por (plataforma, turno).

    Returns:
        (rows, summary) onde rows é lista de dicts com platform/turno/status/desc,
        e summary é dict com totais {pass, warn, fail, critical_fail}.
    """
    expected = _expected_platforms()
    turnos = [turno_filter] if turno_filter else ["Abertura", "Fechamento"]

    rows: List[Dict] = []
    summary = {
        "pass": 0, "warn": 0, "fail": 0,
        "critical_fail": 0, "offline_channels": 0,
    }
    offline_vistos: set = set()

    for turno in turnos:
        offline = _offline_channels(
            counts, turno, expected,
            janela_fechada=_turno_window_closed(turno, data_str),
        )
        offline_vistos |= offline

        for platform in expected:
            count = counts.get((platform, turno), 0)
            status, desc, critical = _evaluate(platform, turno, count)

            # Canal offline: a plataforma não falhou, a máquina não rodou.
            # Rebaixa para WARN e tira do exit code, para o alerta crítico
            # voltar a significar "algo quebrou e precisa de conserto".
            canal = PLATFORM_CHANNEL.get(platform)
            canal_offline = None
            if status == "FAIL" and canal in offline:
                status = "WARN"
                desc = f"canal offline — {canal}"
                critical = False
                canal_offline = canal

            rows.append({
                "platform": platform,
                "turno":    turno,
                "count":    count,
                "status":   status,
                "desc":     desc,
                "critical": critical,
                # Nome do canal quando a linha só está zerada porque a máquina
                # não rodou. O Telegram agrupa essas linhas numa só: o fato a
                # comunicar é "o PC não rodou", não N plataformas quietas.
                "canal_offline": canal_offline,
            })
            key = status.lower()
            if key in summary:
                summary[key] += 1
            if status == "FAIL" and critical:
                summary["critical_fail"] += 1

    summary["offline_channels"] = len(offline_vistos)
    summary["offline_names"] = sorted(offline_vistos)

    # Plataformas que coletaram dados mas NÃO estão no expected (ex: dealer
    # novo ou typo) — entram como INFO no relatório
    seen = {(r["platform"], r["turno"]) for r in rows}
    for (plat, turno), count in counts.items():
        if turno_filter and turno != turno_filter:
            continue
        if (plat, turno) not in seen:
            rows.append({
                "platform": plat,
                "turno":    turno,
                "count":    count,
                "status":   "INFO",
                "desc":     "não está em ACTIVE_PLATFORMS",
                "critical": False,
            })

    return rows, summary


# ---------------------------------------------------------------------------
# Formatação — terminal + Telegram HTML
# ---------------------------------------------------------------------------

_STATUS_ICON = {
    "PASS": "✅",
    "WARN": "⚠️",
    "FAIL": "❌",
    "INFO": "ℹ️",
}


def _print_terminal(
    data_str: str,
    turno_filter: Optional[str],
    rows: List[Dict],
    summary: Dict[str, int],
) -> None:
    title = f"STATUS COLETA {data_str}"
    if turno_filter:
        title += f" ({turno_filter})"
    print("\n" + "=" * 78)
    print(f"{title:^78}")
    print("=" * 78)
    fmt = "{:<22} {:<12} {:>7} {:<6} {:<30}"
    print(fmt.format("Plataforma", "Turno", "Reg", "St", "Detalhe"))
    print("-" * 78)

    # Ordena: FAIL críticos primeiro, depois WARN, depois PASS
    order_key = {"FAIL": 0, "WARN": 1, "PASS": 2, "INFO": 3}
    sorted_rows = sorted(
        rows,
        key=lambda r: (order_key.get(r["status"], 9), not r["critical"], r["platform"]),
    )
    for r in sorted_rows:
        icon = _STATUS_ICON.get(r["status"], "?")
        flag = "★" if r["critical"] and r["status"] != "PASS" else " "
        print(fmt.format(
            r["platform"][:21] + flag,
            r["turno"],
            r["count"],
            f"{icon} {r['status']}",
            r["desc"][:30],
        ))
    print("-" * 78)
    print(
        f"Resumo: ✅ {summary['pass']} PASS | ⚠️ {summary['warn']} WARN | "
        f"❌ {summary['fail']} FAIL | crítico: {summary['critical_fail']}"
    )
    for canal in summary.get("offline_names") or []:
        print(
            f"⚠️  CANAL OFFLINE: {canal} — nenhuma plataforma deste canal "
            "coletou. Máquina desligada, não scraper quebrado."
        )
    print("=" * 78 + "\n")


def _print_coverage(
    today: Dict[str, Dict[str, float]],
    baseline: Dict[str, Dict[str, float]],
    cov_warnings: List[Dict],
) -> None:
    """Imprime matriz plataforma × campo (hoje/média 7d) no terminal."""
    if not today:
        return
    print("COBERTURA DOS CAMPOS DE INSIGHT — % preenchido (hoje/média 7d)")
    print("-" * 78)
    header = "{:<22}".format("Plataforma") + "".join(
        f"{_INSIGHT_LABELS[field]:>11}" for field in _INSIGHT_FIELDS
    )
    print(header)
    warned = {(w["platform"], w["field"]) for w in cov_warnings}
    for plat in sorted(today):
        cells: List[str] = []
        for field in _INSIGHT_FIELDS:
            t = today[plat].get(field, 0.0)
            b = baseline.get(plat, {}).get(field)
            cell = f"{t:.0f}/{b:.0f}" if b is not None else f"{t:.0f}/—"
            if (plat, field) in warned:
                cell += "⚠"
            cells.append(f"{cell:>11}")
        print("{:<22}".format(plat[:21]) + "".join(cells))
    if cov_warnings:
        print("-" * 78)
        for w in cov_warnings:
            print(
                f"⚠️ {w['platform']}: {w['field']} caiu para {w['today_pct']:.0f}% "
                f"(média 7d: {w['base_pct']:.0f}%)"
            )
    print("=" * 78 + "\n")


def _coverage_telegram_lines(
    cov_warnings: List[Dict], overall_line: str
) -> List[str]:
    """Bloco compacto de cobertura de insight para a mensagem Telegram."""
    import html as _html
    esc = _html.escape

    lines = ["<b>📈 Campos de insight</b>"]
    if overall_line:
        lines.append(f"  {esc(overall_line)}")
    if cov_warnings:
        for w in cov_warnings:
            lines.append(
                f"  ⚠️ <code>{esc(w['platform'])}</code>: "
                f"{esc(_INSIGHT_LABELS.get(w['field'], w['field']))} "
                f"{w['today_pct']:.0f}% (7d: {w['base_pct']:.0f}%)"
            )
    else:
        lines.append("  ✅ sem regressão de cobertura vs média 7d")
    lines.append("")
    return lines


def _is_dealer(platform: str) -> bool:
    """True se a plataforma é dealer (não marketplace nacional)."""
    return platform in DEALER_CONFIGS


def _format_telegram(
    data_str: str,
    turno_filter: Optional[str],
    rows: List[Dict],
    summary: Dict[str, int],
    coverage_lines: Optional[List[str]] = None,
) -> str:
    """Formata mensagem HTML pra Telegram.

    Estratégia:
      - Marketplaces (Amazon, ML, Magalu...): cada um aparece com status próprio
      - Dealers: agrupados — FAILs viram uma linha compacta com a lista dos nomes
        (evita spam de 20 linhas quando vários dealers menores falham)
    """
    import html as _html
    esc = _html.escape

    # Cabeçalho — emoji indica saúde geral
    if summary["critical_fail"] > 0:
        header_emoji = "🔴"
    elif summary["fail"] > 0 or summary["warn"] > 0:
        header_emoji = "🟡"
    else:
        header_emoji = "🟢"

    title = f"{header_emoji} <b>Status Coleta {esc(data_str)}</b>"
    if turno_filter:
        title += f" — {esc(turno_filter)}"

    lines: List[str] = [title, ""]

    # Agrupa por turno
    by_turno: Dict[str, List[Dict]] = {}
    for r in rows:
        by_turno.setdefault(r["turno"], []).append(r)

    for turno in sorted(by_turno.keys()):
        lines.append(f"<b>📅 {esc(turno)}</b>")

        # Separa marketplaces (alta visibilidade) de dealers (agrupados)
        marketplaces = [r for r in by_turno[turno] if not _is_dealer(r["platform"])]
        dealers      = [r for r in by_turno[turno] if _is_dealer(r["platform"])]

        # --- Canais offline: uma linha por canal, não uma por plataforma ---
        # Quando o PC coletor não roda, todas as suas plataformas vêm zeradas.
        # Repetir isso em N linhas é o mesmo ruído que o alerta queria acabar:
        # o fato é único ("o canal não rodou") e assim deve ser comunicado.
        offline_rows = [r for r in by_turno[turno] if r.get("canal_offline")]
        for canal in sorted({r["canal_offline"] for r in offline_rows}):
            nomes = sorted(
                r["platform"] for r in offline_rows if r["canal_offline"] == canal
            )
            lines.append(
                f"  ⚪ <b>Canal offline — {esc(canal)}</b>: "
                f"{len(nomes)} plataforma(s) sem coleta "
                f"(<code>{esc(', '.join(nomes))}</code>)"
            )

        # --- Marketplaces: linha por linha ---
        mk_order = {"FAIL": 0, "WARN": 1, "PASS": 2, "INFO": 3}
        marketplaces.sort(key=lambda r: (mk_order.get(r["status"], 9), r["platform"]))
        for r in marketplaces:
            if r["status"] == "INFO":
                continue  # não pertence ao registry — silencioso
            if r.get("canal_offline"):
                continue  # já contabilizada no bloco de canal offline acima
            icon = _STATUS_ICON[r["status"]]
            crit = " <b>[CRÍTICO]</b>" if r["critical"] and r["status"] != "PASS" else ""
            lines.append(
                f"  {icon} <code>{esc(r['platform'])}</code>: "
                f"{r['count']} reg — {esc(r['desc'])}{crit}"
            )

        # --- Dealers: agrupados por status ---
        dealer_by_status: Dict[str, List[Dict]] = {}
        for r in dealers:
            if r.get("canal_offline"):
                continue  # já contabilizada no bloco de canal offline acima
            dealer_by_status.setdefault(r["status"], []).append(r)

        d_fail = dealer_by_status.get("FAIL", [])
        d_warn = dealer_by_status.get("WARN", [])
        d_pass = dealer_by_status.get("PASS", [])

        if d_fail:
            names = ", ".join(sorted(r["platform"] for r in d_fail))
            lines.append(
                f"  ❌ <i>Dealers sem dados ({len(d_fail)}):</i> {esc(names)}"
            )
        if d_warn:
            names = ", ".join(
                f"{r['platform']}({r['count']})"
                for r in sorted(d_warn, key=lambda r: r["platform"])
            )
            lines.append(
                f"  ⚠️ <i>Dealers abaixo do mínimo ({len(d_warn)}):</i> {esc(names)}"
            )
        if d_pass:
            lines.append(
                f"  ✅ <i>Dealers OK ({len(d_pass)})</i>"
            )

        lines.append("")

    if coverage_lines:
        lines.extend(coverage_lines)

    lines.append(
        f"<b>Resumo:</b> ✅ {summary['pass']} | ⚠️ {summary['warn']} | "
        f"❌ {summary['fail']} | crítico: {summary['critical_fail']}"
    )

    return "\n".join(lines)


def _format_telegram_sem_supabase(
    data_str: str, falha: SupabaseUnavailable, hist: Optional[Dict]
) -> str:
    """Alerta para quando o Supabase não responde.

    Antes, esse caminho terminava em ``return 2`` sem mandar nada: o único dia
    em que o alerta mais importa (banco fora) era o único dia em que ele não
    saía. A mensagem carrega o status do frio justamente porque é ele que
    decide se o dia foi perdido ou só ficou invisível no dashboard.

    O título vem da classificação (``falha.titulo``): "rejeitou a credencial"
    e "inacessível" mandam a pessoa de plantão para lugares diferentes, e o
    alerta genérico de antes não distinguia os dois.
    """
    import html as _html
    esc = _html.escape

    lines = [
        f"🔴 <b>Status Coleta {esc(data_str)}</b>",
        "",
        f"<b>❌ {esc(falha.titulo)}</b> "
        f"(exit {falha.exit_code} — o watchdog não conseguiu ler o banco; "
        "isso NÃO significa que a coleta falhou)",
        f"  <code>{esc(str(falha)[:300])}</code>",
    ]
    if falha.remedy:
        lines.append(f"  🛠 {esc(falha.remedy)}")
    lines.append("")
    if hist:
        lines.extend(_history_telegram_lines(hist))
        if hist["status"] == "PASS":
            lines.append(
                "<i>O dia está no histórico frio — o dashboard fica cego, "
                "mas o dado não se perdeu.</i>"
            )
        elif hist["status"] == "WARN":
            # WARN é partição gravada porém degradada (anêmica, parcialmente
            # ilegível). Anunciar isso como perda total seria mentira: o dado
            # provavelmente ainda dá para recuperar, e é o detalhe acima que diz
            # o quanto.
            lines.append(
                "<i>O dia chegou ao histórico frio, mas degradado — veja o "
                "detalhe acima antes de contar com ele.</i>"
            )
        else:
            lines.append(
                "<b>⚠️ Sem banco e sem histórico: o dia de hoje não está em "
                "lugar nenhum além do CSV.</b>"
            )
    else:
        lines.append(
            "<i>Histórico frio não pôde ser verificado — rode: "
            "<code>python scripts/history_cli.py stats</code></i>"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Notificação Telegram (reusa infraestrutura do n8n_notify)
# ---------------------------------------------------------------------------

def _send_telegram(message: str) -> bool:
    """Envia via N8N webhook se configurado, fallback Bot API direto."""
    try:
        from utils.n8n_notify import _send  # type: ignore[attr-defined]
    except Exception as exc:
        logger.warning(f"Não foi possível importar utils.n8n_notify: {exc}")
        return False

    payload = {
        "event":   "daily_status",
        "message": message,
    }
    try:
        return _send(payload)
    except Exception as exc:
        logger.warning(f"Envio Telegram falhou: {exc}")
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valida status diário das plataformas no Supabase",
    )
    parser.add_argument(
        "--data",
        help="Data YYYY-MM-DD (padrão: hoje BRT)",
        default=None,
    )
    parser.add_argument(
        "--turno",
        choices=["Abertura", "Fechamento"],
        default=None,
        help="Filtra por turno (padrão: ambos)",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Não envia Telegram, só imprime no terminal",
    )
    args = parser.parse_args()

    if args.data:
        data_str = args.data
    else:
        data_str = now_brt().strftime("%Y-%m-%d")

    logger.info(f"[daily_status] Validando coleta de {data_str} "
                f"(turno={args.turno or 'todos'})")

    # Histórico frio PRIMEIRO e fora de qualquer dependência do Supabase. Ele é
    # a redundância: precisa ser verificado (e alertado) exatamente nos dias em
    # que o banco não responde — antes, um Supabase fora abortava o script no
    # `return 2` e ninguém descobria que o frio também estava vazio.
    hist: Optional[Dict] = None
    try:
        hist = _check_history(data_str)
    except Exception as exc:
        # pyarrow ausente, credencial podre, erro inesperado de leitura: daqui
        # não dá para distinguir "o backup está bem, só não consegui olhar" de
        # "o backup está vazio". Engolir isso devolvia watchdog verde com o frio
        # sem verificação nenhuma — que é o buraco que este check existe para
        # fechar. Um check que não roda conta como falha.
        logger.warning(f"[daily_status] Check do histórico frio indisponível: {exc}")
        hist = {
            "status": "FAIL",
            "backend": "indisponível",
            "partitions": 0,
            "rows": 0,
            "detail": f"não foi possível verificar o histórico frio: {exc}",
        }

    try:
        counts = _fetch_counts(data_str, args.turno)
    except RuntimeError as exc:
        falha = (
            exc if isinstance(exc, SupabaseUnavailable)
            else _classify_supabase_error(exc)
        )
        logger.error(f"[daily_status] {falha.titulo}: {falha}")

        # Banner no terminal (e não só no log): o motivo real precisa ser a
        # primeira coisa visível na aba Actions. Nove runs seguidos falharam
        # por 401 "Unregistered API key" com a causa enterrada no meio do log,
        # indistinguível de "a coleta não trouxe dado".
        print("\n" + "=" * 78)
        print(f"{'FALHA DE FERRAMENTA — WATCHDOG NÃO PÔDE LER O SUPABASE':^78}")
        print("=" * 78)
        print(f"Natureza : {falha.titulo} (exit {falha.exit_code})")
        print(f"Detalhe  : {str(falha)[:300]}")
        if falha.remedy:
            print(f"Ação     : {falha.remedy}")
        print(
            "Nota     : isto NÃO é o mesmo que 'a coleta não trouxe dado' "
            f"(exit {EXIT_DADO_AUSENTE})."
        )
        print("=" * 78)

        # Sem o banco não há tabela de plataformas para montar — mas o frio é
        # verificável sem ele, e é o que decide se o dia foi perdido de fato.
        if hist:
            icon = _STATUS_ICON.get(hist["status"], "?")
            print(f"\nHISTÓRICO FRIO: {icon} {hist['status']} — {hist['detail']}")
            print("=" * 78 + "\n")
        if not args.no_notify:
            _send_telegram(_format_telegram_sem_supabase(data_str, falha, hist))
        return falha.exit_code

    rows, summary = _build_report(data_str, args.turno, counts)

    if hist:
        if hist["status"] == "WARN":
            summary["warn"] += 1
        elif hist["status"] == "FAIL":
            summary["fail"] += 1
            # Frio vazio é perda de dado permanente, não degradação — pesa como
            # falha crítica para o job do watchdog terminar vermelho.
            summary["critical_fail"] += 1

    # Cobertura dos campos de insight — best-effort: bancos sem a migration
    # 003 ou falhas de consulta não derrubam o relatório de contagens.
    cov_today: Dict[str, Dict[str, float]] = {}
    cov_base: Dict[str, Dict[str, float]] = {}
    cov_rows: Dict[str, int] = {}
    cov_warnings: List[Dict] = []
    try:
        insight_rows = _fetch_insight_rows(data_str, args.turno)
        cov_today, cov_base, cov_rows = _coverage_tables(insight_rows, data_str)
        cov_warnings = _evaluate_coverage(cov_today, cov_base)
        summary["warn"] += len(cov_warnings)
    except RuntimeError as exc:
        logger.warning(f"[daily_status] Cobertura de insight indisponível: {exc}")

    # Import D-1 do PriceTrack — best-effort: falha de consulta não derruba
    # o relatório das plataformas (mesmo contrato da cobertura de insight).
    pt_check: Optional[Dict] = None
    try:
        pt_check = _check_pricetrack_import(data_str)
        if pt_check["status"] == "WARN":
            summary["warn"] += 1
        elif pt_check["status"] == "FAIL":
            summary["fail"] += 1
    except RuntimeError as exc:
        logger.warning(f"[daily_status] Check do import PriceTrack indisponível: {exc}")

    _print_terminal(data_str, args.turno, rows, summary)
    _print_coverage(cov_today, cov_base, cov_warnings)
    if hist:
        icon = _STATUS_ICON.get(hist["status"], "?")
        print(f"HISTÓRICO FRIO: {icon} {hist['status']} — {hist['detail']}")
        print("=" * 78 + "\n")
    if pt_check:
        icon = _STATUS_ICON.get(pt_check["status"], "?")
        print(f"PRICETRACK IMPORT (D-1): {icon} {pt_check['status']} — "
              f"{pt_check['detail']}")
        print("=" * 78 + "\n")

    if not args.no_notify:
        coverage_lines = (
            _coverage_telegram_lines(
                cov_warnings, _coverage_overall_line(cov_today, cov_rows)
            )
            if cov_today else []
        )
        if hist:
            coverage_lines = coverage_lines + _history_telegram_lines(hist)
        if pt_check:
            coverage_lines = coverage_lines + _pricetrack_telegram_lines(pt_check)
        msg = _format_telegram(data_str, args.turno, rows, summary,
                               coverage_lines or None)
        sent = _send_telegram(msg)
        if sent:
            logger.success("[daily_status] Notificação enviada ao Telegram.")
        else:
            logger.warning("[daily_status] Notificação Telegram não enviada "
                           "(N8N_WEBHOOK_URL / TELEGRAM_BOT_TOKEN ausentes?).")

    return EXIT_OK if summary["critical_fail"] == 0 else EXIT_DADO_AUSENTE


if __name__ == "__main__":
    sys.exit(main())
