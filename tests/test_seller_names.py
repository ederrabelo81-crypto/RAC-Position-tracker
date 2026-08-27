"""Testes do de-para canônico de sellers (`utils/seller_names.py`).

Todas as grafias deste arquivo são **reais**: vieram do share de buy box de
2026-08-20 a 2026-08-27 (120 sellers distintos), não de formato imaginado.
É essa a diferença entre um mapa que sobrevive à coleta e um que quebra nela.

Rodar:
    pytest tests/test_seller_names.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from utils.seller_names import (  # noqa: E402
    SELLER_GROUPS,
    canonical_names,
    normalize_seller_name,
    seller_key,
    variants_for,
)


class TestSellerKey:
    """A chave ignora tudo que muda entre marketplaces sem mudar o lojista."""

    @pytest.mark.parametrize("raw", ["Ar Certo", "ar-certo", "ARCERTO", " Ar  Certo "])
    def test_caixa_espaco_e_pontuacao_colapsam(self, raw):
        assert seller_key(raw) == "arcerto"

    @pytest.mark.parametrize("raw", ["Friopeças", "friopecas", "FRIO PEÇAS"])
    def test_acento_colapsa(self, raw):
        assert seller_key(raw) == "friopecas"

    def test_marca_registrada_nao_conta(self):
        assert seller_key("Frigelar®") == seller_key("Frigelar")

    def test_sufixo_societario_longo_cai(self):
        assert seller_key("Tudão Tech Ltda") == "tudaotech"
        assert seller_key("casamaeltda") == "casamae"

    def test_sufixo_curto_nao_cai(self):
        # "me"/"sa" ficaram FORA do corte de propósito: são terminações
        # comuns de nome comercial, e cortá-las colidiria sellers distintos.
        assert seller_key("Fast Home") == "fasthome"
        assert seller_key("Loja Ursa") == "lojaursa"

    @pytest.mark.parametrize("raw", [None, "", "   ", "!!!"])
    def test_vazio(self, raw):
        assert seller_key(raw) == ""


class TestGruposConfirmados:
    """Cada caso é um dealer que aparecia fatiado no share de buy box."""

    @pytest.mark.parametrize("raw", [
        "Webcontinental", "Webcontinental ES", "Webcontinental_ES",
        "Webcontinental Marketplace", "lojawebcontinentalmarketplace",
        "continentalcenter", "ContinentalCenter",
    ])
    def test_web_continental(self, raw):
        assert normalize_seller_name(raw) == "Web Continental"

    @pytest.mark.parametrize("raw", ["Clima Rio", "ClimaRio", "climario"])
    def test_clima_rio(self, raw):
        assert normalize_seller_name(raw) == "Clima Rio"

    @pytest.mark.parametrize("raw", ["Friopeças", "friopecas"])
    def test_frio_pecas(self, raw):
        assert normalize_seller_name(raw) == "Frio Peças"

    @pytest.mark.parametrize("raw", ["Centralar.com", "centralar", "Central Ar"])
    def test_central_ar(self, raw):
        assert normalize_seller_name(raw) == "Central Ar"

    @pytest.mark.parametrize("raw", ["Engage Eletro", "engageeletroful"])
    def test_engage(self, raw):
        assert normalize_seller_name(raw) == "Engage Eletro"

    @pytest.mark.parametrize("raw,esperado", [
        ("frigelar2", "Frigelar"), ("Frigelar®", "Frigelar"),
        ("leveros3", "Leveros"), ("fastshop2", "Fast Shop"),
        ("comprebel2", "Comprebel"), ("angeloni2", "Angeloni"),
    ])
    def test_sufixo_numerico_do_ml(self, raw, esperado):
        """O ML anexa um dígito quando o nickname já existe."""
        assert normalize_seller_name(raw) == esperado

    @pytest.mark.parametrize("raw,esperado", [
        ("Belmicro Oficial", "Bel Micro"), ("BELMICRO", "Bel Micro"),
        ("Denteck Ar Condicionado", "Denteck"), ("denteck", "Denteck"),
        ("A.Dias", "A.Dias"), ("adias", "A.Dias"), ("A DIAS", "A.Dias"),
        ("Efácil Oficial", "E-Fácil"), ("E-FÁCIL", "E-Fácil"),
        ("bagatolionline", "Bagatoli"), ("bagatolishop", "Bagatoli"),
        ("lojascolombooficial", "Lojas Colombo"),
        ("carrefouroficial", "Carrefour"), ("gazinshop", "Gazin"),
        ("loja-ultrafeu", "Ultrafeu"), ("lojatclsemp", "TCL SEMP"),
        ("lgelectronicsdobrasil", "LG"),
        ("refricrilrefrigeracaoepecas", "Refricril Refrigeração"),
        ("electrolux", "Electrolux"), ("samsung", "Samsung"),
    ])
    def test_demais_grupos(self, raw, esperado):
        assert normalize_seller_name(raw) == esperado


class TestSellerDesconhecidoPassa:
    """Seller sem identidade confirmada NÃO é chutado para grupo parecido."""

    @pytest.mark.parametrize("raw", [
        "mgshopgra", "GoCompras", "Turum", "Domus", "GHOX", "mg777",
        "multiloja", "Tudão Tech Ltda", "Loja da Ferramenta",
    ])
    def test_passa_inalterado(self, raw):
        assert normalize_seller_name(raw) == raw

    def test_climamix_nao_vira_clima_rio(self):
        # Prefixo "clima" é coincidência de string, não identidade de lojista.
        assert normalize_seller_name("CLIMAMIX") == "CLIMAMIX"
        assert normalize_seller_name("sdclimax") == "sdclimax"

    def test_bela_magazine_nao_vira_magazine_luiza(self):
        assert normalize_seller_name("Bela Magazine") == "Bela Magazine"

    def test_refriparts_nao_vira_refricril(self):
        assert normalize_seller_name("Refriparts") == "Refriparts"

    def test_limpeza_minima_sem_match(self):
        assert normalize_seller_name("  Loja   Nova ® ") == "Loja Nova"


class TestBordas:
    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_vazio_vira_none(self, raw):
        assert normalize_seller_name(raw) is None

    def test_idempotente(self):
        """Canonizar duas vezes não muda nada — a coleta grava canônico e a
        automação Admin reescreve o histórico por cima."""
        for canonical in canonical_names():
            assert normalize_seller_name(canonical) == canonical


class TestVariantsFor:
    def test_expande_para_o_filtro_do_dashboard(self):
        variantes = variants_for("Web Continental")
        assert "Web Continental" in variantes
        assert "continentalcenter" in [v.lower() for v in variantes]
        assert len(variantes) == len(set(variantes))  # sem repetição

    def test_aceita_variante_como_entrada(self):
        assert variants_for("friopecas") == variants_for("Frio Peças")

    def test_desconhecido_devolve_ele_mesmo(self):
        assert variants_for("mgshopgra") == ["mgshopgra"]

    def test_vazio(self):
        assert variants_for("") == []


class TestIntegridadeDoMapa:
    def test_canonico_nunca_colide_com_outro_grupo(self):
        """_build_lookup levanta na importação; aqui garantimos que continua
        valendo depois de qualquer edição do mapa."""
        chaves = {}
        for canonical, variants in SELLER_GROUPS.items():
            for v in (canonical, *variants):
                k = seller_key(v)
                assert chaves.setdefault(k, canonical) == canonical, (
                    f"{v!r} disputado por {chaves[k]!r} e {canonical!r}"
                )

    def test_todo_canonico_se_resolve_para_si(self):
        for canonical in SELLER_GROUPS:
            assert normalize_seller_name(canonical) == canonical


# ---------------------------------------------------------------------------
# Integração com o dashboard
# ---------------------------------------------------------------------------
# O de-para entra no app.py em DUAS pontas — leitura (canoniza) e filtro
# (re-expande para as grafias brutas). Errar uma delas devolve recorte vazio
# ao usuário, que é pior que a fragmentação original: some dado sem avisar.

import pandas as pd  # noqa: E402

import app  # noqa: E402  — importável sem renderizar


class TestHelpersDoDashboard:
    def test_canoniza_coluna_de_leitura(self):
        df = pd.DataFrame({
            "seller": ["friopecas", "continentalcenter", "mgshopgra"],
            "buy_box_seller": ["Frigelar®", "climario", "GoCompras®"],
        })
        out = app._apply_seller_canonical(df)
        assert out["seller"].tolist() == [
            "Frio Peças", "Web Continental", "mgshopgra",
        ]
        assert out["buy_box_seller"].tolist() == [
            "Frigelar", "Clima Rio", "GoCompras",
        ]

    @pytest.mark.parametrize("ausente", [None, float("nan"), pd.NA])
    def test_ausencia_nao_vira_seller_literal(self, ausente):
        """Sem a guarda, `str(pd.NA)` entraria no ranking como a loja "<NA>"."""
        assert pd.isna(app._canonical_seller(ausente))

    def test_coluna_sem_seller_passa_batido(self):
        df = pd.DataFrame({"produto": ["X"]})
        assert app._apply_seller_canonical(df).columns.tolist() == ["produto"]

    def test_filtro_reexpande_para_as_grafias_brutas(self):
        """O dropdown oferece "Web Continental"; o banco guarda as 5 grafias."""
        brutas = app._expand_sellers(["Web Continental"])
        assert "continentalcenter" in [b.lower() for b in brutas]
        assert "lojawebcontinentalmarketplace" in [b.lower() for b in brutas]
        assert len(brutas) >= 5

    def test_filtro_de_seller_desconhecido_nao_some(self):
        assert app._expand_sellers(["mgshopgra"]) == ["mgshopgra"]

    def test_dropdown_colapsa_as_grafias_em_uma_opcao(self):
        opcoes = app._canonical_seller_options([
            "Webcontinental", "continentalcenter", "Webcontinental ES",
            "friopecas", "Friopeças", None, "", "mgshopgra",
        ])
        assert opcoes == ["Frio Peças", "Web Continental", "mgshopgra"]

    def test_roundtrip_dropdown_para_banco(self):
        """Toda opção do dropdown reexpande para grafias que voltam a ela."""
        for canonical in app._canonical_seller_options(
            ["Webcontinental", "friopecas", "frigelar2", "Centralar.com"]
        ):
            for bruta in app._expand_sellers([canonical]):
                assert app._canonical_seller(bruta) == canonical
