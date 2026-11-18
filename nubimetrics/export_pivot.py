"""Leitor dos exports XLSX dos relatórios dinâmicos da Nubimetrics (Flexmonster).

Por que existe: o MCP da Nubimetrics NÃO serve o relatório por publicações ×
dia (``/company/itemreport``) — só ranking de mês fechado e uma foto do
explorador (``catalog/MAPPING.md`` §4). A série DIÁRIA por marca/seller/anúncio
só sai do botão **Exportar** dos templates salvos ("RAC | Top anuncios por dia",
"RAC | Diário por Marca" etc.).

Formato do export (conferido nos 5 templates em 17/11/2026)::

    linha 1   ''                  | 'Filtros'
    linha 2   'Categoria_Nivel_3' | 'Ares Condicionados'        ← filtros do relatório
    linha 3   (vazia)
    linha 4   <campo nível 0>     | 'Mes'                       ← campos de linha, um por
    linha 5   <campo nível 1>     | '01/11/2026' | '' | '02/11/2026' | …   linha, de cima
    linha 6   <campo nível 2>     | 'Soma de Unidades_Vendidas' | 'Soma de Monto_…'
    linha 7+  rótulo (indentado)  | valores…
    última    'Total geral'       | totais por dia

A hierarquia das linhas (título → marca → seller) está SÓ na indentação da
célula da coluna A (``alignment.indent`` 0, 1, 2…): não há coluna por nível.
Por isso o leitor usa ``openpyxl`` com estilos, nunca ``pandas.read_excel``
(que achataria os níveis num rótulo só e misturaria título com seller).

Regras:
- **falha alto** se o layout mudar (sem 'Mes', sem medida reconhecida, nível
  sem campo) — um export lido errado devolveria um recorte plausível e errado;
- só as **folhas** (nível mais fundo) viram linhas; os níveis acima são
  subtotais e servem para conferir (``reconciliar``);
- a coluna "Totais" é descartada (é soma das datas).

Módulo puro (sem rede, sem Streamlit) — ``tests/test_nubimetrics_export.py``.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd

#: Medidas do Flexmonster → coluna do módulo. Prefixos de agregação
#: ("Soma de ", "Soma total de ") são removidos antes do de-para.
MEDIDAS: Dict[str, str] = {
    "Unidades_Vendidas": "unidades",
    "Monto_Vendido_Moneda_Local": "gmv_brl",
    "Monto_Vendido_USD": "gmv_usd",
    "Cancelaciones_Unidades": "cancel_unidades",
    "Cancelaciones_Monto_Moneda_Local": "cancel_brl",
}

#: Campo de linha do Flexmonster → coluna do módulo.
CAMPOS: Dict[str, str] = {
    "Titulo_Publicacion": "titulo",
    "Marca": "marca",
    "Nickname_Vendedor": "seller",
    "Codigo_de_Publicacion": "publication_id",
    "Codigo_Vendedor": "seller_id",
    "Tipo_Vendedor": "seller_tipo",
    "Nombre_Tienda_Oficial": "loja_oficial",
    # Relatório de atributos (/company/attributereport) — só dimensão de produto.
    "tipo de ar condicionado": "ac_type_raw",
    "com tecnologia inverter": "inverter_raw",
    "capacidade de refrigeração": "btu_raw",
    "capacidade de refrigeracao": "btu_raw",
}

#: Colunas de atributo que o importador entende.
ATRIBUTOS = ("ac_type_raw", "inverter_raw", "btu_raw")

_AGREGACAO = re.compile(r"^(soma( total)? de|sum of|total sum of)\s+", re.I)
_DATA = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_TOTAL_GERAL = re.compile(r"^(total geral|grand total|total general)$", re.I)


class ExportLayoutError(ValueError):
    """O arquivo não tem o layout esperado de um export da Nubimetrics."""


@dataclass
class PivotExport:
    """Conteúdo de um export, já em formato longo."""

    arquivo: str
    #: Campos de linha, do nível 0 ao mais fundo, já com o nome do módulo.
    niveis: List[str]
    #: Filtros do relatório (ex.: ``{'Categoria_Nivel_3': 'Ares Condicionados'}``).
    filtros: Dict[str, str]
    #: Uma linha por folha × dia com valor: colunas = níveis + ``data`` + medidas.
    linhas: pd.DataFrame
    #: Linha "Total geral" por dia: ``data`` + medidas.
    total_geral: pd.DataFrame
    #: Subtotais do nível 0 por dia (para reconciliação).
    subtotais_n0: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def medidas(self) -> List[str]:
        return [c for c in self.total_geral.columns if c != "data"]


def _texto(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _medida(rotulo: str) -> Optional[str]:
    nome = _AGREGACAO.sub("", rotulo.strip())
    return MEDIDAS.get(nome)


def _campo(rotulo: str) -> str:
    rotulo = rotulo.strip()
    if rotulo.lower() in CAMPOS:
        return CAMPOS[rotulo.lower()]
    return CAMPOS.get(rotulo, re.sub(r"\W+", "_", rotulo.strip()).strip("_").lower())


def _indent(cell: Any) -> int:
    try:
        return int(round(float(cell.alignment.indent or 0)))
    except (AttributeError, TypeError, ValueError):
        return 0


def _numero(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(".", "").replace(",", ".") if "," in str(v) else str(v).strip()
    try:
        return float(s)
    except ValueError:
        return None


def ler_export(caminho: Union[str, Path]) -> PivotExport:
    """Lê um export XLSX da Nubimetrics e devolve as folhas em formato longo.

    Raises:
        ExportLayoutError: layout irreconhecível (o importador não grava nada).
    """
    import openpyxl  # dependência do pandas[excel]; import tardio p/ o app não pagar

    caminho = Path(caminho)
    try:
        wb = openpyxl.load_workbook(caminho, data_only=True)  # estilos: indent = nível
    except Exception as exc:  # noqa: BLE001 — arquivo corrompido/não-xlsx
        raise ExportLayoutError(f"{caminho.name}: não abriu como XLSX ({exc})") from exc
    ws = wb.worksheets[0]
    linhas = list(ws.iter_rows())
    if not linhas:
        raise ExportLayoutError(f"{caminho.name}: planilha vazia")

    # 1) Linha 'Mes' (cabeçalho das colunas) e linha das medidas logo abaixo das datas.
    i_mes = next((i for i, r in enumerate(linhas[:30]) if len(r) > 1 and _texto(r[1].value) == "Mes"), None)
    if i_mes is None:
        raise ExportLayoutError(f"{caminho.name}: sem a coluna 'Mes' — o template precisa ter "
                                "o dia (campo Mes) nas COLUNAS")
    i_datas = i_mes + 1
    i_medidas = next((i for i in range(i_datas + 1, min(i_datas + 4, len(linhas)))
                      if any(_medida(_texto(c.value)) for c in linhas[i][1:])), None)
    if i_medidas is None:
        raise ExportLayoutError(f"{caminho.name}: nenhuma medida reconhecida "
                                f"(esperado uma de {sorted(MEDIDAS)})")

    # 2) Filtros (entre 'Filtros' e o cabeçalho).
    filtros: Dict[str, str] = {}
    for r in linhas[:i_mes]:
        k, v = _texto(r[0].value), _texto(r[1].value) if len(r) > 1 else ""
        if k and v and v != "Filtros":
            filtros[k] = v

    # 3) Campos de linha: coluna A do cabeçalho, de cima para baixo.
    niveis = [_campo(_texto(linhas[i][0].value)) for i in range(i_mes, i_medidas + 1)
              if _texto(linhas[i][0].value)]
    if not niveis:
        raise ExportLayoutError(f"{caminho.name}: sem campo de linha (Marca, Seller, Título…)")

    # 4) Colunas: data (propagada sobre as células '') × medida. 'Totais' fica de fora.
    colunas: Dict[int, Tuple[dt.date, str]] = {}
    data_atual: Optional[dt.date] = None
    for j, c in enumerate(linhas[i_datas]):
        if j == 0:
            continue
        t = _texto(c.value)
        if t:
            data_atual = dt.datetime.strptime(t, "%d/%m/%Y").date() if _DATA.match(t) else None
        med = _medida(_texto(linhas[i_medidas][j].value)) if j < len(linhas[i_medidas]) else None
        if data_atual is not None and med:
            colunas[j] = (data_atual, med)
    if not colunas:
        raise ExportLayoutError(f"{caminho.name}: nenhuma coluna de dia dd/mm/aaaa")
    medidas = sorted({m for _, m in colunas.values()}, key=list(MEDIDAS.values()).index)

    # 5) Linhas: caminho hierárquico pela indentação; só folhas viram registro.
    profundo = len(niveis) - 1
    caminho_atual: List[Optional[str]] = [None] * len(niveis)
    folhas: List[Dict[str, Any]] = []
    n0: List[Dict[str, Any]] = []
    total: List[Dict[str, Any]] = []

    def _valores(r: Tuple[Any, ...]) -> Dict[dt.date, Dict[str, float]]:
        out: Dict[dt.date, Dict[str, float]] = {}
        for j, (d, m) in colunas.items():
            v = _numero(r[j].value) if j < len(r) else None
            if v is not None:
                out.setdefault(d, {})[m] = v
        return out

    for r in linhas[i_medidas + 1:]:
        rotulo = _texto(r[0].value)
        if not rotulo:
            continue
        if _TOTAL_GERAL.match(rotulo) and _indent(r[0]) == 0:
            total = [{"data": d, **v} for d, v in _valores(r).items()]
            continue
        nivel = _indent(r[0])
        if nivel > profundo:
            raise ExportLayoutError(f"{caminho.name}: linha '{rotulo[:40]}' com nível {nivel}, "
                                    f"mas o relatório só tem {len(niveis)} campo(s) de linha")
        caminho_atual[nivel] = rotulo
        for k in range(nivel + 1, len(niveis)):
            caminho_atual[k] = None
        if nivel == 0:
            n0.extend({niveis[0]: rotulo, "data": d, **v} for d, v in _valores(r).items())
        if nivel == profundo:
            if any(p is None for p in caminho_atual):
                raise ExportLayoutError(f"{caminho.name}: folha '{rotulo[:40]}' sem pai")
            base = dict(zip(niveis, caminho_atual))
            folhas.extend({**base, "data": d, **v} for d, v in _valores(r).items())

    if not total:
        raise ExportLayoutError(f"{caminho.name}: sem a linha 'Total geral' — export truncado?")

    def _df(regs: List[Dict[str, Any]], chaves: List[str]) -> pd.DataFrame:
        df = pd.DataFrame(regs, columns=chaves + ["data"] + medidas)
        for m in medidas:
            df[m] = pd.to_numeric(df[m], errors="coerce").fillna(0.0)
        return df

    return PivotExport(
        arquivo=caminho.name,
        niveis=niveis,
        filtros=filtros,
        linhas=_df(folhas, niveis),
        total_geral=_df(total, []),
        subtotais_n0=_df(n0, niveis[:1]),
    )


def reconciliar(exp: PivotExport, tolerancia: float = 0.005) -> List[str]:
    """Confere folhas × 'Total geral' por dia e medida. Devolve as divergências.

    Lista vazia = o export foi lido inteiro. A Nubimetrics arredonda o GMV
    por célula, daí a tolerância relativa (0,5%) em vez de igualdade exata.
    """
    problemas: List[str] = []
    soma = exp.linhas.groupby("data")[exp.medidas].sum()
    tot = exp.total_geral.set_index("data")[exp.medidas]
    for d in tot.index:
        for m in exp.medidas:
            esperado = float(tot.at[d, m])
            obtido = float(soma.at[d, m]) if d in soma.index else 0.0
            if abs(obtido - esperado) > max(1.0, abs(esperado) * tolerancia):
                problemas.append(f"{exp.arquivo} {d:%d/%m}: {m} folhas={obtido:,.0f} × total={esperado:,.0f}")
    return problemas


def ler_atributos(caminho: Union[str, Path]) -> pd.DataFrame:
    """Export do relatório de ATRIBUTOS → uma linha por (título, marca).

    Aceita dois formatos:
    - o pivot do Flexmonster com título (e marca) e os atributos como campos
      de LINHA ("tipo de ar condicionado", "com tecnologia inverter",
      "capacidade de refrigeração"). As colunas de data são ignoradas: neste
      relatório o ``Mes`` NÃO é a data da venda (concentra o mês no dia do
      snapshot) — só serve de peso para escolher o valor mais vendido;
    - uma tabela plana (CSV/XLSX, cabeçalho com ``Titulo_Publicacion`` e os
      atributos).

    Atributo divergente para o mesmo título (anúncios diferentes com o mesmo
    nome) fica com o valor de mais unidades.
    """
    caminho = Path(caminho)
    try:
        exp = ler_export(caminho)
        df = exp.linhas
        peso = df["unidades"] if "unidades" in df else pd.Series(1.0, index=df.index)
        df = df.assign(_peso=peso)
    except ExportLayoutError:
        df = _tabela_plana(caminho)
        df["_peso"] = pd.to_numeric(df.get("unidades", 1.0), errors="coerce").fillna(1.0)
    if "titulo" not in df:
        raise ExportLayoutError(f"{caminho.name}: atributos sem Titulo_Publicacion — sem como "
                                "ligar ao export diário (que agrupa por título)")
    attrs = [a for a in ATRIBUTOS if a in df]
    if not attrs:
        raise ExportLayoutError(f"{caminho.name}: nenhum atributo reconhecido "
                                "(tipo de ar condicionado / com tecnologia inverter / capacidade)")
    chaves = ["titulo"] + (["marca"] if "marca" in df else [])
    for a in attrs:
        df[a] = df[a].map(lambda v: None if v is None or str(v).strip() in ("", "-", "nan") else str(v).strip())
    g = df.groupby(chaves + attrs, dropna=False)["_peso"].sum().reset_index()
    g = g.sort_values("_peso", ascending=False).drop_duplicates(chaves)
    return g.drop(columns="_peso").reset_index(drop=True)


def _tabela_plana(caminho: Path) -> pd.DataFrame:
    if caminho.suffix.lower() == ".csv":
        bruto = pd.read_csv(caminho, sep=None, engine="python", header=None, dtype=str)
    else:
        bruto = pd.read_excel(caminho, header=None, dtype=str)
    i_cab = next((i for i, r in bruto.head(30).iterrows()
                  if any(_texto(v) in ("Titulo_Publicacion", "Codigo_de_Publicacion") for v in r)), None)
    if i_cab is None:
        raise ExportLayoutError(f"{caminho.name}: nem pivot nem tabela com Titulo_Publicacion")
    cab = [_campo(_medida_ou_texto(v)) for v in bruto.iloc[i_cab]]
    df = bruto.iloc[i_cab + 1:].copy()
    df.columns = cab
    return df.reset_index(drop=True)


def _medida_ou_texto(v: Any) -> str:
    t = _texto(v)
    return _medida(t) or t
