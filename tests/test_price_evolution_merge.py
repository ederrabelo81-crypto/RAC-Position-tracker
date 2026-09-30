"""
tests/test_price_evolution_merge.py — gating de fonte no merge do Price Evolution.

`query_price_evolution_data` combina PriceTrack (fonte de verdade por
``(data, SKU)``) com as coletas. Para não estourar o ``statement_timeout`` numa
consulta ILIKE sobre a tabela inteira, ele **pula** as coletas quando o usuário
não trouxe nenhum filtro que recorte a janela **e** o PriceTrack já cobre todas
as datas pedidas.

Regressão fixada aqui: *Capacidade (BTU)*, *Tipo Produto* e *Tipo Plataforma*
também são filtros que recortam a consulta. Antes ficavam de fora da heurística
``has_narrowing_filter``, então selecionar só um deles — com o PriceTrack
cobrindo toda a janela — derrubava a fonte das coletas em silêncio e o filtro
parecia "não funcionar" no gráfico.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402 — importável sem renderizar


# ---------------------------------------------------------------------------
# Fake PostgREST client — subconjunto usado por query_pricetrack_daily/coletas
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, df):
        self._df = df.copy()
        self._mask = pd.Series(True, index=self._df.index)
        self._order: list = []
        self._limit = None
        self._range = None

    def select(self, *a, **k):
        return self

    def order(self, col, desc=False):
        self._order.append((col, desc))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, lo, hi):
        self._range = (lo, hi)
        return self

    def gte(self, col, val):
        self._mask &= self._df[col].astype(str) >= str(val)
        return self

    def lte(self, col, val):
        self._mask &= self._df[col].astype(str) <= str(val)
        return self

    def eq(self, col, val):
        self._mask &= self._df[col].astype(str) == str(val)
        return self

    def in_(self, col, vals):
        self._mask &= self._df[col].astype(str).isin([str(v) for v in vals])
        return self

    class _Not:
        def __init__(self, q):
            self._q = q

        def is_(self, col, val):
            if str(val).lower() == "null":
                self._q._mask &= self._q._df[col].notna()
            return self._q

    @property
    def not_(self):
        return _Query._Not(self)

    def is_(self, col, val):
        if str(val).lower() == "null":
            self._mask &= self._df[col].isna()
        return self

    @staticmethod
    def _split_top(s: str) -> list[str]:
        parts, depth, cur = [], 0, []
        for ch in s:
            if ch == "(":
                depth += 1; cur.append(ch)
            elif ch == ")":
                depth -= 1; cur.append(ch)
            elif ch == "," and depth == 0:
                parts.append("".join(cur)); cur = []
            else:
                cur.append(ch)
        if cur:
            parts.append("".join(cur))
        return parts

    def _eval(self, expr: str) -> pd.Series:
        expr = expr.strip()
        if expr.startswith("and(") and expr.endswith(")"):
            m = pd.Series(True, index=self._df.index)
            for p in self._split_top(expr[4:-1]):
                m &= self._eval(p)
            return m
        mt = re.match(r"^([A-Za-z0-9_]+)\.([a-z]+)\.(.*)$", expr, re.S)
        col, op, val = mt.group(1), mt.group(2), mt.group(3)
        s = self._df[col]
        if op == "ilike":
            needle = val.strip("%").casefold()
            return s.astype("string").str.casefold().str.contains(
                re.escape(needle), na=False)
        if op == "like":
            return s.astype("string").str.contains(re.escape(val.strip("%")), na=False)
        if op == "eq":
            return s.astype(str) == val
        if op == "lt":
            return s.astype(str) < val
        if op == "in":
            return s.astype(str).isin(val.strip("()").split(","))
        raise AssertionError(f"op não suportado no fake: {op}")

    def or_(self, expr):
        m = pd.Series(False, index=self._df.index)
        for p in self._split_top(expr):
            m |= self._eval(p)
        self._mask &= m
        return self

    def execute(self):
        df = self._df[self._mask]
        for col, desc in reversed(self._order):
            if col in df.columns:
                df = df.sort_values(col, ascending=not desc, kind="stable")
        if self._range is not None:
            lo, hi = self._range
            df = df.iloc[lo:hi + 1]
        elif self._limit is not None:
            df = df.head(self._limit)
        return _Resp(df.to_dict("records"))


class _FakeClient:
    def __init__(self, tables: dict):
        self._t = {k: v.reset_index(drop=True) for k, v in tables.items()}

    def table(self, name):
        return _Query(self._t.get(name, pd.DataFrame()))

    def rpc(self, *a, **k):
        raise RuntimeError("rpc indisponível no fake")


# ---------------------------------------------------------------------------
# Fixtures — PriceTrack e coletas cobrindo a MESMA janela inteira (2 dias)
# ---------------------------------------------------------------------------

_DAYS = [date(2026, 8, 17), date(2026, 8, 18)]


@pytest.fixture
def pt() -> pd.DataFrame:
    rows = []
    i = 0
    for d in _DAYS:
        for sku, btu in [("SKU-9K", "9000"), ("SKU-12K", "12000")]:
            i += 1
            rows.append({
                "id": i, "collection_date": d, "turno": "Diário",
                "brand": "MIDEA", "sku": sku,
                "title": f"SPLIT MIDEA {btu} BTU FRIO INVERTER",
                "marketplace": "MERCADO LIVRE", "seller": "Loja Midea",
                "min_price": 2000.0 + i, "avg_price": 2100.0 + i,
                "mode_price": 2050.0 + i, "max_price": 2200.0 + i,
            })
    return pd.DataFrame(rows)


@pytest.fixture
def coletas() -> pd.DataFrame:
    # sku_resolvido PROPOSITALMENTE distinto dos SKUs do PriceTrack: são os
    # SKUs "não cobertos pelo pricetrack" (caso (b) do merge), que sobrevivem à
    # dedup e por isso provam que a fonte das coletas foi de fato consultada.
    rows = []
    i = 0
    for d in _DAYS:
        for sku, btu in [("COL-9K", "9000"), ("COL-12K", "12000")]:
            i += 1
            rows.append({
                "id": 1000 + i, "data": d, "turno": "Manhã",
                "plataforma": "Amazon", "tipo": "Marketplace", "marca": "Midea",
                "seller": "AmazonBR", "keyword": "ar condicionado midea",
                "produto": f"Ar Condicionado Midea {btu} BTUs Inverter Frio",
                "preco": 1900.0 + i, "posicao_geral": i,
                "posicao_organica": i, "posicao_patrocinada": None,
                "estado_match": "MAPEADO", "familia_resolvida": None,
                "sku_resolvido": sku, "voltagem_resolvida": "220V",
                "run_id": None, "created_at": f"{d}T09:00:00",
            })
    return pd.DataFrame(rows)


@pytest.fixture(autouse=True)
def _wire(monkeypatch, pt, coletas):
    """PriceTrack cobre toda a janela; sem histórico frio; ambas as fontes on."""
    fake = _FakeClient({"pricetrack_daily": pt, "coletas": coletas,
                        "produtos_catalogo": pd.DataFrame(),
                        "produtos_depara_nome": pd.DataFrame()})
    monkeypatch.setattr(app, "_get_supabase", lambda: fake)
    monkeypatch.setattr(app, "get_catalogo", lambda: pd.DataFrame())
    monkeypatch.setattr(app, "get_depara", lambda: pd.DataFrame())
    monkeypatch.setattr(app, "_pricetrack_gap_fill", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(app, "_history_gap_fill", lambda *a, **k: pd.DataFrame())
    app.st.session_state["gf_sources"] = ["coletas", "pricetrack"]
    yield


def _sources(df: pd.DataFrame) -> dict:
    return df["source"].value_counts().to_dict() if "source" in df.columns else {}


class TestNarrowingKeepsColetas:
    """Filtros que recortam a consulta preservam a fonte das coletas."""

    def test_capacity_only_keeps_coletas(self):
        """Regressão: Capacidade (BTU) sozinha não pode derrubar as coletas."""
        df, meta = app.query_price_evolution_data(
            _DAYS[0], _DAYS[-1], btu_filter=["12000"])
        src = _sources(df)
        assert meta["coletas_rows"] > 0, "coletas foram descartadas pelo filtro de BTU"
        assert src.get("coletas", 0) > 0 and src.get("pricetrack", 0) > 0, src
        # E de fato recorta: só os SKUs de 12.000 BTU aparecem (nenhum 9.000).
        skus = set(df["sku"].dropna().unique())
        assert skus == {"SKU-12K", "COL-12K"}, sorted(skus)

    def test_product_type_only_keeps_coletas(self):
        df, meta = app.query_price_evolution_data(
            _DAYS[0], _DAYS[-1], product_types=["Inverter"])
        assert meta["coletas_rows"] > 0, "coletas descartadas pelo filtro de Tipo Produto"
        assert _sources(df).get("coletas", 0) > 0

    def test_platform_type_only_keeps_coletas(self):
        df, meta = app.query_price_evolution_data(
            _DAYS[0], _DAYS[-1], platform_types=["Marketplace"])
        assert meta["coletas_rows"] > 0, "coletas descartadas pelo filtro de Tipo Plataforma"
        assert _sources(df).get("coletas", 0) > 0

    def test_brand_still_keeps_coletas(self):
        """Sanidade: marca — que já contava — segue trazendo as duas fontes."""
        df, meta = app.query_price_evolution_data(
            _DAYS[0], _DAYS[-1], brands=["Midea"])
        assert _sources(df).get("coletas", 0) > 0
        assert _sources(df).get("pricetrack", 0) > 0


class TestNoFilterStillSkips:
    """Sem NENHUM filtro e PriceTrack cobrindo tudo, as coletas continuam
    puladas — a defesa contra o timeout de ILIKE na tabela inteira."""

    def test_no_filter_skips_coletas(self):
        df, meta = app.query_price_evolution_data(_DAYS[0], _DAYS[-1])
        assert meta["coletas_rows"] == 0, "coletas não deveriam ser consultadas sem filtro"
        assert _sources(df).get("pricetrack", 0) > 0


# ---------------------------------------------------------------------------
# Uma série, uma fonte (30/09/2026) — o caso Philco
#
# `pricetrack_daily` sem 25, 26 e 29/09/2026: a precedência por (data, SKU)
# deixava as coletas preencherem esses dias e a série da Philco "caía" de
# ~R$ 13 mil para ~R$ 2 mil sem o mercado mexer. O gráfico agora pede as duas
# fontes sem a precedência (`dedup_sku_day=False`) e `_evo_build_series`
# escolhe UMA fonte por série.
# ---------------------------------------------------------------------------

_SEP = [date(2026, 9, x) for x in range(22, 31)]
_PT_GAPS = {date(2026, 9, 25), date(2026, 9, 26), date(2026, 9, 29)}
_PT_DAYS = [d for d in _SEP if d not in _PT_GAPS]


@pytest.fixture
def philco(monkeypatch):
    """PT falha em 25, 26 e 29/09 e cobre o split 12K da Philco e um portátil
    de 12K (a categoria do PriceTrack é todo "AR CONDICIONADO"); as coletas
    têm todos os dias do split 12K e de um 9K que o PriceTrack não cobre."""
    pt_rows = []
    pt_produtos = [
        ("PH-12K", "SPLIT PHILCO 12000 BTU FRIO INVERTER", 2400.0),
        ("PH-PORT12", "AR CONDICIONADO PORTATIL PHILCO 12000 BTU FRIO", 2100.0),
    ]
    for d in _PT_DAYS:
        for sku, title, piso in pt_produtos:
            pt_rows.append({
                "id": len(pt_rows) + 1, "collection_date": d, "turno": "Diário",
                "brand": "PHILCO", "sku": sku, "title": title,
                "marketplace": "MERCADO LIVRE", "seller": "Loja Philco",
                "min_price": piso, "avg_price": piso + 200,
                "mode_price": piso + 150, "max_price": piso + 400,
            })
    col_rows = []
    produtos = [
        ("PH-12K", "Ar Condicionado Philco 12000 BTUs Inverter Frio", 1900.0),
        ("PH-9K", "Ar Condicionado Philco 9000 BTUs Inverter Frio", 1500.0),
    ]
    i = 0
    for d in _SEP:
        for sku, nome, preco in produtos:
            i += 1
            col_rows.append({
                "id": 5000 + i, "data": d, "turno": "Abertura",
                "plataforma": "Amazon", "tipo": "Marketplace", "marca": "Philco",
                "seller": "AmazonBR", "keyword": "ar condicionado",
                "produto": nome, "preco": preco, "posicao_geral": 1,
                "posicao_organica": 1, "posicao_patrocinada": None,
                "estado_match": "MAPEADO",
                "familia_resolvida": None, "sku_resolvido": sku,
                "voltagem_resolvida": "220V", "run_id": None,
                "created_at": f"{d}T09:00:00",
            })
    fake = _FakeClient({"pricetrack_daily": pd.DataFrame(pt_rows),
                        "coletas": pd.DataFrame(col_rows),
                        "produtos_catalogo": pd.DataFrame(),
                        "produtos_depara_nome": pd.DataFrame()})
    monkeypatch.setattr(app, "_get_supabase", lambda: fake)
    yield


def _load(dedup: bool) -> pd.DataFrame:
    df, _ = app.query_price_evolution_data(
        _SEP[0], _SEP[-1], brands=["Philco"], dedup_sku_day=dedup)
    return df


def _opts(group_by: str, **kw) -> "app._EvoOptions":
    return app._EvoOptions(group_by=group_by,
                           metric=app._PRICE_METRICS["Buy Box (menor preço)"], **kw)


@pytest.mark.usefixtures("philco")
class TestUmaFontePorSerie:
    def test_precedencia_por_dia_troca_a_fonte_da_serie(self):
        """Reprodução: com a precedência por (data, SKU), o MESMO SKU vem do
        PriceTrack num dia e das coletas no outro — por isso nenhum gráfico
        de série usa mais esse modo."""
        df = _load(dedup=True)
        por_dia = (df[df["sku"] == "PH-12K"].groupby("data")["source"]
                   .agg(lambda s: set(s)))
        assert all(por_dia[d] == {"pricetrack"} for d in _PT_DAYS)
        assert all(por_dia[d] == {"coletas"} for d in _PT_GAPS)

    def test_sem_precedencia_as_duas_fontes_chegam_inteiras(self):
        df = _load(dedup=False)
        sk = df[df["sku"] == "PH-12K"]
        assert set(sk.loc[sk["source"] == "coletas", "data"]) == set(_SEP)
        assert set(sk.loc[sk["source"] == "pricetrack", "data"]) == set(_PT_DAYS)

    def test_serie_por_sku_nao_troca_de_fonte(self):
        evo = app._evo_build_series(_load(dedup=False), _opts("Product"))
        agg = evo.agg
        ph12 = agg[agg["series"].str.endswith("PH-12K")]
        assert set(ph12["fonte"]) == {"pricetrack"}
        assert set(ph12["value"]) == {2400.0}, "a linha mudou só porque a fonte mudou"
        # Dia sem PriceTrack é lacuna — nunca preenchido pelas coletas.
        assert set(ph12["data"].dt.date) == set(_PT_DAYS)
        # SKU que só as coletas cobrem segue inteiro, nas coletas.
        ph9 = agg[agg["series"].str.endswith("PH-9K")]
        assert set(ph9["fonte"]) == {"coletas"} and len(ph9) == len(_SEP)
        # Cada ponto sabe a fonte (hover/legenda).
        assert set(agg["fonte_label"]) == {"PriceTrack", "Coletas"}

    def test_serie_por_marca_no_mesmo_btu_e_uma_fonte(self):
        """Caso Philco: a série por marca (12.000 BTU, padrão) fica no
        PriceTrack; 25, 26 e 29/09 ficam em branco e a legenda diz isso."""
        evo = app._evo_build_series(_load(dedup=False), _opts("Brand"))
        assert evo.empty_reason == ""
        philco_pts = evo.agg[evo.agg["series"] == "Philco"]
        assert set(philco_pts["fonte"]) == {"pricetrack"}
        assert philco_pts["value"].nunique() == 1
        assert set(philco_pts["data"].dt.date) == set(_PT_DAYS)
        rep = evo.report.set_index("series").loc["Philco"]
        assert rep["fonte"] == "pricetrack"
        assert set(rep["datas_descartadas"]) == _PT_GAPS
        # O 9K e o portátil não entram na série de 12.000 BTU.
        assert evo.removed_capacity == len(_SEP)
        assert evo.removed_format == len(_PT_DAYS)

    def test_uma_linha_por_btu(self):
        evo = app._evo_build_series(
            _load(dedup=False), _opts("Brand", capacity=app._EVO_CAP_EACH))
        fontes = evo.agg.groupby("series")["fonte"].unique().map(list).to_dict()
        assert fontes == {"Philco · 12.000 BTU": ["pricetrack"],
                          "Philco · 9.000 BTU": ["coletas"]}

    def test_capacidade_sem_linha_avisa_escopo(self):
        evo = app._evo_build_series(
            _load(dedup=False), _opts("Brand", capacity="60000"))
        assert evo.empty_reason == "scope"

    def test_hiwall_desligado_mantem_portatil(self):
        """Sem o recorte hi-wall o portátil (mais barato) vira o piso da
        marca — é o número que o recorte existe para não mostrar."""
        evo = app._evo_build_series(
            _load(dedup=False), _opts("Brand", hiwall_only=False))
        assert evo.removed_format == 0
        assert set(evo.agg.loc[evo.agg["series"] == "Philco", "value"]) == {2100.0}

    def test_default_da_capacidade_e_12000(self):
        assert app._EvoOptions(group_by="Brand", metric={}).capacity == "12000"


class TestEscopoEFonteNaSaida:
    def test_excluir_google_vale_com_guarda_desligada(self):
        """"Excluir Google Shopping" é escopo, não parte da guarda "Dados limpos"."""
        df = pd.DataFrame([
            {"data": date(2026, 9, d), "source": "coletas", "sku": "S",
             "marca": "Midea", "plataforma": plat,
             "produto": "Split Midea 12000 BTUs", "preco": 2000.0}
            for d in (1, 2) for plat in ("Google Shopping", "Amazon")])
        evo = app._evo_build_series(
            df, _opts("Product", clean=False, exclude_google=True))
        assert evo.removed_google == 2
        assert set(evo.work["plataforma"]) == {"Amazon"}

    def test_emails_dizem_a_fonte_do_delta(self):
        """O delta é por fonte: o e-mail precisa dizer qual, senão o mesmo
        produto pode aparecer duas vezes sem distinção."""
        shown = pd.DataFrame([{
            "produto": "P", "marca": "Midea", "plataforma": "Amazon",
            "source": "coletas", "price_today": 2100.0, "price_prev": 2000.0,
            "delta_pct": 5.0}])
        html, text = app._build_anomaly_email(
            date(2026, 9, 30), date(2026, 9, 29), 1.0, shown)
        assert "Fonte" in html and "Coletas" in html
        assert "/ Coletas]" in text
        ups = pd.DataFrame([{
            "produto": "P", "source": "pricetrack", "preco_anterior": 2000.0,
            "preco_atual": 2200.0, "delta_pct": 10.0}])
        html, text = app._build_digest_email(
            date(2026, 9, 22), date(2026, 9, 29), 1, {"P": "Midea"},
            ups, pd.DataFrame(), pd.Series(dtype=int), 10)
        assert "Fonte" in html and "PriceTrack" in html
        assert "[PriceTrack]" in text
