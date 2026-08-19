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
