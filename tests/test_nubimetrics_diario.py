"""Sell-out diário Nubimetrics: leitor de export, regra HW Inverter, contas e import.

Sem rede e sem os exports reais (dado licenciado, fora do git): os XLSX são
montados aqui com o MESMO layout do Flexmonster (hierarquia pela indentação
da coluna A). A aceitação contra os números de 11/11/2026 está em
``test_nubimetrics_aceitacao.py`` e roda só com os exports em ``uploads/``.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import List, Sequence, Tuple

import openpyxl
import pandas as pd
import pytest
from openpyxl.styles import Alignment

from nubimetrics import diario as D
from nubimetrics import import_export as IE
from nubimetrics.export_pivot import ExportLayoutError, ler_atributos, ler_export, reconciliar

D1, D2 = dt.date(2026, 11, 10), dt.date(2026, 11, 11)
UN, GMV = "Soma de Unidades_Vendidas", "Soma de Monto_Vendido_Moneda_Local"


def _xlsx(path: Path, campos: Sequence[str], linhas: List[Tuple[int, str, Sequence]],
          dias: Sequence[dt.date] = (D1, D2), total: Sequence = None, filtro: str = "Ares Condicionados") -> Path:
    """Monta um export no layout do Flexmonster. ``linhas``: (nível, rótulo, valores un/gmv por dia)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["", "Filtros"])
    ws.append(["Categoria_Nivel_3", filtro])
    ws.append([None])
    cab = [[c] for c in campos]
    while len(cab) < 3:
        cab.insert(0, [None])
    cab[0] += ["Mes"]
    datas: list = []
    for d in dias:
        datas += [d.strftime("%d/%m/%Y"), ""]
    cab[1] += datas + ["Totais", ""]
    cab[2] += [UN, GMV] * len(dias) + ["Soma total de Unidades_Vendidas", "Soma total de Monto_Vendido_Moneda_Local"]
    for r in cab:
        ws.append(r)
    for nivel, rotulo, vals in linhas:
        ws.append([rotulo, *vals, None, None])
        ws.cell(ws.max_row, 1).alignment = Alignment(indent=nivel)
    ws.append(["Total geral", *(total or [])])
    wb.save(path)
    return path


@pytest.fixture
def fino(tmp_path) -> Path:
    linhas = [
        (0, "Ar Condicionado Split Midea Airvolution 12000 Btus", (10, 25000, 20, 50000)),
        (1, "MIDEA", (10, 25000, 20, 50000)),
        (2, "DUFRIO REFRIGERAÇÃO", (6, 15000, 15, 37500)),
        (2, "lesma.betula.pensativo", (4, 10000, 5, 12500)),
        (0, "Ar Condicionado Portátil LG 12000 Btus", (3, 6000, None, None)),
        (1, "LG", (3, 6000, None, None)),
        (2, "CLIMARIOREFRIGERACAO", (3, 6000, None, None)),
        (0, "Ar Condicionado LG Dual Inverter Voice 9", (2, 4000, 8, 16000)),
        (1, "LG", (2, 4000, 8, 16000)),
        (2, "CLIMARIOREFRIGERACAO", (2, 4000, 8, 16000)),
    ]
    return _xlsx(tmp_path / "top.xlsx", ["Titulo_Publicacion", "Marca", "Nickname_Vendedor"], linhas,
                 total=(15, 35000, 28, 66000))


# ── leitor ──────────────────────────────────────────────────────────────────

def test_le_hierarquia_pela_indentacao(fino):
    e = ler_export(fino)
    assert e.niveis == ["titulo", "marca", "seller"]
    assert e.filtros == {"Categoria_Nivel_3": "Ares Condicionados"}
    # Folhas: 2 sellers Midea × 2 dias + portátil 1 dia + Dual × 2 dias = 7
    assert len(e.linhas) == 7
    dufrio = e.linhas[(e.linhas["seller"] == "DUFRIO REFRIGERAÇÃO") & (e.linhas["data"] == D2)].iloc[0]
    assert dufrio["titulo"].startswith("Ar Condicionado Split Midea") and dufrio["marca"] == "MIDEA"
    assert (dufrio["unidades"], dufrio["gmv_brl"]) == (15, 37500)
    assert reconciliar(e) == []


def test_reconciliar_acusa_folha_faltando(tmp_path):
    p = _xlsx(tmp_path / "x.xlsx", ["Marca"], [(0, "MIDEA", (10, 100, 10, 100))], total=(99, 100, 10, 100))
    erros = reconciliar(ler_export(p))
    assert len(erros) == 1 and "unidades" in erros[0]


def test_layout_errado_falha_alto(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.append(["Marca", "Unidades"])
    wb.save(tmp_path / "plana.xlsx")
    with pytest.raises(ExportLayoutError, match="Mes"):
        ler_export(tmp_path / "plana.xlsx")


def test_sem_total_geral_falha(tmp_path):
    p = _xlsx(tmp_path / "x.xlsx", ["Marca"], [(0, "MIDEA", (1, 1, 1, 1))])
    wb = openpyxl.load_workbook(p)
    ws = wb.active
    ws.delete_rows(ws.max_row)
    wb.save(p)
    with pytest.raises(ExportLayoutError, match="Total geral"):
        ler_export(p)


def test_atributos_por_titulo(tmp_path):
    p = _xlsx(tmp_path / "attr.xlsx", ["Titulo_Publicacion", "com tecnologia inverter", "tipo de ar condicionado"], [
        (0, "Ar Condicionado Split Philco Espelhado 1", (50, 1, 0, 0)),
        (1, "Sim", (50, 1, 0, 0)),
        (2, "Split Hi Wall", (50, 1, 0, 0)),
    ], total=(50, 1, 0, 0))
    a = ler_atributos(p)
    assert a.iloc[0].to_dict() == {"titulo": "Ar Condicionado Split Philco Espelhado 1",
                                   "inverter_raw": "Sim", "ac_type_raw": "Split Hi Wall"}


def test_atributos_layout_do_relatorio_de_atributos(tmp_path):
    """Layout real do export de atributos (18/11/2026): sem filtros, campos de
    linha nas primeiras linhas da coluna A, ``Mes.Month`` e '(blank)'."""
    wb = openpyxl.Workbook()
    ws = wb.active
    cab = [["Titulo_Publicacion", None, None], ["Marca", None, None],
           ["com tecnologia inverter", None, None], ["tipo de ar condicionado", "Mes.Month", None],
           ["capacidade de aquecimento", "11", "Soma total de Unidades_Vendidas"]]
    dados = [(0, "Ar Condicionado Split Philco Espelhado 1", 50), (1, "PHILCO", 50), (2, "Sim", 50),
             (3, "Split", 50), (4, "(blank)", 50),
             (0, "Ar Condicionado Janela Inverter 10000", 7), (1, "GREE", 7), (2, "(blank)", 7),
             (3, "Janela", 7), (4, "(blank)", 7)]
    for i, r in enumerate(cab):
        ws.append(r)
        ws.cell(ws.max_row, 1).alignment = Alignment(indent=i)
    for nivel, rot, v in dados:
        ws.append([rot, v, v])
        ws.cell(ws.max_row, 1).alignment = Alignment(indent=nivel)
    ws.append(["Total geral", 57, 57])
    wb.save(tmp_path / "atributos.xlsx")
    a = ler_atributos(tmp_path / "atributos.xlsx").set_index("titulo")
    assert a.loc["Ar Condicionado Split Philco Espelhado 1", "inverter_raw"] == "Sim"
    assert a.loc["Ar Condicionado Split Philco Espelhado 1", "marca"] == "PHILCO"
    assert a.loc["Ar Condicionado Janela Inverter 10000", "ac_type_raw"] == "Janela"
    assert pd.isna(a.loc["Ar Condicionado Janela Inverter 10000", "inverter_raw"])
    # Na pasta junto dos exports diários, é reconhecido como atributos (não como erro).
    lote = IE.ler_lote([tmp_path / "atributos.xlsx"])
    assert len(lote.atributos) == 1 and not any("Mes" in p for p in lote.problemas)
    # Na pasta E em --atributos: lido uma vez só.
    lote = IE.ler_lote([tmp_path / "atributos.xlsx"], [tmp_path / "atributos.xlsx"])
    assert len(lote.atributos) == 1
    assert [p for p in lote.problemas if "Top anuncios" not in p] == []  # só falta o export fino
    # Atributo ausente chega como NaN do pandas e não quebra a regra.
    assert D.is_hw_inverter("Ar Split Inverter", float("nan"), float("nan")) is True


def _attr_xlsx(tmp_path, cab, dados, nome="a.xlsx"):
    wb = openpyxl.Workbook()
    ws = wb.active
    for i, r in enumerate(cab):
        ws.append(r)
        ws.cell(ws.max_row, 1).alignment = Alignment(indent=i)
    for nivel, rot, *vals in dados:
        ws.append([rot, *vals])
        ws.cell(ws.max_row, 1).alignment = Alignment(indent=nivel)
    wb.save(tmp_path / nome)
    return tmp_path / nome


def test_atributos_folha_sem_pai_falha_alto(tmp_path):
    cab = [["Titulo_Publicacion", None, None], ["com tecnologia inverter", "Mes.Month", None],
           ["tipo de ar condicionado", "11", "Soma total de Unidades_Vendidas"]]
    p = _attr_xlsx(tmp_path, cab, [(0, "T1", 5, 5), (1, "Sim", 5, 5), (2, "Split", 5, 5),
                                   (2, "Janela", 1, 1)])  # ok: herda T1/Sim
    assert len(ler_atributos(p)) == 1
    p2 = _attr_xlsx(tmp_path, cab, [(2, "Split", 5, 5)], nome="b.xlsx")  # folha sem título
    with pytest.raises(ExportLayoutError, match="sem pai"):
        ler_atributos(p2)


def test_atributos_peso_e_unidades_nao_gmv(tmp_path):
    """Mesmo título com dois tipos: vence o de mais UNIDADES, não o de mais GMV."""
    cab = [["Titulo_Publicacion", None, None, None, None],
           ["tipo de ar condicionado", "Mes.Month", None, None, None],
           [None, "11", None, "Totais", None]]
    cab[2] = ["", "Soma de Unidades_Vendidas", "Soma de Monto_Vendido_Moneda_Local",
              "Soma total de Unidades_Vendidas", "Soma total de Monto_Vendido_Moneda_Local"]
    dados = [(0, "Ar Split Inverter 12000", 0, 0, 0, 0),
             (1, "Split", 100, 1000, 100, 1000),        # mais unidades
             (1, "Janela", 10, 900000, 10, 900000)]     # mais GMV
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in cab[:2]:
        ws.append(r)
    ws.append(cab[2])
    for nivel, rot, *vals in dados:
        ws.append([rot, *vals])
        ws.cell(ws.max_row, 1).alignment = Alignment(indent=nivel)
    ws.cell(1, 1).alignment = Alignment(indent=0)
    ws.cell(2, 1).alignment = Alignment(indent=1)
    wb.save(tmp_path / "g.xlsx")
    a = ler_atributos(tmp_path / "g.xlsx")
    assert a.iloc[0]["ac_type_raw"] == "Split"


# ── regra HW Inverter ───────────────────────────────────────────────────────

@pytest.mark.parametrize("titulo,tipo,inv,esperado", [
    ("Ar Condicionado Split Hi Wall Inverter Midea 12000", None, None, True),
    ("Ar Condicionado Split Midea Airvolution 12000 Btus", None, None, True),
    ("Ar Condicionado LG Dual Inverter Voice 9", None, None, True),
    ("Ar Cond Split 12000 Btus Elgin Eco Inver", None, None, True),       # truncado em 40
    ("Ar Condicionado Samsung WindFree 12000", None, None, True),
    ("Ar Condicionado Portátil LG Inverter 12000", None, None, False),
    ("Ar Condicionado Janela Inverter 10000", None, None, False),
    ("Ar Condicionado Piso Teto Inverter 36000", None, None, False),
    ("Condensadora LG Inverter 12k", None, None, False),
    ("Ar Condicionado Multi Split Inverter", None, None, False),
    ("Ar Condicionado Split Philco Espelhado 1", None, None, False),       # título cortado, sem atributo
    ("Ar Condicionado Split Philco Espelhado 1", None, "Sim", True),       # atributo resolve
    ("Ar-condicionado Split 18.000 Btus Philco Frio Pac18fi", "Split Hi Wall Inverter", None, True),  # PAC não exclui
    ("Ar Condicionado Split Inverter 12000", "Janela", "Sim", False),      # tipo exclui
    ("Ar Condicionado Split Inverter 12000", "Cassete 4 vias", None, False),
    ("Ar Condicionado Split 12000", "Horizontal", "Não", False),
])
def test_is_hw_inverter(titulo, tipo, inv, esperado):
    assert D.is_hw_inverter(titulo, tipo, inv) is esperado


def test_classificar_origem_e_override():
    t = "Ar Condicionado Split Philco Espelhado 1"
    assert D.classificar(t).segmento == D.SEG_OUTROS_AC
    c = D.classificar(t, {"inverter_raw": "Sim", "btu_raw": "12000 BTU"})
    assert (c.segmento, c.faixa_btu, c.origem) == (D.SEG_HW_INVERTER, "12k", D.ORIGEM_ATRIBUTO)
    o = D.classificar("Ar Split Inverter 9000", override=D.SEG_OUTROS_AC)
    assert (o.segmento, o.faixa_btu, o.origem) == (D.SEG_OUTROS_AC, "9k", D.ORIGEM_OVERRIDE)
    assert D.classificar("Ar Condicionado Split Inverter").faixa_btu == D.FAIXA_ND


# ── sellers e marcas ────────────────────────────────────────────────────────

def test_seller_mascarado_nunca_desanonimizado():
    assert D.is_mascarado("castor.magenta.abstrato")
    assert not D.is_mascarado("DUFRIO REFRIGERAÇÃO") and not D.is_mascarado("CENTRALAR.COM")
    s = D.seller_exibicao("castor.magenta.abstrato")
    assert s.startswith("Seller não identificado (#") and "castor" not in s
    assert D.seller_exibicao("castor.magenta.abstrato") != D.seller_exibicao("lesma.betula.pensativo")
    assert D.seller_exibicao("CLIMARIOREFRIGERACAO", {"CLIMARIOREFRIGERACAO": "Clima Rio"}) == "Clima Rio"


def test_marca_grupo():
    assert D.marca_grupo("SPRINGER MIDEA") == "Midea"
    assert D.marca_grupo("CARRIER | MIDEA") == "Midea"
    assert D.marca_grupo("BRITÂNIA") == "Britânia"
    assert D.marca_grupo("LG ELETRONICS") == "LG"
    assert D.marca_grupo("AUFIT") == D.OUTRAS


# ── montagem, agregação e conferência ───────────────────────────────────────

def test_montar_e_agregar(fino):
    linhas = D.montar_linhas(ler_export(fino).linhas)
    assert set(linhas["segmento"]) == {D.SEG_HW_INVERTER, D.SEG_OUTROS_AC}
    port = linhas[linhas["titulo"].str.contains("Portátil")]
    assert set(port["segmento"]) == {D.SEG_OUTROS_AC}
    agg = D.agregar(linhas)
    assert agg["unidades"].sum() == linhas["unidades"].sum() == 43
    assert agg.duplicated(["data", "marca", "seller", "segmento", "faixa_btu"]).sum() == 0


def test_override_por_titulo(fino):
    t = "Ar Condicionado LG Dual Inverter Voice 9"
    linhas = D.montar_linhas(ler_export(fino).linhas, overrides={t: D.SEG_OUTROS_AC})
    assert set(linhas.loc[linhas["titulo"] == t, "origem"]) == {D.ORIGEM_OVERRIDE}
    assert set(linhas.loc[linhas["titulo"] == t, "segmento"]) == {D.SEG_OUTROS_AC}


def test_conferir_agregados(fino, tmp_path):
    linhas = D.montar_linhas(ler_export(fino).linhas)
    marca = _xlsx(tmp_path / "m.xlsx", ["Marca"], [(0, "MIDEA", (10, 25000, 20, 50000)),
                                                   (0, "LG", (5, 10000, 8, 16000))], total=(15, 35000, 28, 66000))
    assert D.conferir_agregados(linhas, ler_export(marca).linhas, ["marca"]) == []
    errado = _xlsx(tmp_path / "e.xlsx", ["Marca"], [(0, "MIDEA", (13, 25000, 20, 50000)),
                                                    (0, "LG", (5, 10000, 8, 16000))], total=(18, 35000, 28, 66000))
    assert D.conferir_agregados(linhas, ler_export(errado).linhas, ["marca"])


# ── importador ──────────────────────────────────────────────────────────────

def test_import_lote_e_dia_do_export_mais_recente(fino, tmp_path):
    import os

    novo = _xlsx(tmp_path / "novo.xlsx", ["Titulo_Publicacion", "Marca", "Nickname_Vendedor"], [
        (0, "Ar Condicionado Split Midea Airvolution 12000 Btus", (99, 1, 0, 0)),
        (1, "MIDEA", (99, 1, 0, 0)),
        (2, "DUFRIO REFRIGERAÇÃO", (99, 1, 0, 0)),
    ], dias=(D2, dt.date(2026, 11, 12)), total=(99, 1, 0, 0))
    os.utime(fino, (1, 1))
    lote = IE.ler_lote([fino, novo])
    assert lote.problemas == [] and len(lote.fino) == 2
    f = IE.folhas_unificadas(lote)
    # 10/11 só no antigo; 11/11 no novo (mais recente) — não soma os dois.
    assert f.loc[f["data"] == D1, "unidades"].sum() == 15
    assert f.loc[f["data"] == D2, "unidades"].sum() == 99


def test_import_filtro_de_categoria_errado(tmp_path):
    p = _xlsx(tmp_path / "x.xlsx", ["Titulo_Publicacion", "Marca", "Nickname_Vendedor"],
              [(0, "T", (1, 1, 0, 0)), (1, "M", (1, 1, 0, 0)), (2, "S", (1, 1, 0, 0))],
              total=(1, 1, 0, 0), filtro="Ventiladores")
    lote = IE.ler_lote([p])
    assert any("Ventiladores" in x for x in lote.problemas) and not lote.fino


def test_import_dry_run(fino):
    assert IE.main([str(fino), "--dry-run"]) == 0


def test_gravar_banco_rpc_unica_com_janela(fino):
    linhas = D.montar_linhas(ler_export(fino).linhas)

    class _Resp:
        def __init__(self, data):
            self.data = data

        def execute(self):
            return self

    class _Cli:
        def __init__(self):
            self.rpcs = []

        def rpc(self, nome, args):
            self.rpcs.append((nome, args))
            return _Resp({"agg": len(args.get("p_agg", []))} if nome == "nubi_substituir_diario" else {})

    cli = _Cli()
    IE.gravar_banco(cli, linhas, D.agregar(linhas), None, dt.date(2026, 11, 18))
    nome, args = cli.rpcs[0]
    assert nome == "nubi_substituir_diario"
    assert (args["p_desde"], args["p_ate"]) == ("2026-11-10", "2026-11-11")
    assert args["p_corte_anuncio"] == "2026-10-14" and args["p_corte_agg"] == "2026-08-10"
    assert all(r["atualizado_em"] for r in args["p_agg"])  # jsonb_populate_recordset não aplica DEFAULT
    assert [r[0] for r in cli.rpcs[1:]] == ["compactar_indices", "compactar_indices"]


# ── contas do painel ────────────────────────────────────────────────────────

def _df() -> pd.DataFrame:
    return pd.DataFrame([
        {"data": D1, "marca": "MIDEA", "seller": "A", "unidades": 10, "gmv_brl": 1000},
        {"data": D1, "marca": "SPRINGER MIDEA", "seller": "A", "unidades": 5, "gmv_brl": 500},
        {"data": D1, "marca": "LG", "seller": "B", "unidades": 20, "gmv_brl": 3000},
        {"data": D2, "marca": "LG", "seller": "A", "unidades": 5, "gmv_brl": 500},
        {"data": D2, "marca": "MIDEA", "seller": "A", "unidades": 15, "gmv_brl": 1500},
    ])


def test_kpis_midea_somada_e_delta():
    k = D.kpis(_df())
    assert k["unidades"] == 55 and k["share_midea_unid"] == pytest.approx(30 / 55)
    assert k["posicao_midea"] == 1  # Midea 30 (MIDEA + SPRINGER MIDEA) > LG 25
    assert k["delta_unidades"] is None
    ant = _df().assign(unidades=lambda x: x["unidades"] / 2)
    k2 = D.kpis(_df(), ant)
    assert k2["delta_unidades"] == pytest.approx(1.0) and k2["delta_share_midea_unid"] == pytest.approx(0.0)


def test_share_diario_soma_um():
    s = D.share_diario(_df())
    assert s.groupby("data")["share"].sum().round(6).tolist() == [1.0, 1.0]


def test_share_of_wallet():
    w = D.share_of_wallet(_df(), ["A"]).set_index("data")
    assert w.loc[D1, "share_midea"] == 1.0
    assert w.loc[D2, "share_midea"] == pytest.approx(0.75)


def test_matriz_seller_ordena_pelo_total():
    m = D.matriz_seller(_df())
    assert list(m.index) == ["A", "B"]


def test_datas_promocionais():
    assert D.black_friday(2026) == dt.date(2026, 11, 27)
    p = D.datas_promocionais(dt.date(2026, 11, 1), dt.date(2026, 12, 31))
    assert p[dt.date(2026, 11, 11)] == "11/11" and p[dt.date(2026, 11, 27)] == "Black Friday"
    assert p[dt.date(2026, 12, 25)] == "Natal"


def test_periodo_anterior():
    assert D.periodo_anterior(dt.date(2026, 11, 1), dt.date(2026, 11, 15)) == (
        dt.date(2026, 10, 17), dt.date(2026, 10, 31))


def test_spearman_elasticidade_forca():
    assert D.spearman([1, 2, 3, 4, 5], [10, 8, 6, 4, 2]) == pytest.approx(-1.0)
    assert D.spearman([1, 2, 3], [3, 2, 1]) is None
    precos = [100, 110, 121, 133.1]
    unid = [1000, 1000 / 1.1 ** 2, 1000 / 1.21 ** 2, 1000 / 1.331 ** 2]
    assert D.elasticidade(precos, unid) == pytest.approx(-2.0)
    assert D.forca_evidencia(-0.9, 10) == "evidência FRACA"
    assert D.forca_evidencia(-0.7, 30) == "evidência forte"
    assert D.forca_evidencia(None, 30) == "sem dado suficiente"


def test_cruzar_seller_pelo_nome_canonico():
    vendas = pd.DataFrame([
        {"data": D1, "marca": "MIDEA", "seller": "CLIMARIOREFRIGERACAO", "unidades": 7, "gmv_brl": 1},
        {"data": D1, "marca": "MIDEA", "seller": "lesma.betula.pensativo", "unidades": 9, "gmv_brl": 1},
    ])
    coletas = pd.DataFrame([
        {"data": D1, "marca": "Midea", "seller": "Clima Rio", "buy_box_seller": None,
         "posicao_organica": 3, "preco": 2500},
        {"data": D1, "marca": "Midea", "seller": "climario", "buy_box_seller": "Clima Rio",
         "posicao_organica": 1, "preco": 2400},
    ])
    painel = D.painel_tracker(coletas)
    cruz = D.cruzar_seller(vendas, painel, {"CLIMARIOREFRIGERACAO": "Clima Rio"})
    assert len(cruz) == 1
    r = cruz.iloc[0]
    assert (r["seller_canonico"], r["pos_midea"], r["unidades"]) == ("Clima Rio", 1, 7)


def test_cruzar_bestseller_melhor_posicao():
    vendas = D.montar_linhas(pd.DataFrame([
        {"titulo": "Ar Split Midea Inverter 12000 Btus", "marca": "MIDEA", "seller": "A", "data": D1,
         "unidades": 50, "gmv_brl": 1},
    ]))
    bs = pd.DataFrame([
        {"data": D1, "marca": "Midea", "titulo": "Split Midea 12000", "btu": 12000, "rank": 4},
        {"data": D1, "marca": "Midea", "titulo": "Split Midea 12k", "btu": None, "rank": 2},
    ])
    c = D.cruzar_bestseller(vendas, bs)
    assert c.iloc[0]["rank"] == 2 and c.iloc[0]["unidades"] == 50
