"""Módulo nubimetrics/: filtros, transformação, carga e ETL (sem rede).

Usa as amostras REAIS de nubimetrics/catalog/samples/ (conta Midea Brasil,
MLB1646, 13/11/2026) via o cliente falso de tests/nubimetrics_fake.py.
"""
import asyncio
import json
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from nubimetrics_fake import FakeNubi, amostra  # noqa: E402

from nubimetrics import analytics as an  # noqa: E402
from nubimetrics import load, transform  # noqa: E402
from nubimetrics.filters import (  # noqa: E402
    SEG_HW_INVERTER, SEG_HW_ONOFF, SEG_JANELA, SEG_PISO_TETO, SEG_PORTATIL,
    btu_do_modelo, classificar, faixa_btu, is_midea, marca_exibicao, normalizar_marca,
    parse_btu, parse_voltagem,
)

HOJE = dt.date(2026, 11, 13)


# ── filtros ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("texto,btu", [
    ("Ar Split 9.000 Btus", 9000), ("Inverter 12000btu", 12000), ("12k Midea", 12000),
    ("18 000 BTUs", 18000), ("Ar 220V sem btu", None), ("Agratto Zen 9.000 Btus Frio Inv", 9000),
])
def test_parse_btu(texto, btu):
    assert parse_btu(texto) == btu


def test_btu_pelo_modelo_e_faixas():
    assert btu_do_modelo("38EZVCC12M5") == 12000
    assert btu_do_modelo("96662756") is None          # só dígitos: não é código de modelo
    assert [faixa_btu(b) for b in (7500, 9000, 12000, 18000, 24000, 36000)] == \
        ["≤7k", "9k", "12k", "18k", "22–24k", "30k+"]


def test_marca_normalizada():
    assert normalizar_marca("Britânia") == normalizar_marca("BRITANIA") == "BRITANIA"
    assert marca_exibicao("MIDEA") == "Midea" and is_midea("Midea") and not is_midea("LG")
    assert parse_voltagem("Frio 220v") == "220V" and parse_voltagem("127V") == "127V"


@pytest.mark.parametrize("titulo,seg,fonte", [
    ("Ar Condicionado Split Hi Wall Inverter Midea 12.000 Btus Frio 220v", SEG_HW_INVERTER, "title_regex"),
    ("Ar Condicionado Split Hi Wall On/Off 9000 Btus", SEG_HW_ONOFF, "title_regex"),
    ("Ar-Condicionado Portátil Electrolux 12.000 BTUS", SEG_PORTATIL, "title_regex"),
    ("Ar Condicionado Janela 7500 Btus Mecânico", SEG_JANELA, "title_regex"),
    ("Ar Condicionado Split Piso Teto 36000 Btus", SEG_PISO_TETO, "title_regex"),
    ("Ar Condicionado Split Philco Espelhado 1", SEG_HW_INVERTER, "title_default"),
])
def test_classificar(titulo, seg, fonte):
    c = classificar(titulo)
    assert (c.segmento, c.match_source) == (seg, fonte)


def test_classificar_btu_pelo_modelo_quando_titulo_truncado():
    c = classificar("Ar Condicionado Hw Inverter Midea Ai Eco", "38EZVCC12M5")
    assert (c.btu, c.match_source) == (12000, "model_regex")


# ── transformação (amostras reais) ──────────────────────────────────────────

def test_mercado_marca_mes_corrente_como_parcial():
    linhas = transform.mercado_mensal({"tamanho": amostra("nubi_category_market_size"),
                                       "estrutura": amostra("nubi_category_market_structure")}, HOJE)
    assert len(linhas) == 13
    nov = linhas[-1]
    assert nov["mes"] == "2026-11-01" and nov["parcial"] is True
    assert round(nov["receita_brl"] / 1e6, 2) == 173.34     # bate com a UI ("R$ 173,3M")


def test_ranking_fallback_nao_grava_mes_errado():
    bruto = amostra("meli_market_rankings__brands")          # pedido nov → veio out (fallback)
    assert bruto["period_fallback"] is True
    assert transform.ranking(bruto, dt.date(2026, 11, 1), "brands") == []


def test_ranking_posicao_unica_mesmo_com_empate():
    bruto = {**amostra("meli_market_rankings__catalog"), "period_fallback": False}
    linhas = transform.ranking(bruto, dt.date(2026, 10, 1), "catalog")
    assert [r["posicao"] for r in linhas] == list(range(1, len(linhas) + 1))


def test_soma_das_marcas_bate_com_a_categoria():
    marcas = transform.ranking({**amostra("meli_market_rankings__brands"), "period_fallback": False},
                               dt.date(2026, 10, 1), "brands")
    mercado = transform.mercado_mensal({"tamanho": amostra("nubi_category_market_size"), "estrutura": {}}, HOJE)
    assert transform.checar_consistencia(marcas, mercado) is None
    assert marcas[1]["nome"] == "MIDEA" and round(marcas[1]["share_brl"], 1) == 13.3


def test_concorrente_converte_strings_arredondadas():
    linhas = transform.concorrente([{"concorrente": "x", "serie": amostra("nubi_competition_seller_evolution")}])
    assert linhas and {"visitas", "unidades", "receita_brl", "conversao_pct"} <= set(linhas[0])
    assert transform._num("1.3K") == 1300 and transform._num("3M") == 3_000_000


def test_detalhe_categoria_so_no_mes_pedido():
    linhas = [{"mes": "2026-10-01", "tipo": "real"}]
    det = {**amostra("meli_category_detail"), "period_applied": "2026-10-01"}
    transform.detalhe_categoria(det, linhas, dt.date(2026, 10, 1))
    assert linhas[0]["saturacao"] == "Média"
    outra = [{"mes": "2026-10-01", "tipo": "real"}]
    transform.detalhe_categoria(amostra("meli_category_detail"), outra, dt.date(2026, 10, 1))  # veio nov
    assert "saturacao" not in outra[0]


# ── carga ───────────────────────────────────────────────────────────────────

class _Tabela:
    def __init__(self, log):
        self.log = log

    def upsert(self, lote, on_conflict):
        self.log.append((lote, on_conflict))
        return self

    def execute(self):
        return type("R", (), {"data": []})()


class _Cliente:
    def __init__(self):
        self.log = []
        self.rpcs = []

    def table(self, nome):
        return _Tabela(self.log)

    def rpc(self, nome, params):
        self.rpcs.append((nome, params))
        return type("Q", (), {"execute": lambda s: type("R", (), {"data": len(params["p_linhas"])})()})()


def test_upsert_iguala_as_chaves_do_lote():
    cli = _Cliente()
    load.upsert(cli, "nubi_ranking_mensal", [{"a": 1, "b": 2}, {"a": 3}])
    lote, chave = cli.log[0]
    assert all(set(x) == {"a", "b", "atualizado_em"} for x in lote)
    assert chave == "categoria_id,mes,dimensao,posicao"


def test_foto_vazia_mantem_a_anterior():
    cli = _Cliente()
    assert load.substituir_anuncios(cli, []) == 0 and cli.rpcs == []


# ── ETL de ponta a ponta (MCP falso) ────────────────────────────────────────

@pytest.fixture
def dados(monkeypatch):
    import nubimetrics.client as client
    import nubimetrics.run_daily as run

    monkeypatch.setattr(client, "NubiClient", lambda *a, **k: FakeNubi())
    return run.transformar(asyncio.run(run.extrair(2, True, HOJE)), HOJE)


def test_etl_monta_todas_as_tabelas(dados):
    assert {x["tipo"] for x in dados["mercado"]} == {"real", "previsao"}
    dims = {r["dimensao"] for r in dados["rankings"]}
    assert dims == {"brands", "seller", "catalog", "items", "official_store", "search_terms"}
    assert len(dados["anuncios"]) == 10 and dados["concorrente"]
    assert all(a["atualizado_em"] for a in dados["anuncios"])        # NOT NULL na RPC


def test_etl_dry_run_nao_grava(monkeypatch):
    import nubimetrics.client as client
    import nubimetrics.run_daily as run

    monkeypatch.setattr(client, "NubiClient", lambda *a, **k: FakeNubi())
    monkeypatch.setattr(run, "gravar", lambda *a, **k: pytest.fail("dry-run gravou"))
    assert run.main(["--dry-run"]) == run.EXIT_OK


# ── análises do painel ──────────────────────────────────────────────────────

def _frames(dados):
    rk = pd.DataFrame(dados["rankings"])
    a = an.preparar_anuncios(pd.DataFrame(dados["anuncios"]),
                             pd.DataFrame([{"nickname": "DUFRIO REFRIGERAÇÃO", "display_name": "Dufrio",
                                            "is_midea_partner": True},
                                           {"nickname": "BELMICROSHOP", "display_name": "Bel Micro",
                                            "is_midea_partner": True}]))
    return rk, a


def test_kpis_midea_outubro(dados):
    rk, _ = _frames(dados)
    k = an.kpis_marca(rk, pd.Timestamp("2026-10-01"))
    assert round(k["mercado_receita"] / 1e6, 1) == 223.5
    assert k["posicao"] == 2 and round(k["share"], 1) == 13.3 and round(k["gap_rival_pp"], 1) == -6.7


def test_penetracao_e_oportunidades(dados):
    _, a = _frames(dados)
    rec = an.filtrar(a)
    pen = an.penetracao(rec).set_index("seller_exib")
    assert pen.loc["Bel Micro", "share_midea"] == 0 and pen.loc["Dufrio", "indice_penetracao"] > 1
    opps = an.oportunidades(rec)
    assert not opps.empty and list(opps["prioridade"]) == list(range(1, len(opps) + 1))
    assert opps["impacto_brl"].is_monotonic_decreasing
    assert set(opps["seller"]) <= {"Dufrio", "Bel Micro", "Mercado (todos)"}   # só parceiros + mercado


def test_formatacao_pt_br():
    assert an.fmt_brl(33_970_000) == "R$ 34,0 mi" and an.fmt_brl(345_000) == "R$ 345 mil"
    assert an.fmt_int(15567, " un") == "15.567 un" and an.fmt_pct(19.6) == "19,6%"
    assert an.fmt_pp(1.234) == "+1,2 p.p."


def test_resumo_em_ingles(dados):
    rk, a = _frames(dados)
    k = an.kpis_marca(rk, pd.Timestamp("2026-10-01"))
    en = an.resumo_en(k, pd.Timestamp("2026-10-01"), an.oportunidades(an.filtrar(a)))
    assert en.iloc[0]["Value"] == "Oct 2026" and "Midea revenue share" in set(en["Metric"])


def test_meta_de_share():
    r = an.meta_share(100.0, 13.0, 15.0)
    assert r["falta"] == pytest.approx(2.0)


# ── revisão do PR #449 (Cubic, 2ª passada) ──────────────────────────────────

def test_explorador_pagina_ate_o_fim_e_deduplica(monkeypatch):
    from nubimetrics import extract

    nubi = FakeNubi()
    linhas = asyncio.run(extract.anuncios(nubi, termos=("ar condicionado",)))
    paginas = [a["inp"]["page"] for t, a in nubi.log if t == "meli_publication_explorer"]
    assert paginas == [1, 2, 3]                       # total real 4.057: só para na página vazia
    assert len(linhas) == 10 == len({x["item_id"] for x in linhas})


def test_grupos_paginam_e_concorrente_nao_duplica():
    from nubimetrics import extract

    class Nubi(FakeNubi):
        async def call(self, tool, args):
            self.log.append((tool, args))
            off = args["inp"].get("offset", 0)
            if tool == "nubi_competition_seller_groups":
                return {"groups": [{"group_id": 1}, {"group_id": 2}][off:off + 1], "has_more": off == 0}
            if tool == "nubi_competition_seller_group_members":
                return {"members": [{"member_code": "X"}, {"member_code": "Y", "is_self": True}], "has_more": False}
            return {"metrics": []}

    nubi = Nubi()
    saida = asyncio.run(extract.concorrente(nubi))
    assert [c["concorrente"] for c in saida] == ["X"]          # X está nos 2 grupos: 1 vez só
    assert sum(t == "nubi_competition_seller_groups" for t, _ in nubi.log) == 2


def test_rpc_troca_a_foto_e_falha_alto():
    cli = _Cliente()
    assert load.substituir_anuncios(cli, [{"item_hash": "a"}, {"item_hash": "b"}]) == 2
    assert cli.rpcs[0][0] == "nubi_substituir_anuncios" and len(cli.rpcs[0][1]["p_linhas"]) == 2

    class Recusa(_Cliente):
        def rpc(self, nome, params):
            raise RuntimeError("permission denied")

    with pytest.raises(load.LoadError):
        load.substituir_anuncios(Recusa(), [{"item_hash": "a"}])


def test_parquet_antes_do_banco_e_falha_vira_ressalva(monkeypatch):
    import nubimetrics.run_daily as run

    ordem = []
    monkeypatch.setattr(load, "parquet", lambda *a, **k: ordem.append("parquet") or None)
    monkeypatch.setattr(load, "cliente", lambda: ordem.append("banco") or _Cliente())
    dados = {"mercado": [], "rankings": [], "concorrente": [], "anuncios": [{"item_hash": "a"}], "avisos": []}
    run.gravar(dados, HOJE)
    assert ordem[:2] == ["parquet", "banco"]
    assert any("Parquet" in a for a in dados["avisos"])


def test_dry_run_nao_abre_batida(monkeypatch):
    import nubimetrics.client as client
    import nubimetrics.run_daily as run

    batidas = []
    monkeypatch.setattr(client, "NubiClient", lambda *a, **k: FakeNubi())
    monkeypatch.setattr(run, "_bater", lambda status, **k: batidas.append(status))
    assert run.main(["--dry-run"]) == run.EXIT_OK and batidas == []


def test_explorador_vazio_vira_ressalva(monkeypatch):
    import nubimetrics.client as client
    import nubimetrics.run_daily as run

    class Vazio(FakeNubi):
        async def call(self, tool, args):
            if tool == "meli_publication_explorer":
                return {"publications": [], "total": 0}
            return await super().call(tool, args)

    batidas = []
    monkeypatch.setattr(client, "NubiClient", lambda *a, **k: Vazio())
    monkeypatch.setattr(run, "gravar", lambda *a, **k: 0)
    monkeypatch.setattr(run, "_bater", lambda status, **k: batidas.append((status, k.get("detalhe", ""))))
    assert run.main([]) == run.EXIT_RESSALVA
    assert batidas[-1][0] == "PARTIAL" and "0 anúncios" in batidas[-1][1]


@pytest.mark.parametrize("titulo", [
    "Controle Remoto Universal Para Ar Condicionado Split",
    "Capa Para Ar Condicionado Split 12000",
    "Kit Instalação Ar Condicionado 3m",
    "Ventilador de Teto 3 Pás",
])
def test_acessorio_nao_entra_no_high_wall(titulo):
    assert classificar(titulo).segmento != SEG_HW_INVERTER


def test_atributo_no_meio_do_titulo_nao_vira_acessorio():
    assert classificar("Ar Condicionado Split Inverter 12000 Btus com Filtro de Prata").segmento == SEG_HW_INVERTER


def test_bivolt_em_par():
    assert parse_voltagem("Ar 110/220V") == parse_voltagem("127v e 220v") == "Bivolt"


def test_sub_linha_midea_conta_como_midea_e_sku_por_titulo():
    df = an.preparar_anuncios(pd.DataFrame([
        {"item_hash": "a", "marca": "Springer Midea", "modelo": "", "titulo": "Ar Split Midea Xtreme 12k",
         "seller": "S1", "exposicao": "gold_pro", "receita_brl": 10, "unidades": 1, "preco": 10,
         "faixa_btu": "12k", "catalogo": False, "full_": False},
    ]))
    assert df.loc[0, "marca"] == "MIDEA"
    assert an._sku_chave(df).iloc[0] == "Ar Split Midea Xtreme 12k"


def test_discover_nunca_chama_user_info_nem_feedback(monkeypatch, tmp_path):
    import contextlib
    import types

    import nubimetrics.client as client
    from nubimetrics import discover as disc

    def _tool(nome):
        return {"name": nome, "description": "", "annotations": {"readOnlyHint": True},
                "inputSchema": {"type": "object", "properties": {}}}

    chamadas = []

    class Sessao:
        async def list_tools(self, cursor=None):
            return types.SimpleNamespace(tools=[_tool("nubi_user_info"), _tool("nubi_user_feedback"),
                                                _tool("nubi_seller_info")], nextCursor=None)

        async def call_tool(self, nome, args):
            chamadas.append(nome)
            return {"content": [{"type": "text", "text": "{}"}], "isError": False}

    @contextlib.asynccontextmanager
    async def sessao_falsa(*a, **k):
        yield Sessao()

    monkeypatch.setattr(client, "open_session", sessao_falsa)
    monkeypatch.setattr(disc, "CATALOG_DIR", tmp_path)
    monkeypatch.setattr(disc, "SAMPLES_DIR", tmp_path / "samples")
    asyncio.run(disc.discover(False, {}, False))

    assert chamadas == ["nubi_seller_info"]
    status = json.loads((tmp_path / "samples_status.json").read_text(encoding="utf-8"))
    assert status["nubi_user_info"]["status"].startswith("omitida")
    assert status["nubi_user_feedback"]["status"].startswith("pulada")


def test_sub_linhas_midea_somam_no_ranking_de_marcas():
    bruto = {**amostra("meli_market_rankings__brands"), "period_fallback": False}
    rk = an.canonizar_marcas(pd.DataFrame(transform.ranking(bruto, dt.date(2026, 10, 1), "brands")))
    k = an.kpis_marca(rk, pd.Timestamp("2026-10-01"))
    # MIDEA 13,3% + SPRINGER MIDEA 4,23% + MIDEA BRANCA + CARRIER | MIDEA
    assert round(k["share"], 1) == 17.6 and k["posicao"] == 2
    assert not rk["nome"].str.contains("SPRINGER MIDEA").any()
    assert "CARRIER" in set(rk["nome"])                     # Carrier sozinho é outra marca
    assert sorted(rk["posicao"]) == list(range(1, len(rk) + 1))
    assert round(rk["share_brl"].sum(), 1) == 100.0
    midea = rk[rk["nome"] == "MIDEA"].iloc[0]
    # MIDEA BRANCA veio sem crescimento: o Δ do grupo fica indisponível, não parcial.
    assert pd.isna(midea["cresc_receita_pct"])
    assert pd.isna(midea["qtd_sellers"])                  # união de sellers não está no payload
    lg = rk[rk["nome"] == "LG"].iloc[0]
    assert lg["qtd_sellers"] == 46 and round(lg["cresc_receita_pct"], 2) == -6.94


def test_cliente_tem_ao_menos_uma_tentativa():
    from nubimetrics.client import NubiClient

    assert NubiClient(tentativas=0).tentativas == 1


# ── revisão do PR #449 (Cubic, 3ª passada) ──────────────────────────────────

def test_btu_do_modelo_ate_60k():
    assert btu_do_modelo("38CCU48M5") == 48000 and btu_do_modelo("38CCU60M5") == 60000
    assert btu_do_modelo("42XQV048515LC") is None          # 42 é série Carrier, não BTU


def test_zero_explicito_do_ranking_nao_vira_nulo():
    bruto = {"period_fallback": False, "items": [{"position": 1, "name": "X", "gmv": 0, "units": 0,
                                                  "sellers_quantity": 0, "items_quantity": 0}]}
    linha = transform.ranking(bruto, dt.date(2026, 10, 1), "brands")[0]
    assert linha["qtd_sellers"] == 0 and linha["qtd_anuncios"] == 0


class _SessaoMCP:
    """Sessão MCP falsa para o NubiClient (sem rede)."""

    def __init__(self, resultados):
        self.resultados = list(resultados)
        self.n = 0

    async def call_tool(self, tool, args):
        self.n += 1
        r = self.resultados.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _cliente_mcp(monkeypatch, tmp_path, resultados, **kw):
    import nubimetrics.client as client

    monkeypatch.setattr(client, "CACHE_DIR", tmp_path)
    nubi = client.NubiClient(intervalo_s=0, **kw)
    nubi._session = _SessaoMCP(resultados)
    return nubi


def _resultado(payload, erro=False):
    """CallToolResult de verdade do SDK `mcp` (mesma forma que a sessão devolve;
    no SDK 2.x o atributo é `is_error`, o alias JSON é `isError`)."""
    from mcp.types import CallToolResult, TextContent

    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))],
                          structuredContent=payload, isError=erro)


def test_cache_corrompido_vira_cache_vazio(monkeypatch, tmp_path):
    ok = _resultado({"a": 1})
    nubi = _cliente_mcp(monkeypatch, tmp_path, [ok])
    caminho = nubi._cache_path("t", {"x": 1})
    caminho.parent.mkdir(parents=True)
    caminho.write_bytes(b"\x1f\x8b truncado")
    assert asyncio.run(nubi.call("t", {"x": 1})) == {"a": 1}
    assert nubi._session.n == 1 and not caminho.with_suffix(".tmp").exists()
    assert asyncio.run(nubi.call("t", {"x": 1})) == {"a": 1} and nubi.do_cache == 1   # agora do cache


def test_erro_de_negocio_sai_mascarado(monkeypatch, tmp_path):
    from nubimetrics.client import NubiToolError

    erro = _resultado({"account_id": "uuid-secreto", "msg": "x"}, erro=True)
    nubi = _cliente_mcp(monkeypatch, tmp_path, [erro], cache=False)
    with pytest.raises(NubiToolError) as exc:
        asyncio.run(nubi.call("t", {}))
    assert "uuid-secreto" not in str(exc.value) and "***" in str(exc.value)


def test_ultima_tentativa_sobe_sem_esperar(monkeypatch, tmp_path):
    esperas = []

    async def _dorme(s):
        esperas.append(s)

    monkeypatch.setattr(asyncio, "sleep", _dorme)
    nubi = _cliente_mcp(monkeypatch, tmp_path, [OSError("rede")] * 3, cache=False, tentativas=3)
    with pytest.raises(RuntimeError):
        asyncio.run(nubi.call("t", {}))
    assert esperas == [2, 4]                       # nenhuma espera depois da 3ª falha


def test_historico_guarda_o_bruto_do_explorador(dados):
    h = dados["anuncios_historico"]
    assert len(h) == len(dados["anuncios"])
    assert {"raw_installments", "raw_oem", "raw_item_id", "segmento", "btu"} <= set(h[0])


# ── 1ª rodada de produção (13/11/2026): limite de 100 chamadas/min ──────────

def test_limite_de_taxa_espera_e_repete(monkeypatch, tmp_path):
    esperas = []

    async def _dorme(s):
        esperas.append(s)

    monkeypatch.setattr(asyncio, "sleep", _dorme)
    limite = _resultado("Rate limit exceeded: 100/minute. Retry after 1s.", erro=True)
    nubi = _cliente_mcp(monkeypatch, tmp_path, [limite, limite, _resultado({"ok": 1})], cache=False)
    assert asyncio.run(nubi.call("t", {})) == {"ok": 1}
    assert nubi.esperas_limite == 2 and esperas == [3.0, 5.0]       # retry_after + 2^n


def test_limite_de_taxa_persistente_desiste(monkeypatch, tmp_path):
    from nubimetrics.client import NubiToolError

    async def _dorme(s):
        return None

    monkeypatch.setattr(asyncio, "sleep", _dorme)
    limite = _resultado("Rate limit exceeded: 100/minute. Retry after 1s.", erro=True)
    nubi = _cliente_mcp(monkeypatch, tmp_path, [limite] * 10, cache=False)
    with pytest.raises(NubiToolError):
        asyncio.run(nubi.call("t", {}))
    assert nubi.esperas_limite == nubi.MAX_ESPERAS_LIMITE


def test_intervalo_padrao_fica_abaixo_de_100_por_minuto():
    from nubimetrics.client import NubiClient

    assert 60 / NubiClient().intervalo_s < 100


def test_explorador_falhando_nao_derruba_rankings(monkeypatch):
    import nubimetrics.client as client
    import nubimetrics.run_daily as run

    class Limite(FakeNubi):
        async def call(self, tool, args):
            if tool == "meli_publication_explorer":
                raise client.NubiToolError("meli_publication_explorer: Rate limit exceeded")
            return await super().call(tool, args)

    gravado = {}
    batidas = []
    monkeypatch.setattr(client, "NubiClient", lambda *a, **k: Limite())
    monkeypatch.setattr(run, "gravar", lambda dados, hoje: gravado.update(dados) or 1)
    monkeypatch.setattr(run, "_bater", lambda status, **k: batidas.append((status, k.get("detalhe", ""))))
    assert run.main([]) == run.EXIT_RESSALVA
    assert gravado["rankings"] and gravado["mercado"] and gravado["anuncios"] == []
    assert batidas[-1][0] == "PARTIAL" and "explorador de anúncios falhou" in batidas[-1][1]
    assert "0 anúncios" not in batidas[-1][1]               # a causa é a falha, não "veio vazio"


def test_sem_fonte_principal_falha_de_vez(monkeypatch):
    import nubimetrics.client as client
    import nubimetrics.run_daily as run

    class Fora(FakeNubi):
        async def call(self, tool, args):
            raise client.NubiToolError(f"{tool}: indisponível")

    monkeypatch.setattr(client, "NubiClient", lambda *a, **k: Fora())
    monkeypatch.setattr(run, "_bater", lambda *a, **k: None)
    assert run.main(["--dry-run"]) == run.EXIT_FALHA


def test_analises_toleram_foto_vazia():
    vazio = pd.DataFrame()
    assert an.gap_preco_midea_lg(vazio).empty
    assert an.cruzar_por_grupo(vazio, pd.DataFrame({"x": [1]})).empty


# ── 2ª rodada de produção (13/11/2026): "invalid input syntax for type integer" ──

def _colunas_inteiras() -> dict:
    """{tabela: {colunas INTEGER}} lidas da própria migração 023."""
    import re

    sql = (Path(__file__).resolve().parent.parent / "docs" / "migrations" / "023_nubimetrics.sql").read_text(
        encoding="utf-8")
    out = {}
    for tabela, corpo in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", sql, re.S):
        out[tabela] = set(re.findall(r"^\s+(\w+)\s+INTEGER\b", corpo, re.M))
    return out


def test_etl_nao_manda_decimal_para_coluna_inteira(monkeypatch):
    """O PostgREST recusa 2952.0 numa coluna INTEGER (o adaptador psycopg2 aceitava —
    por isso o teste local não pegou). Confere todo valor enviado ao banco."""
    import nubimetrics.client as client
    import nubimetrics.run_daily as run

    inteiras = _colunas_inteiras()
    assert inteiras["nubi_mercado_mensal"] >= {"sellers", "sellers_prof", "anuncios"}
    enviados = []

    class Estrito(_Cliente):
        def table(self, nome):
            cli = self

            class T(_Tabela):
                def upsert(self, lote, on_conflict):
                    enviados.extend((nome, linha) for linha in lote)
                    return self

            return T(cli.log)

        def rpc(self, nome, params):
            enviados.extend(("nubi_anuncios", linha) for linha in params["p_linhas"])
            return super().rpc(nome, params)

    monkeypatch.setattr(client, "NubiClient", lambda *a, **k: FakeNubi())
    monkeypatch.setattr(load, "cliente", lambda: Estrito())
    monkeypatch.setattr(load, "parquet", lambda *a, **k: "ok")
    dados = run.transformar(asyncio.run(run.extrair(13, True, HOJE)), HOJE)
    run.gravar(dados, HOJE)

    ruins = [(t, c, v) for t, linha in enviados for c, v in linha.items()
             if c in inteiras.get(t, ()) and isinstance(v, float)]
    assert not ruins, ruins[:5]
    assert {t for t, _ in enviados} >= {"nubi_mercado_mensal", "nubi_ranking_mensal", "nubi_anuncios"}


def test_backfill_tem_total_da_categoria_para_todo_mes_de_ranking(monkeypatch):
    import nubimetrics.client as client
    import nubimetrics.run_daily as run

    nubi = FakeNubi()
    monkeypatch.setattr(client, "NubiClient", lambda *a, **k: nubi)
    asyncio.run(run.extrair(13, False, HOJE))
    args = next(a["inp"] for t, a in nubi.log if t == "nubi_category_market_size")
    assert args["date_from"] == "2025-10-01"          # 13º mês fechado para trás (out/25)
