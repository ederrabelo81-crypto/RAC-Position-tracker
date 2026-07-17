"""
utils/supabase_client.py — Upload de registros para Supabase.

Carrega credenciais do .env (SUPABASE_URL + SUPABASE_KEY).
Mapeia colunas do formato interno do bot para a tabela `coletas`.
Faz upsert em lotes de 500 linhas para evitar timeout na free tier.

USO:
    from utils.supabase_client import upload_to_supabase
    upload_to_supabase(all_records)   # lista de dicts do scraper

REQUISITOS:
    pip install supabase python-dotenv
    .env na raiz do projeto com:
        SUPABASE_URL=https://xxxx.supabase.co
        SUPABASE_KEY=eyJ...
"""

import os
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from utils.text import is_valid_product
from utils.normalize_product import normalize_product_name, normalize_product_name_v2

# Carrega .env da raiz do projeto (pai de utils/)
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(_env_path)
except ImportError:
    pass  # python-dotenv opcional — variáveis podem vir do ambiente

try:
    from supabase import create_client, Client
    _HAS_SUPABASE = True
except ImportError:
    _HAS_SUPABASE = False

# Tamanho do lote para upsert — free tier Supabase suporta bem até 500 linhas
_BATCH_SIZE = 500

# Mapeamento: coluna interna do bot → coluna na tabela `coletas` do Supabase
_COLUMN_MAP = {
    "Data":                 "data",
    "Turno":                "turno",
    "Horário":              "horario",
    "Plataforma":           "plataforma",
    "Tipo Plataforma":      "tipo",
    "Keyword Buscada":      "keyword",
    "Categoria Keyword":    "categoria",
    "Marca Monitorada":     "marca",
    "Produto / SKU":        "produto",
    "Produto Normalizado":  "produto_normalizado",
    "Posição Orgânica":     "posicao_organica",
    "Posição Patrocinada":  "posicao_patrocinada",
    "Posição Geral":        "posicao_geral",
    "Patrocinado?":         "patrocinado",
    # ── Insights de buy box / seller (foco principal — Mai/2026) ──
    "Buy Box Seller":       "buy_box_seller",
    "Qtd Sellers":          "qtd_sellers",
    "Tipo Seller":          "tipo_seller",
    "Reputação Seller":     "reputacao_seller",
    "Preço (R$)":           "preco",
    "Seller / Vendedor":    "seller",
    "Fulfillment?":         "fulfillment",
    "Avaliação":            "avaliacao",
    "Qtd Avaliações":       "qtd_avaliacoes",
    "Tag Destaque":         "tag",
    "URL Produto":          "url_produto",
    "Screenshot Busca":     "screenshot_busca",
    "Screenshot Produto":   "screenshot_produto",
    # run_id é injetado diretamente no upload — não vem do CSV/dict interno
}

# Colunas adicionadas posteriormente ao schema — podem não existir em bancos
# ainda não migrados. Se o upsert falhar por coluna ausente, o upload remove
# essas chaves e tenta novamente (degradação graciosa).
_OPTIONAL_DEST_COLS = {
    "url_produto", "screenshot_busca", "screenshot_produto",
    # Adicionadas na migration 003 (foco buy box/seller)
    "patrocinado", "buy_box_seller", "qtd_sellers", "tipo_seller", "reputacao_seller",
    # Adicionada na migration 004 (formato canônico v2 SKU-anchored)
    "produto_normalizado",
}

# Colunas numéricas — None em vez de NaN para o Postgres
_INT_COLS   = {"posicao_organica", "posicao_patrocinada", "posicao_geral",
               "qtd_avaliacoes", "qtd_sellers"}
_FLOAT_COLS = {"preco", "avaliacao"}
_BOOL_COLS  = {"fulfillment", "patrocinado"}


def _get_client() -> Optional["Client"]:
    """Cria e retorna o client Supabase, ou None se não configurado."""
    if not _HAS_SUPABASE:
        logger.error(
            "[Supabase] ❌ Pacote 'supabase' NÃO instalado. "
            "Execute no terminal: pip install supabase"
        )
        return None

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", "").strip()

    if not url:
        logger.error(
            "[Supabase] ❌ SUPABASE_URL não encontrada. "
            f"Verifique o arquivo .env em: {Path(__file__).parent.parent / '.env'}"
        )
        return None
    if not key:
        logger.error(
            "[Supabase] ❌ SUPABASE_KEY não encontrada. "
            f"Verifique o arquivo .env em: {Path(__file__).parent.parent / '.env'}"
        )
        return None

    logger.info(f"[Supabase] Conectando em: {url[:40]}...")
    try:
        client = create_client(url, key)
        logger.info("[Supabase] ✓ Conexão estabelecida.")
        return client
    except Exception as exc:
        logger.error(f"[Supabase] ❌ Falha ao criar client: {exc}")
        return None


def is_quota_restricted_error(exc: Exception) -> bool:
    """
    Detecta o estado "projeto restrito por cota de ARMAZENAMENTO" do Supabase.

    Quando o banco estoura a cota de disco (plano free), o Supabase RESTRINGE o
    projeto inteiro: a API REST (PostgREST) passa a responder HTTP 402 com o
    marcador `exceed_db_size_quota` em TODAS as operações — leitura e escrita.
    Nesse estado não adianta reenviar lotes nem rodar a automação; o único
    caminho é liberar espaço no banco ou fazer upgrade do plano.

    Detecta pelo marcador específico `exceed_db_size_quota` — e NÃO por um HTTP
    402 genérico. Outras restrições 402 (cota de egress, pagamento em atraso)
    exigem remediação diferente e não devem disparar a mensagem de "disco cheio"
    nem abortar o upload como se fosse falta de espaço; essas caem no tratamento
    de erro comum.

    Args:
        exc: exceção capturada de uma chamada ao Supabase (postgrest APIError etc.).

    Returns:
        True se o erro indica projeto restrito por cota de armazenamento.

    Example:
        >>> try:
        ...     client.table("coletas").insert(rows).execute()
        ... except Exception as exc:
        ...     if is_quota_restricted_error(exc):
        ...         logger.error("Banco cheio — libere espaço ou faça upgrade.")
    """
    return "exceed_db_size_quota" in str(exc).lower()


def _map_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Converte um registro do formato interno do bot para o formato da tabela.
    Trata tipos: int/float/bool/None. Trunca produto a 500 chars (limite índice PG).
    """
    row: Dict[str, Any] = {}
    for src_col, dest_col in _COLUMN_MAP.items():
        val = record.get(src_col)

        # Converte NaN/pandas NA para None
        if val != val:  # NaN check (NaN != NaN em float)
            val = None
        elif hasattr(val, "item"):  # numpy scalar → Python nativo
            val = val.item()

        if val is None or val == "" or val == "nan":
            val = None
        elif dest_col in _INT_COLS:
            try:
                val = int(float(val))
            except (ValueError, TypeError):
                val = None
        elif dest_col in _FLOAT_COLS:
            try:
                val = round(float(val), 2)
            except (ValueError, TypeError):
                val = None
        elif dest_col in _BOOL_COLS:
            if isinstance(val, bool):
                pass
            elif isinstance(val, str):
                val = val.strip().lower() in ("sim", "yes", "true", "1", "s")
            else:
                val = bool(val) if val is not None else None

        # Trunca produto — índice único PG tem limite de ~2700 bytes
        if dest_col == "produto" and isinstance(val, str) and len(val) > 500:
            val = val[:500]

        row[dest_col] = val

    # Normalize product name for CSV imports (live scraping normalizes in _build_record)
    if row.get("produto"):
        normalized = normalize_product_name(row["produto"], row.get("marca"))
        if normalized:
            row["produto"] = normalized[:500]

    # Canonical v2 (UPPERCASE, SKU-anchored). Só a parte descritiva aqui —
    # voltagem/SKU são anexados pela resolução de-para. Live scraping já
    # preenche "Produto Normalizado" em _build_record; CSV imports caem aqui.
    if row.get("produto_normalizado") is None:
        base_name = record.get("Produto / SKU") or row.get("produto")
        if base_name:
            v2 = normalize_product_name_v2(base_name, row.get("marca"))
            if v2:
                row["produto_normalizado"] = v2[:500]

    return row


def _is_ac_row(row: Dict[str, Any]) -> bool:
    """
    Retorna True se o registro mapeado deve ser mantido.

    Permite registros sem nome de produto (dados parciais com posição/preço).
    Rejeita apenas quando o nome do produto está presente e claramente não é AC.
    """
    produto = row.get("produto") or ""
    if not produto:
        return True  # Sem nome → não é possível validar → mantém
    preco = row.get("preco")  # None se não capturado
    return is_valid_product(produto, preco)










# ---------------------------------------------------------------------------
# Price validation — detect ×10 parser errors stored in DB
# ---------------------------------------------------------------------------

# Reasonable price ceilings per BTU capacity (R$) for residential/light-commercial ACs.
# Prices above these are almost certainly ×10 parse errors (e.g., R$ 1.899 stored as 18990).
_BTU_PRICE_CEILINGS: Dict[int, float] = {
    7000:  4_500,
    9000:  5_500,
    12000: 7_000,
    18000: 12_000,
    22000: 15_000,
    24000: 16_000,
    30000: 22_000,
    36000: 28_000,
    48000: 40_000,
    60000: 55_000,
}

# Matches BTU values like "9.000 BTU", "12000 BTUs", "9,000 BTU"
_BTU_RE = re.compile(
    r'(\d{1,2})[.,](\d{3})\s*BTU|(\d{4,6})\s*BTU',
    re.IGNORECASE,
)


def _extract_btu(produto: str) -> Optional[int]:
    """Extract BTU capacity (int) from a product name, or None if not found."""
    m = _BTU_RE.search(produto or "")
    if not m:
        return None
    if m.group(3):          # raw: "9000 BTU"
        return int(m.group(3))
    return int(m.group(1)) * 1000 + int(m.group(2))   # dotted: "9.000 BTU"


def _is_price_suspicious(produto: str, preco: float) -> bool:
    """
    Returns True if preco exceeds the reasonable ceiling for the BTU detected in produto.
    When no BTU is detected, uses the global R$ 80,000 ceiling from is_valid_product().
    """
    if not preco or preco <= 0:
        return False
    btu = _extract_btu(produto)
    if btu is None:
        return preco > 80_000
    closest_btu = min(_BTU_PRICE_CEILINGS, key=lambda k: abs(k - btu))
    return preco > _BTU_PRICE_CEILINGS[closest_btu]




def upload_to_supabase(
    records: List[Dict[str, Any]],
    run_id: Optional[str] = None,
) -> bool:
    """
    Faz upload de uma lista de registros para a tabela `coletas` no Supabase.

    Cada execução do scraper deve passar um `run_id` único (UUID) gerado no
    início da sessão. Isso permite múltiplos snapshots por turno sem colapso
    silencioso — a constraint UNIQUE agora inclui run_id.

    Registros históricos (importados sem run_id) continuam com NULL e são
    tratados como snapshot consolidado pelo dashboard.

    Args:
        records: lista de dicts no formato interno do bot (mesmo formato do CSV)
        run_id:  UUID string gerado pelo main.py para esta execução.
                 None é aceito para compatibilidade com imports manuais.

    Returns:
        True se upload bem-sucedido, False se falhou (CSV já foi salvo antes).
    """
    if not records:
        logger.info("[Supabase] Nenhum registro para enviar.")
        return True

    logger.info(
        f"[Supabase] Iniciando upload de {len(records)} registros "
        f"(run_id={run_id or 'NULL'})..."
    )
    client = _get_client()
    if client is None:
        return False

    rows = [_map_record(r) for r in records]

    # Remove linhas sem plataforma (requerida pela constraint NOT NULL)
    rows = [r for r in rows if r.get("plataforma")]

    # Filtra produtos não relacionados a ar-condicionado
    before_filter = len(rows)
    rows = [r for r in rows if _is_ac_row(r)]
    filtered_out = before_filter - len(rows)
    if filtered_out > 0:
        logger.info(
            f"[Supabase] Filtro AC: {filtered_out} registro(s) removido(s) "
            f"(não relacionados a ar-condicionado)."
        )

    # Injeta run_id em todas as linhas do batch
    for row in rows:
        row["run_id"] = run_id  # None para imports históricos

    total   = len(rows)
    batches = math.ceil(total / _BATCH_SIZE)
    sent    = 0
    dupes   = 0  # linhas ignoradas por já existirem (acumulado entre lotes)
    errors  = 0
    # Quando o banco ainda não tem as colunas opcionais (url/screenshots),
    # o primeiro erro de coluna ausente ativa este flag e os lotes seguintes
    # já são enviados sem essas chaves.
    drop_optional = False
    # Projeto restrito por cota (disco cheio → HTTP 402): TODOS os lotes falham
    # igual. No 1º lote com esse erro abortamos o restante (fail-fast) em vez de
    # repetir a mesma falha N vezes.
    quota_restricted = False

    def _strip_optional(batch_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {k: v for k, v in row.items() if k not in _OPTIONAL_DEST_COLS}
            for row in batch_rows
        ]

    def _is_missing_column_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return (
            "pgrst204" in msg
            or ("column" in msg and ("does not exist" in msg or "could not find" in msg))
            or any(c in msg for c in _OPTIONAL_DEST_COLS)
        )

    logger.info(f"[Supabase] Enviando {total} registros em {batches} lote(s)...")

    for i in range(batches):
        batch = rows[i * _BATCH_SIZE : (i + 1) * _BATCH_SIZE]
        if drop_optional:
            batch = _strip_optional(batch)
        # Lote na fronteira entre plataformas contém mais de uma — rotular só
        # pela 1ª linha escondia registros (ex.: Casas Bahia dentro de um lote
        # "Shopee", que na validação do log parecia nunca ter subido).
        plataforma = (
            "+".join(sorted({r.get("plataforma") or "?" for r in batch}))
            if batch else "?"
        )
        try:
            result = client.table("coletas").upsert(
                batch,
                on_conflict="data,turno,plataforma,keyword,produto,run_id",
                ignore_duplicates=True,  # → INSERT ON CONFLICT (coletas_unique_run) DO NOTHING
            ).execute()
            inseridas = len(result.data) if result.data else 0
            ignoradas = len(batch) - inseridas
            sent += inseridas
            dupes += ignoradas
            if ignoradas:
                logger.info(
                    f"[INSERT] Plataforma={plataforma} | Lote {i+1}/{batches} | "
                    f"Inseridas={inseridas} | Já existiam={ignoradas} (ignoradas)"
                )
            else:
                logger.info(
                    f"[INSERT] Plataforma={plataforma} | Lote {i+1}/{batches} | "
                    f"Inseridas={inseridas}"
                )
        except Exception as exc:
            # Projeto restrito por cota de armazenamento (disco cheio) → a API
            # devolve HTTP 402 em todos os lotes. Aborta o restante (fail-fast).
            if is_quota_restricted_error(exc):
                quota_restricted = True
                errors += len(batch)
                break
            # Banco sem as colunas opcionais (url/screenshots) → remove e tenta de novo
            if not drop_optional and _is_missing_column_error(exc):
                drop_optional = True
                logger.warning(
                    "[Supabase] Colunas opcionais (url_produto/screenshot_*) ausentes "
                    "no banco — reenviando sem elas. Aplique a migração "
                    "docs/migrations/001_add_url_screenshot_columns.sql para persisti-las."
                )
                try:
                    result = client.table("coletas").upsert(
                        _strip_optional(batch),
                        on_conflict="data,turno,plataforma,keyword,produto,run_id",
                        ignore_duplicates=True,
                    ).execute()
                    inseridas = len(result.data) if result.data else 0
                    sent += inseridas
                    dupes += len(batch) - inseridas
                    logger.info(
                        f"[INSERT] Plataforma={plataforma} | Lote {i+1}/{batches} | "
                        f"Inseridas={inseridas} (sem colunas opcionais)"
                    )
                    continue
                except Exception as exc2:
                    # A cota pode estourar também no reenvio sem colunas opcionais.
                    if is_quota_restricted_error(exc2):
                        quota_restricted = True
                        errors += len(batch)
                        break
                    exc = exc2
            errors += len(batch)
            logger.warning(
                f"[INSERT] Plataforma={plataforma} | Lote {i+1}/{batches} falhou: {exc}"
            )

    # `dupes` = já existiam (contadas nos lotes processados); `nao_enviados` =
    # linhas em lotes nunca tentados (só quando abortamos por cota). Sem abort,
    # nao_enviados é sempre 0 e o resumo fecha: total = sent + dupes + errors.
    ignorados = dupes
    nao_enviados = total - sent - dupes - errors
    logger.info(
        f"[INSERT] Run={run_id or 'NULL'} | "
        f"Tentadas={total} | Inseridas={sent} | Já existiam={ignorados} | Erros={errors}"
        + (f" | Não enviados (abortado)={nao_enviados}" if quota_restricted else "")
    )

    if quota_restricted:
        logger.error(
            "[Supabase] 🚫 Projeto RESTRITO por cota de armazenamento "
            "(exceed_db_size_quota): a API respondeu HTTP 402 e o upload foi "
            f"abortado — {sent} de {total} registros gravados.\n"
            "   • O CSV local JÁ está salvo — nada foi perdido.\n"
            "   • Reenvie quando o banco voltar: "
            "python scripts/upload_csv.py <arquivo.csv> (idempotente).\n"
            "   • Para RESTAURAR o serviço: libere espaço no banco (as maiores "
            "tabelas são pricetrack_daily e coletas) ou faça upgrade do plano "
            "Supabase / remova o spend cap."
        )
        return False

    if errors == 0:
        if sent == 0 and ignorados > 0:
            logger.info(
                f"[Supabase] Todos os {ignorados} registros já existiam no banco (run idempotente)."
            )
        else:
            logger.success(f"[Supabase] {sent} registros inseridos com sucesso.")
        return True
    elif sent > 0 or ignorados > 0:
        logger.warning(f"[Supabase] Upload parcial: {sent} inseridos, {ignorados} já existiam, {errors} com erro.")
        return False
    else:
        logger.error(f"[Supabase] Upload falhou para todos os {total} registros.")
        return False


def log_auditoria_run(
    run_id: str,
    csv_path: str,
    client: Optional["Client"] = None,
) -> None:
    """
    Compara contagem do CSV (após filtro AC) com registros gravados no Supabase
    para este run_id.

    Args:
        run_id:   UUID da execução atual
        csv_path: caminho para o CSV gerado nesta execução
        client:   client Supabase já instanciado (opcional — cria novo se None)
    """
    import pandas as pd

    try:
        df = pd.read_csv(csv_path, sep=";", encoding="utf-8-sig")
        csv_total = len(df)
    except Exception as exc:
        logger.warning(f"[Auditoria] Não foi possível ler o CSV: {exc}")
        return

    if client is None:
        client = _get_client()
    if client is None:
        logger.warning("[Auditoria] Client Supabase indisponível — pulando auditoria.")
        return

    try:
        sb_result = (
            client.table("coletas")
            .select("id", count="exact")
            .eq("run_id", run_id)
            .execute()
        )
        sb_total = sb_result.count or 0
    except Exception as exc:
        logger.warning(f"[Auditoria] Erro ao consultar Supabase: {exc}")
        return

    # Calcula esperado: únicos por (plataforma, keyword, produto) após filtro AC.
    # A constraint coletas_unique_run permite apenas 1 linha por (keyword, produto, run_id),
    # então o CSV pode ter duplicatas intra-run (mesmo produto em posições diferentes
    # para a mesma keyword) que são descartadas corretamente pelo banco.
    try:
        from utils.text import is_valid_product
        plat_col  = df.get("Plataforma",    df.get("plataforma",   pd.Series()))
        kw_col    = df.get("Keyword Buscada", df.get("keyword",    pd.Series()))
        prod_col  = df.get("Produto / SKU", df.get("Produto/SKU",  df.get("produto", pd.Series())))
        preco_col = df.get("Preço (R$)",    df.get("preco",        pd.Series()))

        seen: set = set()
        csv_ac = 0
        for plat, kw, p, r in zip(plat_col, kw_col, prod_col, preco_col):
            prod_str  = str(p) if pd.notna(p) else ""
            preco_val = float(r) if pd.notna(r) else None
            if not is_valid_product(prod_str, preco_val):
                continue
            key = (str(plat) if pd.notna(plat) else "", str(kw) if pd.notna(kw) else "", prod_str)
            if key not in seen:
                seen.add(key)
                csv_ac += 1
        csv_duplicatas = csv_total - csv_ac
    except Exception:
        csv_ac = csv_total
        csv_duplicatas = 0

    sep = "=" * 60
    logger.info(f"\n{sep}")
    logger.info(f"AUDITORIA RUN {run_id}")
    logger.info(sep)
    logger.info(f"CSV total:                    {csv_total}")
    if csv_duplicatas:
        logger.info(f"CSV duplicatas intra-run:     {csv_duplicatas}  (mesmo produto+keyword, posições diferentes)")
    logger.info(f"CSV únicos (plat+kw+produto): {csv_ac}  (esperado no Supabase)")
    logger.info(f"Supabase (run_id):            {sb_total}")
    diff = csv_ac - sb_total
    if diff > 0:
        logger.warning(
            f"[Auditoria] DISCREPÂNCIA: {diff} registro(s) únicos do CSV não estão no Supabase."
        )
    else:
        logger.success("[Auditoria] CSV e Supabase em sincronia.")
    logger.info(f"{sep}\n")




