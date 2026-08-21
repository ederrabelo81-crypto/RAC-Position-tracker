"""
bestsellers/validate.py — Portões de qualidade da coleta diária.

Rodam ANTES de qualquer análise. Existem porque a série de mais vendidos tem
um modo de falha traiçoeiro: a coleta "funciona", produz linhas, e mede a
coisa errada. Uma lista de relevância parseia igualzinho a uma lista de
vendas — só o número no fim é que fica mentiroso.

| Portão                   | Regra                                    | Ação        |
|--------------------------|------------------------------------------|-------------|
| Ordenação por vendas     | parâmetro canônico ausente no endpoint   | QUARENTENA  |
| Escopo de categoria      | >15% de itens não-RAC                    | AVISO       |
| Volume mínimo            | <10 itens                                | AVISO       |
| Cobertura de campo       | `preco` ou `titulo` <85%                 | AVISO       |
| Posições duplicadas      | `rank` repetido                          | ERRO        |
| Plataforma ausente       | fonte pedida que não produziu linha      | AVISO       |
| URL mudou entre datas    | endpoint diferente da leitura anterior   | AVISO       |
| Dia da semana            | base de comparação com weekday diferente | AVISO       |

QUARENTENA é o único nível que remove dados da análise: sem o parâmetro de
ordenação a lista não é o que dizemos que é, e usá-la contamina a série
inteira. Os demais níveis registram o problema e deixam o número passar com
a ressalva anexada.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from bestsellers.config import (
    FRACAO_MINIMA_DA_LISTA,
    LIMITE_CONTAMINACAO,
    LIMITE_COBERTURA_CAMPO,
    LIMITE_ITENS_MINIMO,
    REFERENCIA_RELEVANCIA,
    SOURCES,
)

NIVEL_QUARENTENA = "QUARENTENA"
NIVEL_ERRO = "ERRO"
NIVEL_AVISO = "AVISO"


def _texto(valor) -> str:
    """
    Célula como string limpa — vazia quando o valor é ausente.

    `str(x or "")` não serve: o NaN do pandas é TRUTHY, então uma coluna vazia
    lida do CSV virava a string "nan". No portão de ordenação isso impedia o
    fallback para `url_coleta` de disparar e mandava uma lista boa para
    quarentena.
    """
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    texto = str(valor).strip()
    return "" if texto.lower() in ("nan", "none", "<na>") else texto


@dataclass
class Ocorrencia:
    """Um achado de validação, com o que fazer a respeito."""

    nivel: str
    plataforma: str
    mensagem: str

    def __str__(self) -> str:
        return f"[{self.nivel}] {self.plataforma}: {self.mensagem}"


def validar(
    hoje: pd.DataFrame,
    *,
    esperadas: Optional[List[str]] = None,
    falhas: Optional[Dict[str, str]] = None,
) -> List[Ocorrencia]:
    """
    Aplica os portões de qualidade sobre a coleta do dia.

    Args:
        hoje:      DataFrame da coleta (colunas de `models.COLUNAS`).
        esperadas: chaves de fonte que deveriam ter sido coletadas. Uma
                   plataforma pedida e ausente vira AVISO — a alternativa é
                   comparar bases desiguais fingindo que são a mesma.
        falhas:    mapa plataforma → motivo da falha, vindo do coletor.

    Returns:
        Lista de ocorrências, ordenada por severidade.
    """
    ocorrencias: List[Ocorrencia] = []
    falhas = falhas or {}
    coletadas = set(hoje["plataforma"].unique()) if len(hoje) else set()

    for plataforma in esperadas or []:
        if plataforma not in coletadas:
            motivo = falhas.get(plataforma, "sem linhas coletadas")
            ocorrencias.append(Ocorrencia(
                NIVEL_AVISO, plataforma,
                f"plataforma ausente da coleta ({motivo}). O relatório registra "
                "a ausência em vez de comparar bases desiguais.",
            ))

    for plataforma, grupo in hoje.groupby("plataforma"):
        ocorrencias.extend(_validar_plataforma(str(plataforma), grupo))

    ordem = {NIVEL_QUARENTENA: 0, NIVEL_ERRO: 1, NIVEL_AVISO: 2}
    return sorted(ocorrencias, key=lambda o: (ordem.get(o.nivel, 9), o.plataforma))


def _validar_plataforma(plataforma: str, grupo: pd.DataFrame) -> List[Ocorrencia]:
    ocorrencias: List[Ocorrencia] = []
    spec = SOURCES.get(plataforma)

    # --- Portão 1: a lista é mesmo de mais vendidos? ---------------------
    if spec is None:
        ocorrencias.append(Ocorrencia(
            NIVEL_AVISO, plataforma,
            "plataforma não registrada em bestsellers/config.py — mecânica de "
            "ordenação desconhecida, número não interpretável.",
        ))
    else:
        # A prova é o que foi REALMENTE chamado (`endpoint`). `url_coleta` é
        # a URL declarada no registro — ela sempre contém o parâmetro, então
        # aceitá-la como evidência tornaria o portão incapaz de reprovar
        # qualquer coisa. Só serve de fallback quando não há endpoint (import
        # do histórico manual, onde a URL veio do próprio export).
        alvo = _texto(grupo["endpoint"].iloc[0]) or _texto(grupo["url_coleta"].iloc[0])
        if not spec.ordenacao_comprovada(alvo):
            # A mensagem depende da REFERÊNCIA declarada da fonte: uma lista de
            # mais vendidos sem o sort por vendas provavelmente virou
            # relevância (a falha clássica); uma fonte de referência RELEVANCIA
            # que perde seu próprio âncora de ordenação também vira população
            # incerta. Nos dois casos o dado sai da análise — mas as duas
            # referências continuam em séries separadas, jamais comparadas.
            if spec.referencia == REFERENCIA_RELEVANCIA:
                motivo = (
                    f'âncora de relevância "{spec.parametro_ordenacao}" ausente '
                    "do endpoint coletado — não há prova de que a lista é a "
                    "ordem de relevância pretendida. Descartada da análise "
                    "(referência: relevância)."
                )
            else:
                motivo = (
                    f'ordenação "{spec.parametro_ordenacao}" ausente do endpoint '
                    "coletado. A lista NÃO é de mais vendidos (provavelmente "
                    "relevância) — descartada da análise."
                )
            ocorrencias.append(Ocorrencia(NIVEL_QUARENTENA, plataforma, motivo))

    total = len(grupo)

    # --- Portão 2: volume ------------------------------------------------
    # Dois pisos: um absoluto (nenhuma lista útil tem menos de 10 posições) e
    # um relativo ao tamanho declarado da lista. Sem o relativo, uma Amazon
    # que devolve 12 de 30 passa silenciosamente e seu `pct_topN` acaba
    # comparado com dias de lista cheia — bases diferentes, delta sem sentido.
    piso_relativo = (
        int(spec.itens_esperados * FRACAO_MINIMA_DA_LISTA) if spec else 0
    )
    piso = max(LIMITE_ITENS_MINIMO, piso_relativo)
    if total < piso:
        esperado = f"{spec.itens_esperados} esperados" if spec else "n/d"
        ocorrencias.append(Ocorrencia(
            NIVEL_AVISO, plataforma,
            f"apenas {total} itens capturados (mínimo aceitável: {piso}; "
            f"{esperado}) — coleta possivelmente truncada. Comparar este dia "
            "com um de lista cheia mistura bases de tamanho diferente.",
        ))

    # --- Portão 3: contaminação de categoria -----------------------------
    fora = int((~grupo["no_escopo"].astype(bool)).sum())
    if total and fora / total > LIMITE_CONTAMINACAO:
        ocorrencias.append(Ocorrencia(
            NIVEL_AVISO, plataforma,
            f"{fora} de {total} itens fora do escopo RAC ({fora / total:.0%}) — "
            "categoria contaminada (umidificador/depurador/ventilador). Os "
            "itens ficam na base mas o KPI os ignora.",
        ))

    # --- Portão 4: cobertura dos campos ----------------------------------
    for campo in ("titulo", "preco"):
        if campo not in grupo.columns or not total:
            continue
        cobertura = float(grupo[campo].notna().mean())
        if cobertura < LIMITE_COBERTURA_CAMPO:
            ocorrencias.append(Ocorrencia(
                NIVEL_AVISO, plataforma,
                f"campo `{campo}` com {cobertura:.0%} de cobertura — o parser "
                "provavelmente perdeu o seletor/chave desse campo.",
            ))

    # --- Portão 5: integridade do ranking --------------------------------
    if grupo["rank"].duplicated().any():
        ocorrencias.append(Ocorrencia(
            NIVEL_ERRO, plataforma,
            "posições duplicadas no ranking — a lista não é uma ordenação "
            "válida e o pareamento com outras datas fica sem sentido.",
        ))

    return ocorrencias


def plataformas_em_quarentena(ocorrencias: List[Ocorrencia]) -> List[str]:
    """Chaves das plataformas que devem sair da análise."""
    return sorted({o.plataforma for o in ocorrencias if o.nivel == NIVEL_QUARENTENA})


def aplicar_quarentena(
    df: pd.DataFrame, ocorrencias: List[Ocorrencia]
) -> pd.DataFrame:
    """Remove do DataFrame as plataformas reprovadas no portão de ordenação."""
    quarentena = plataformas_em_quarentena(ocorrencias)
    if not quarentena or not len(df):
        return df
    return df[~df["plataforma"].isin(quarentena)].copy()


def comparar_endpoints(hoje: pd.DataFrame, anterior: pd.DataFrame) -> List[Ocorrencia]:
    """
    Detecta plataformas cujo endpoint mudou entre as duas datas.

    Duas URLs diferentes são duas POPULAÇÕES diferentes. Comparar preço ou
    posição nesse caso produz um delta que não é movimento competitivo — é
    troca de amostra. Estas plataformas ficam fora dos deltas.
    """
    ocorrencias: List[Ocorrencia] = []
    if not len(hoje) or not len(anterior):
        return ocorrencias

    for plataforma in sorted(set(hoje["plataforma"]) & set(anterior["plataforma"])):
        atual = _texto(hoje[hoje["plataforma"] == plataforma]["endpoint"].iloc[0])
        antes = _texto(anterior[anterior["plataforma"] == plataforma]["endpoint"].iloc[0])
        if atual != antes:
            ocorrencias.append(Ocorrencia(
                NIVEL_AVISO, plataforma,
                "endpoint de coleta mudou desde a leitura anterior — populações "
                "diferentes. Delta de preço e de posição desta plataforma fica "
                "excluído.",
            ))
    return ocorrencias
