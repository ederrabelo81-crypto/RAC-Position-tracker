"""
reenviar_csv.py — v7 FINAL
Reenvia CSV para o Supabase com run_id novo.
Uso: python reenviar_csv.py output\rac_monitoramento_20260502_1036.csv
"""

import sys, uuid, os, re
import pandas as pd
import numpy as np
from datetime import datetime
from loguru import logger
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TABELA       = "coletas"
CHUNK_SIZE   = 500

# Chave única da constraint (deve bater com coletas_unique_run)
ON_CONFLICT_COLS = "data,turno,plataforma,keyword,produto,run_id"

SCHEMA = {
    "data":                {"aliases": ["data","date","data_coleta"],                                   "tipo":"date"},
    "turno":               {"aliases": ["turno","periodo","shift"],                                     "tipo":"text"},
    "horario":             {"aliases": ["horario","hora","horário","time"],                             "tipo":"text"},
    "plataforma":          {"aliases": ["plataforma","platform","marketplace"],                         "tipo":"text"},
    "tipo":                {"aliases": ["tipo","tipo plataforma","tipo_plataforma","type"],             "tipo":"text"},
    "keyword":             {"aliases": ["keyword","keyword buscada","keyword_buscada","busca","query"], "tipo":"text"},
    "categoria":           {"aliases": ["categoria","categoria keyword","categoria_keyword"],            "tipo":"text"},
    "marca":               {"aliases": ["marca","marca monitorada","marca_monitorada","brand"],         "tipo":"text"},
    "produto":             {"aliases": ["produto","produto / sku","produto/sku","titulo","title",
                                        "nome_produto","nome","sku","product"],                         "tipo":"text"},
    "posicao_organica":    {"aliases": ["posicao_organica","posição orgânica","posicao organica"],      "tipo":"integer"},
    "posicao_patrocinada": {"aliases": ["posicao_patrocinada","posição patrocinada","posicao patrocinada"],"tipo":"integer"},
    "posicao_geral":       {"aliases": ["posicao_geral","posição geral","posicao geral","rank"],        "tipo":"integer"},
    "preco":               {"aliases": ["preco","preço (r$)","preco (r$)","preço","price","valor"],     "tipo":"numeric"},
    "seller":              {"aliases": ["seller","seller / vendedor","seller/vendedor","vendedor"],      "tipo":"text"},
    "fulfillment":         {"aliases": ["fulfillment","fulfillment?","entrega","frete"],                "tipo":"boolean"},
    "avaliacao":           {"aliases": ["avaliacao","avaliação","rating","nota"],                       "tipo":"numeric"},
    "qtd_avaliacoes":      {"aliases": ["qtd_avaliacoes","qtd avaliações","reviews","avaliacoes"],      "tipo":"integer"},
    "tag":                 {"aliases": ["tag","tag destaque","tag_destaque","tags","badge"],            "tipo":"text"},
}

PALAVRAS_EXCLUIR = [
    "ventilador","climatizador","purificador","umidificador","aquecedor",
    "coifa","exaustor","peça","suporte","controle remoto","capa","filtro",
    "gás","fluido","mangueira","duto","instalação"
]

# ── Helpers ───────────────────────────────────────────────────────────────────
def normalizar_colunas(df):
    idx = {c.lower().strip(): c for c in df.columns}
    ren, miss = {}, []
    for col, info in SCHEMA.items():
        if col in df.columns: continue
        found = next((idx[a.lower()] for a in info["aliases"] if a.lower() in idx), None)
        if found: ren[found] = col
        else: miss.append(col)
    if ren:
        logger.info(f"[COLUNAS] Mapeando: {ren}")
        df = df.rename(columns=ren)
    if miss:
        logger.warning(f"[COLUNAS] Sem match (omitidas): {miss}")
    return df


def converter_tipos(df, data_referencia: str):
    for col, info in SCHEMA.items():
        if col not in df.columns:
            continue
        tipo = info["tipo"]

        if tipo == "boolean":
            mapa = {"sim":True,"s":True,"yes":True,"y":True,"true":True,"1":True,
                    "não":False,"nao":False,"n":False,"no":False,"false":False,"0":False}
            df[col] = df[col].apply(
                lambda v: mapa.get(str(v).strip().lower(), None) if pd.notna(v) else None
            )

        elif tipo == "integer":
            df[col] = (df[col].astype(str)
                       .str.replace(r"\.0+$", "", regex=True)
                       .str.replace(r"[^\d-]", "", regex=True))
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

        elif tipo == "numeric":
            s = df[col].astype(str).str.replace(r"R\$|\s", "", regex=True)
            br = s.str.match(r"^\d{1,3}(\.\d{3})*(,\d+)?$")
            s = s.where(~br, s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False))
            s = s.str.replace(",", ".", regex=False)
            df[col] = pd.to_numeric(s, errors="coerce")

        elif tipo == "date":
            # Força a data de referência extraída do nome do arquivo — não confia no CSV
            df[col] = data_referencia

    logger.info(f"[TIPOS] Conversão concluída. Data forçada: {data_referencia}")
    return df


def limpar_nan(records):
    limpos = []
    for row in records:
        limpa = {}
        for k, v in row.items():
            try:
                is_na = pd.isna(v)
            except Exception:
                is_na = False
            if is_na:
                limpa[k] = None
            elif isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
                limpa[k] = None
            elif isinstance(v, np.integer):
                limpa[k] = int(v)
            elif isinstance(v, np.floating):
                limpa[k] = float(v)
            else:
                limpa[k] = v
        limpos.append(limpa)
    return limpos


def filtro_rac(df):
    if "produto" not in df.columns:
        return df
    mask = df["produto"].fillna("").str.lower().apply(
        lambda n: not any(p in n for p in PALAVRAS_EXCLUIR)
    )
    logger.info(f"[FILTRO RAC] {(~mask).sum()} registro(s) removido(s).")
    return df[mask].copy()


def extrair_data_do_nome(csv_path: str) -> str:
    nome = os.path.basename(csv_path)
    m = re.search(r"(\d{4})(\d{2})(\d{2})_\d{4}", nome)
    if m:
        data = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        logger.info(f"[DATA] Extraída do nome do arquivo: {data}")
        return data
    data = datetime.now().strftime("%Y-%m-%d")
    logger.warning(f"[DATA] Nome sem padrão — usando hoje: {data}")
    return data


def inserir_lote(sb, lote, i, n_lotes):
    """
    Usa upsert com on_conflict explícito (nome da constraint).
    ignore_duplicates=True + on_conflict = INSERT ... ON CONFLICT (...) DO NOTHING
    """
    try:
        result = sb.table(TABELA).upsert(
            lote,
            on_conflict=ON_CONFLICT_COLS,
            ignore_duplicates=True
        ).execute()
        inseridas = len(result.data) if result.data else 0
        ignoradas = len(lote) - inseridas
        logger.info(f"[INSERT] Lote {i}/{n_lotes} | Inseridas={inseridas} Ignoradas={ignoradas}")
        return inseridas, ignoradas, 0
    except Exception as e:
        logger.error(f"[INSERT] Lote {i}/{n_lotes} falhou: {e}")
        return 0, 0, len(lote)


# ── Core ──────────────────────────────────────────────────────────────────────
def reenviar(csv_path: str):
    if not os.path.exists(csv_path):
        logger.error(f"Arquivo não encontrado: {csv_path}")
        sys.exit(1)

    run_id   = str(uuid.uuid4())
    data_ref = extrair_data_do_nome(csv_path)

    logger.info(f"[INIT] CSV:      {csv_path}")
    logger.info(f"[INIT] Data:     {data_ref}")
    logger.info(f"[INIT] Run ID:   {run_id}")
    logger.info(f"[INIT] Início:   {datetime.now().isoformat()}")

    df = pd.read_csv(csv_path, sep=";", encoding="utf-8-sig", dtype=str)
    csv_total = len(df)
    logger.info(f"[CSV] {csv_total} linhas | Colunas: {list(df.columns)}")

    df = normalizar_colunas(df)
    df = converter_tipos(df, data_ref)
    df["run_id"] = run_id

    if "produto" in df.columns:
        antes = len(df)
        df = df[df["produto"].notna() & (df["produto"].astype(str).str.strip() != "")]
        if len(df) < antes:
            logger.warning(f"[FILTRO] {antes - len(df)} sem produto removida(s).")

    df = filtro_rac(df)
    total = len(df)
    logger.info(f"[UPLOAD] {total} registros válidos.")

    cols_ok  = [c for c in list(SCHEMA.keys()) + ["run_id"] if c in df.columns]
    cols_out = [c for c in SCHEMA if c not in df.columns]
    if cols_out:
        logger.warning(f"[UPLOAD] Colunas omitidas: {cols_out}")
    df = df[cols_ok]

    records = limpar_nan(df.to_dict(orient="records"))

    # Amostra de validação antes de enviar
    if records:
        s = records[0]
        logger.info(
            f"[AMOSTRA] data={s.get('data')} plataforma={s.get('plataforma')} "
            f"keyword={s.get('keyword')} run_id={s.get('run_id')} "
            f"fulfillment={s.get('fulfillment')} preco={s.get('preco')}"
        )

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.info("[Supabase] Conectado.")

    ins_t, ign_t, err_t = 0, 0, 0
    n_lotes = (total + CHUNK_SIZE - 1) // CHUNK_SIZE

    for i, inicio in enumerate(range(0, total, CHUNK_SIZE), start=1):
        lote = records[inicio: inicio + CHUNK_SIZE]
        ins, ign, err = inserir_lote(sb, lote, i, n_lotes)
        ins_t += ins; ign_t += ign; err_t += err

    logger.info("=" * 60)
    logger.info(f"AUDITORIA FINAL — Run {run_id}")
    logger.info("=" * 60)
    logger.info(f"CSV lido:          {csv_total}")
    logger.info(f"Após filtros:      {total}")
    logger.info(f"Inseridas:         {ins_t}")
    logger.info(f"Ignoradas (dupl):  {ign_t}")
    logger.info(f"Erros reais:       {err_t}")
    diff = total - ins_t - ign_t - err_t
    if err_t > 0 or diff != 0:
        logger.error(f"[WARN] Discrepância! Diff={diff} Erros={err_t}")
    else:
        logger.success("Upload concluído sem discrepâncias. ✓")
    logger.info("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python reenviar_csv.py <caminho_do_csv>")
        sys.exit(1)
    reenviar(sys.argv[1])
