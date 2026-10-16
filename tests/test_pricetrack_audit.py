"""
tests/test_pricetrack_audit.py — lógica pura da auditoria do PriceTrack.

Os números reproduzem a conferência de 16/10/2026 (degrau de 30/09 → 02/10,
zero indisponível, Shopee fora, kit de instalação casado como aparelho).
"""
from utils.pricetrack_audit import (
    AuditConfig,
    compare_prices,
    coverage_gaps,
    unavailability_streak,
    volume_steps,
)

_SET = {f"2026-09-{d:02d}": 1_470_000 for d in range(23, 31)}
_NOSSO = {f"2026-09-{d:02d}": 2_400 for d in range(23, 31)}


def test_degrau_do_pricetrack_com_nossa_coleta_estavel():
    pt = {**_SET, "2026-10-02": 547_794}
    nosso = {**_NOSSO, "2026-10-02": 2_430}
    passos = volume_steps(pt, nosso)
    assert [p["data"] for p in passos] == ["2026-10-02"]
    assert passos[0]["pricetrack_razao"] < 0.5
    assert passos[0]["nosso_razao"] > 0.95


def test_queda_das_duas_fontes_nao_e_culpa_do_pricetrack():
    pt = {**_SET, "2026-10-02": 547_794}
    nosso = {**_NOSSO, "2026-10-02": 900}   # nossa coleta caiu junto
    assert volume_steps(pt, nosso) == []


def test_sem_base_suficiente_nao_alarma():
    assert volume_steps({"2026-10-01": 1e6, "2026-10-02": 1e5},
                        {"2026-10-01": 1, "2026-10-02": 1}) == []


def test_sequencia_sem_indisponivel():
    assert unavailability_streak({"2026-10-13": 0, "2026-10-14": 0, "2026-10-15": 0}) == 3
    assert unavailability_streak({"2026-10-13": 0, "2026-10-14": 5, "2026-10-15": 0}) == 1
    assert unavailability_streak({}) == 0


def test_dia_sem_observacao_encerra_a_sequencia():
    # 12/10 não foi importado: 13–15 contam, 10–11 não emendam por cima do buraco.
    dias = {"2026-10-10": 0, "2026-10-11": 0, "2026-10-13": 0, "2026-10-14": 0, "2026-10-15": 0}
    assert unavailability_streak(dias) == 3


def test_shopee_sem_cobertura():
    nossas = ["Amazon", "Shopee", "Magalu", "Google Shopping"]
    pt = ["AMAZON", "MAGAZINE LUIZA"]
    # Google Shopping não tem par no PriceTrack — não é lacuna dele.
    assert coverage_gaps(nossas, pt) == ["Shopee"]


def test_kit_de_instalacao_nao_entra_na_comparacao():
    nossos = [
        # O kit de R$ 255 com o nome canônico do aparelho (linha antiga)
        {"plataforma": "Amazon", "sku_resolvido": "TAC-09CSA1", "preco": "255.55",
         "produto": "Ar Condicionado TCL Elite 9.000 BTUs Inverter Frio",
         "url_produto": "https://www.amazon.com.br/Completo-Instala%C3%A7%C3%A3o-condicionado-branco-Suporte/dp/B0DGJ5KR94"},
        {"plataforma": "Amazon", "sku_resolvido": "TAC-09CSA1", "preco": "1950.00",
         "produto": "Ar Condicionado TCL Elite 9.000 BTUs Inverter Frio",
         "url_produto": "https://www.amazon.com.br/Ar-Condicionado-TCL-Elite-Inverter/dp/B0AAAAAAA1"},
    ]
    pt = [{"marketplace": "AMAZON", "sku": "TAC-09CSA1", "min_price": "1997.28"}]
    res = compare_prices(nossos, pt)
    assert res.excluidos_por_tipo == {"ACESSORIO": 1}
    assert res.pares == 1
    assert res.por_plataforma["AMAZON"]["pct_bate"] == 100.0
    assert res.divergencias == []


def test_divergencia_listada_com_url_e_sinal():
    nossos = [{"plataforma": "Mercado Livre", "sku_resolvido": "pac24000ifm15",
               "preco": 2989, "produto": "Ar Condicionado Philco 24.000",
               "url_produto": "https://www.mercadolivre.com.br/ar-condicionado-philco/p/MLB1"}]
    pt = [{"marketplace": "MERCADO LIVRE", "sku": "PAC24000IFM15", "min_price": "5195.88"}]
    res = compare_prices(nossos, pt, AuditConfig(top=5))
    assert res.por_plataforma["MERCADO LIVRE"]["pct_divergente"] == 100.0
    [d] = res.divergencias
    assert d["dif_pct"] == 73.8 and d["url"].endswith("MLB1")


def test_menor_preco_de_unidade_por_sku():
    nossos = [
        {"plataforma": "Magalu", "sku_resolvido": "X", "preco": p,
         "produto": "Ar Condicionado Midea 12.000", "url_produto": f"https://m.magazineluiza.com.br/ar-condicionado-midea/p/{i}"}
        for i, p in enumerate((3100, 2990, None, "abc"))
    ]
    pt = [{"marketplace": "MAGAZINE LUIZA", "sku": "X", "min_price": 2990}]
    res = compare_prices(nossos, pt)
    assert res.por_plataforma["MAGAZINE LUIZA"] == {
        "pares": 1, "pct_bate": 100.0, "pct_divergente": 0.0, "pct_pt_mais_caro": 0.0}


# ── Script (I/O) com cliente falso ───────────────────────────────────────────
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "pricetrack_audit_script",
    Path(__file__).resolve().parent.parent / "scripts" / "pricetrack_audit.py",
)
script = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(script)


class _Q:
    def __init__(self, rows):
        self._rows, self._f = rows, []

    def select(self, *_):
        return self

    def _add(self, fn):
        self._f.append(fn)
        return self

    def eq(self, c, v):
        return self._add(lambda r: str(r.get(c)) == str(v))

    def gte(self, c, v):
        return self._add(lambda r: str(r.get(c)) >= str(v))

    def lte(self, c, v):
        return self._add(lambda r: str(r.get(c)) <= str(v))

    def gt(self, c, v):
        return self._add(lambda r: (r.get(c) or 0) > v)

    def like(self, c, v):
        return self._add(lambda r: str(r.get(c)).startswith(v.rstrip("%")))

    def order(self, *_a, **_k):
        return self

    @property
    def not_(self):
        q = self

        class _N:
            def is_(self, c, _v):
                return q._add(lambda r: r.get(c) is not None)
        return _N()

    def range(self, a, b):
        self._slice = (a, b + 1)
        return self

    def execute(self):
        rows = [r for r in self._rows if all(f(r) for f in self._f)]
        a, b = getattr(self, "_slice", (0, len(rows)))
        return type("R", (), {"data": rows[a:b]})()


class _Client:
    def __init__(self, tabelas):
        self._t = tabelas

    def table(self, nome):
        return _Q(self._t.get(nome, []))


def _tabelas():
    dias = [f"2026-09-{d:02d}" for d in range(23, 31)] + ["2026-10-01", "2026-10-02"]
    log = [{"source_file": f"api-{d}", "rows_total": 547_794 if d == "2026-10-02" else 1_470_000,
            "import_started": f"{d}T09:00:00"} for d in dias]
    sod = [{"data": d, "plataforma": p, "offer_key": f"{p}-{i}"}
           for d in dias for p in ("Amazon", "Shopee") for i in range(1200)]  # > 1 página
    ptd = [{"collection_date": d, "turno": "Diário", "marketplace": "AMAZON", "sku": "X",
            "min_price": "2000", "unavailable_count": 0} for d in dias]
    col = [{"data": "2026-10-02", "plataforma": "Amazon", "sku_resolvido": "X", "preco": "2010",
            "produto": "Ar Condicionado Midea 12.000", "url_produto": "https://www.amazon.com.br/ar-condicionado-midea/dp/B1"}]
    return {"pricetrack_import_log": log, "seller_offer_daily": sod,
            "pricetrack_daily": ptd, "coletas": col}


def test_script_de_ponta_a_ponta_com_cliente_falso():
    dados = script.coletar(_Client(_tabelas()), "2026-10-02", "2026-09-18")
    assert dados["nosso_volume"]["2026-10-02"] == 2400      # paginou além de 1.000
    assert dados["pt_volume"]["2026-10-02"] == 547_794
    rel = script.auditar(dados, "2026-10-02", AuditConfig())
    assert any("degrau" in a for a in rel["alertas"])
    assert any("indisponível" in a for a in rel["alertas"])
    assert rel["cobertura"]["sem_pricetrack"] == ["Shopee"]
    assert rel["precos"]["por_plataforma"]["AMAZON"]["pct_bate"] == 100.0
    md = script._md(rel)
    assert "Degrau de volume" in md and "| AMAZON | 1 | 100.0% |" in md


def test_mesmo_vendedor_e_vendedor_mais_barato_fora_do_pt():
    url = "https://www.mercadolivre.com.br/ar-condicionado-philco/p/MLB{}"
    nossos = [
        {"plataforma": "Mercado Livre", "sku_resolvido": "S1", "preco": 2989,
         "produto": "Ar Condicionado Philco 24.000", "url_produto": url.format(1), "seller": "Compra Turbo"},
        {"plataforma": "Mercado Livre", "sku_resolvido": "S1", "preco": 5100,
         "produto": "Ar Condicionado Philco 24.000", "url_produto": url.format(2), "seller": "friopecas"},
    ]
    pt = [{"marketplace": "MERCADO LIVRE", "sku": "S1", "seller": "FRIOPEÇAS", "min_price": "5195.88"}]
    res = compare_prices(nossos, pt)
    # O preço da Frio Peças bate (5.100 × 5.196); o mínimo do SKU não, porque
    # o vendedor mais barato (Compra Turbo) não está no PriceTrack.
    assert res.mesmo_vendedor["MERCADO LIVRE"]["pct_bate"] == 100.0
    assert res.por_plataforma["MERCADO LIVRE"]["pct_divergente"] == 100.0
    assert res.por_plataforma["MERCADO LIVRE"]["pct_mais_barato_fora_do_pt"] == 100.0


def test_placeholder_de_seller_nao_entra_na_leitura_por_vendedor():
    nossos = [{"plataforma": "Casas Bahia", "sku_resolvido": "S1", "preco": 2000,
               "produto": "Ar Condicionado Midea", "url_produto": "https://www.casasbahia.com.br/ar-condicionado/p/1",
               "seller": "Casas Bahia", "buy_box_seller": None}]
    pt = [{"marketplace": "CASAS BAHIA", "sku": "S1", "seller": "CASAS BAHIA", "min_price": 2000}]
    res = compare_prices(nossos, pt)
    assert res.mesmo_vendedor == {}
    assert "pct_mais_barato_fora_do_pt" not in res.por_plataforma["CASAS BAHIA"]
