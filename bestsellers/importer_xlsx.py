"""
bestsellers/importer_xlsx.py — Ponte com a coleta manual antiga.

A rotina de mais vendidos nasceu manual: um operador exportava cada lista pelo
Web Scraper (webscraper.io) em `<varejista>-AAAA-MM-DD.xlsx`. Este módulo lê
esses arquivos e os converte para o mesmo formato da coleta automática, para
que o histórico já registrado não seja jogado fora quando a automação assumir.

O parser é **schema-agnóstico de propósito**: o Web Scraper renomeia e
reordena as colunas entre execuções (`data`, `data2`, `price`…). Em 08/08/2026
o arquivo da Amazon tinha 9 colunas com o rating em `data4`; em 10/08 tinha 11
e o rating passou para `data5` — e o nome do arquivo mudou de `amazon-` para
`amazon-com-br-`. Por isso cada campo é detectado por CONTEÚDO, nunca por
posição, e o varejista é casado por PREFIXO.
"""

import glob
import os
import re
from typing import Any, Dict, List, Optional

import pandas as pd
from loguru import logger

from bestsellers.config import SOURCES
from bestsellers.models import BestSellerItem
from bestsellers.storage import to_dataframe

# Prefixo do nome do arquivo → chave da fonte. Casamento por prefixo porque o
# Web Scraper muda o sufixo do domínio entre execuções.
_PREFIXOS = {
    "amazon": "amazon",
    "mercadolivre": "mercadolivre",
    "mercado-livre": "mercadolivre",
    "magazineluiza": "magazineluiza",
    "magalu": "magazineluiza",
    "shopee": "shopee",
    "leroymerlin": "leroymerlin",
    "leroy": "leroymerlin",
    "casasbahia": "casasbahia",
}

_RE_DINHEIRO = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})")
_RE_ESTRELAS = re.compile(r"([\d,.]+)\s*de\s*5\s*estrelas")
# O separador de milhar precisa entrar na captura: `(\d+)` sozinho lê
# "1.226 avaliações" como 226 e o número sai 5× menor sem erro nenhum.
_RE_AVALIACOES = re.compile(r"([\d.]+)\s*avalia")
_RE_VENDIDOS = re.compile(r"([\d.,]+)\s*(mil)?\+?\s*[Vv]endid")
_RE_POR_MES = re.compile(r"[Vv]endido\s*/\s*[Mm]ês")

# Faixa plausível de preço de um ar condicionado no varejo brasileiro. Serve
# para escolher a coluna de preço entre várias candidatas numéricas.
_PRECO_MIN, _PRECO_MAX = 700.0, 12_000.0


def _para_dinheiro(valor: Any) -> Optional[float]:
    """
    Converte texto brasileiro em float, sem inventar.

    Retorna None quando não há padrão monetário inequívoco — é o que impede
    ler um rating "4.9" como R$ 4.900.
    """
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    texto = str(valor).strip()

    achado = _RE_DINHEIRO.search(texto)          # 1.234,56 / 234,56
    if achado:
        return float(achado.group(1).replace(".", "").replace(",", "."))
    achado = re.search(r"R\$\s*(\d{1,3}(?:\.\d{3})+)\b", texto)  # R$ 1.649
    if achado:
        return float(achado.group(1).replace(".", ""))
    if re.fullmatch(r"\d{1,3}\.\d{3}", texto):   # 1.649 = separador de milhar
        return float(texto.replace(".", ""))
    if re.fullmatch(r"\d{3,6}", texto):
        return float(texto)
    return None


def _colunas_uteis(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if not str(c).startswith(("image", "web_scraper"))]


_RE_URL = re.compile(r"^\s*(?:https?://|/|www\.)", re.I)


def _escolher_titulo(df: pd.DataFrame) -> Optional[str]:
    """
    Coluna com maior densidade de vocabulário de produto.

    O vocabulário domina a nota e o comprimento entra só como desempate
    LIMITADO: com um termo de comprimento livre, a coluna de href (que também
    contém "ar-condicionado" no slug e é longuíssima) vencia a coluna de
    título, e todo `titulo` importava como URL.
    """
    melhor, melhor_nota = None, -1.0
    for coluna in _colunas_uteis(df):
        serie = df[coluna].dropna().astype(str)
        if not len(serie):
            continue
        # Coluna de link não é título, por mais que o slug pareça um.
        if serie.str.contains(_RE_URL).mean() > 0.5:
            continue
        acertos = serie.str.upper().str.contains(
            "AR CONDICIONADO|AR-CONDICIONADO|SPLIT|UMIDIFICADOR|DEPURADOR"
        ).mean()
        # Desempate por comprimento, no máximo 10 pontos — nunca o suficiente
        # para superar a diferença de vocabulário.
        nota = acertos * 100 + min(serie.str.len().mean(), 100) / 10
        if nota > melhor_nota:
            melhor, melhor_nota = coluna, nota
    return melhor


# Evidência de que a célula é dinheiro, e não um número qualquer: símbolo da
# moeda ou centavos com vírgula.
_RE_EVIDENCIA_MONETARIA = re.compile(r"R\$|\d,\d{2}\b")


def _escolher_preco(df: pd.DataFrame, titulo: Optional[str]):
    """
    (coluna, série) do preço praticado.

    Duas decisões que parecem detalhe e não são:

      * candidatas COM evidência monetária (R$ ou centavos) vencem as sem
        evidência. Sem isso, uma coluna de capacidade [12000, 9000, 3000] cai
        inteira dentro da faixa plausível de preço, ganha no critério de
        cobertura e vira `preco` — silenciosamente;
      * empate de cobertura é resolvido pela MENOR mediana, porque o alvo é o
        preço "por", não o "de" riscado.
    """
    candidatas = []
    for coluna in _colunas_uteis(df):
        if coluna == titulo:
            continue
        textos = df[coluna].dropna().astype(str)
        evidencia = float(textos.str.contains(_RE_EVIDENCIA_MONETARIA).mean()) if len(textos) else 0.0
        valores = df[coluna].apply(_para_dinheiro)
        cobertura = valores.between(_PRECO_MIN, _PRECO_MAX).mean()
        if cobertura >= 0.5:
            mediana = valores[valores.between(_PRECO_MIN, _PRECO_MAX)].median()
            candidatas.append((coluna, valores, cobertura, mediana, evidencia > 0.5))

    if not candidatas:
        return None, None

    monetarias = [c for c in candidatas if c[4]]
    candidatas = monetarias or candidatas

    melhor_cobertura = max(c[2] for c in candidatas)
    finalistas = sorted(
        [c for c in candidatas if c[2] >= melhor_cobertura - 0.05], key=lambda c: c[3]
    )
    return finalistas[0][0], finalistas[0][1]


def _escolher_rating(df: pd.DataFrame) -> Optional[pd.Series]:
    for coluna in _colunas_uteis(df):
        serie = df[coluna].dropna().astype(str)
        if not len(serie):
            continue
        if serie.str.contains("de 5 estrelas").mean() > 0.5:
            extraido = df[coluna].astype(str).str.extract(_RE_ESTRELAS)[0]
            return pd.to_numeric(extraido.str.replace(",", "."), errors="coerce")
        numerico = pd.to_numeric(df[coluna], errors="coerce")
        if numerico.between(3.0, 5.0).mean() > 0.7:
            return numerico
    return None


def _escolher_reviews(df: pd.DataFrame) -> Optional[pd.Series]:
    for coluna in _colunas_uteis(df):
        serie = df[coluna].dropna().astype(str)
        if not len(serie):
            continue
        if serie.str.contains("avalia").mean() > 0.5:
            extraido = df[coluna].astype(str).str.extract(_RE_AVALIACOES)[0]
            return pd.to_numeric(extraido.str.replace(".", "", regex=False), errors="coerce")
        # Magalu junta nota e contagem: "4.7 (303)".
        if serie.str.match(r"^\d[.,]?\d*\s*\(\d+\)$").mean() > 0.5:
            return pd.to_numeric(
                df[coluna].astype(str).str.extract(r"\((\d+)\)")[0], errors="coerce"
            )
        if serie.str.match(r"^\(\d+\)$").mean() > 0.5:
            return pd.to_numeric(
                df[coluna].astype(str).str.extract(r"\((\d+)\)")[0], errors="coerce"
            )
    return None


def _escolher_vendidos(df: pd.DataFrame):
    """(série numérica, base) da coluna de volume, ou (None, None)."""
    for coluna in _colunas_uteis(df):
        serie = df[coluna].dropna().astype(str)
        if not len(serie) or serie.str.contains("endid").mean() <= 0.5:
            continue
        texto = df[coluna].astype(str)
        base = "mes" if texto.str.contains(_RE_POR_MES).any() else "acumulado"

        def _numero(valor: str) -> Optional[float]:
            achado = _RE_VENDIDOS.search(str(valor))
            if not achado:
                return None
            quantidade = float(achado.group(1).replace(".", "").replace(",", "."))
            return quantidade * 1000 if achado.group(2) else quantidade

        return texto.apply(_numero), base
    return None, None


def _detectar_fonte(nome_arquivo: str) -> Optional[str]:
    """Chave da fonte a partir do nome do arquivo, casando por prefixo."""
    base = os.path.basename(nome_arquivo).lower()
    base = re.sub(r"-\d{4}-\d{2}-\d{2}\.xlsx$", "", base)
    base = base.replace("-com-br", "").replace(".com.br", "").replace("-", "")
    for prefixo, chave in _PREFIXOS.items():
        if base.startswith(prefixo.replace("-", "")):
            return chave
    return None


def importar_arquivo(caminho: str, data: str) -> pd.DataFrame:
    """
    Converte um .xlsx do Web Scraper para o formato da série.

    Args:
        caminho: caminho do arquivo.
        data:    data da coleta (YYYY-MM-DD) — o Web Scraper não a grava.

    Returns:
        DataFrame no formato de `models.COLUNAS`. Vazio quando o varejista não
        é reconhecido.
    """
    chave = _detectar_fonte(caminho)
    if chave is None or chave not in SOURCES:
        logger.warning(
            f"[Import] Varejista não reconhecido em {os.path.basename(caminho)} "
            "— arquivo ignorado."
        )
        return pd.DataFrame()

    spec = SOURCES[chave]
    df = pd.read_excel(caminho)
    if not len(df):
        return pd.DataFrame()

    url = ""
    if "web_scraper_start_url" in df.columns:
        urls = df["web_scraper_start_url"].dropna()
        url = str(urls.iloc[0]) if len(urls) else ""

    coluna_titulo = _escolher_titulo(df)
    _, precos = _escolher_preco(df, coluna_titulo)
    ratings = _escolher_rating(df)
    reviews = _escolher_reviews(df)
    vendidos, base_vendidos = _escolher_vendidos(df)

    # Posição: o sufixo de web_scraper_order preserva a ordem do DOM e é a
    # fonte mais estável; um "#N" explícito na página vence quando existir.
    ranks = pd.Series(range(1, len(df) + 1), dtype="float64")
    if "web_scraper_order" in df.columns:
        extraido = df["web_scraper_order"].astype(str).str.extract(r"-(\d+)$")[0]
        numerico = pd.to_numeric(extraido, errors="coerce")
        if numerico.notna().all():
            ranks = numerico.rank(method="first")
    for coluna in _colunas_uteis(df):
        serie = df[coluna].dropna().astype(str)
        if len(serie) > 3 and serie.str.match(r"^#\d+$").mean() > 0.8:
            ranks = pd.to_numeric(
                df[coluna].astype(str).str.extract(r"(\d+)")[0], errors="coerce"
            )
            break

    linhas: List[Dict[str, Any]] = []
    for posicao in range(len(df)):
        titulo = df[coluna_titulo].iloc[posicao] if coluna_titulo else None
        if not isinstance(titulo, str) or not titulo.strip():
            continue
        rank = ranks.iloc[posicao]
        item = BestSellerItem(
            rank=int(rank) if pd.notna(rank) else posicao + 1,
            titulo=titulo.strip(),
            preco=float(precos.iloc[posicao]) if precos is not None and pd.notna(precos.iloc[posicao]) else None,
            rating=float(ratings.iloc[posicao]) if ratings is not None and pd.notna(ratings.iloc[posicao]) else None,
            reviews=int(reviews.iloc[posicao]) if reviews is not None and pd.notna(reviews.iloc[posicao]) else None,
            vendidos=float(vendidos.iloc[posicao]) if vendidos is not None and pd.notna(vendidos.iloc[posicao]) else None,
            base_vendidos=base_vendidos,
        )
        linhas.append(item.to_row(
            data=data,
            horario="",
            run_id=None,
            spec=spec,
            url_coleta=url or spec.url_publica,
            # O import legado prova a ordenação pela URL registrada no
            # próprio export — é ela que o portão de validação inspeciona.
            endpoint=url or spec.url_publica,
        ))

    logger.info(
        f"[Import] {os.path.basename(caminho)} → {spec.nome}: {len(linhas)} posições"
    )
    return to_dataframe(linhas)


def importar_diretorio(diretorio: str, data: str) -> pd.DataFrame:
    """
    Importa todos os `*<data>.xlsx` de um diretório.

    Args:
        diretorio: pasta com os exports do Web Scraper.
        data:      data no nome dos arquivos (YYYY-MM-DD).

    Returns:
        DataFrame concatenado, no formato da série.
    """
    arquivos = sorted(glob.glob(os.path.join(diretorio, f"*{data}.xlsx")))
    if not arquivos:
        logger.warning(f"[Import] Nenhum arquivo *{data}.xlsx em {diretorio}")
        return pd.DataFrame()

    partes = [importar_arquivo(a, data) for a in arquivos]
    partes = [p for p in partes if len(p)]
    if not partes:
        return pd.DataFrame()
    return pd.concat(partes, ignore_index=True)
