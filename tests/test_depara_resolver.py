"""
tests/test_depara_resolver.py — Testes da auto-resolução do de-para.

Casos derivados de nomes reais da fila REVISAR (Jun/2026), incluindo os que o
classificador antigo (regex inline de montar_depara) deixava escapar.
"""
import pytest

from utils.depara_resolver import CatalogFamilias, resolve_depara

# Mapa mínimo de catálogo p/ exercitar a promoção de família-linha.
#   (marca, BTU, ciclo) → {familia_linha}
_CATALOG: CatalogFamilias = {
    ("MIDEA", 9000, "QF"): {"MIDEA-ECOMASTER-9000-QF", "MIDEA-AI-AIRVOLUTION-9000-QF"},
    ("AGRATTO", 24000, "F"): {"AGRATTO-LIV-TOP-24000-F"},
    # Elgin 9000 F só tem Eco Inverter II — um produto "Eco Dream" NÃO deve
    # ser promovido para essa linha (mislabel).
    ("ELGIN", 9000, "F"): {"ELGIN-ECO-INVERTER-II-9000-F"},
}


class TestResolveBrandBtuCycle:
    def test_midea_ecomaster_quente_frio(self):
        # Caso clássico que caía em REVISAR com marca NULL no classificador antigo
        r = resolve_depara(
            "Ar Condicionado Midea AI Ecomaster 9.000 BTUs Inverter Quente/Frio"
        )
        assert r.estado == "MAPEADO"
        assert r.marca_norm == "MIDEA"
        assert r.familia == "MIDEA-9000-QF"
        assert r.sku is None

    def test_samsung_windfree_pro(self):
        r = resolve_depara(
            "Ar Condicionado Samsung WindFree AI Pro 12.000 BTUs Inverter Quente/Frio"
        )
        assert r.estado == "MAPEADO"
        assert r.marca_norm == "SAMSUNG"
        assert r.familia == "SAMSUNG-12000-QF"

    def test_lg_dual_inverter_only_frio_default(self):
        # Sem palavra de ciclo → default Frio
        r = resolve_depara("Ar-condicionado LG Dual Inverter Voice +ai 9000btu")
        assert r.estado == "MAPEADO"
        assert r.marca_norm == "LG"
        assert r.familia == "LG-9000-F"

    def test_brand_inferred_from_line_name(self):
        # "ecomaster" sozinho identifica Midea mesmo sem a marca no texto
        r = resolve_depara("Split Hi Wall Inverter Ai Ecomaster 24000 Frio")
        assert r.estado == "MAPEADO"
        assert r.marca_norm == "MIDEA"
        assert r.familia == "MIDEA-24000-F"

    def test_btu_format_glued_btus(self):
        # "9000btus" colado — a regex antiga (\b9000\b) não pegava; a nova sim
        r = resolve_depara("Ar-condicionado Split 9000btus Gree Inverter Frio")
        assert r.estado == "MAPEADO"
        assert r.marca_norm == "GREE"
        assert r.familia == "GREE-9000-F"

    def test_btu_format_dot_glued_btus(self):
        # "18.000Btus" ponto+colado — exige o lookahead (?=\D) na remoção de ponto
        r = resolve_depara("Ar Condicionado 18.000Btus Inverte 42AFVCI18S5 Midea")
        assert r.estado == "MAPEADO"
        assert r.marca_norm == "MIDEA"
        assert r.familia == "MIDEA-18000-F"

    def test_btu_glued_to_cycle_letters_is_limitation(self):
        # Limitação conhecida: "12kqf220v" gruda BTU+ciclo+voltagem sem fronteira
        # de palavra; `_extract_btus_value` não lê o "12k" → permanece REVISAR.
        r = resolve_depara("Ar Condicionado Split Hw Lg Dual Voice Ai Inverter 12kqf220v")
        assert r.estado == "REVISAR"
        assert r.marca_norm == "LG"


_CATALOG_BTUS = {9000, 12000, 18000, 22000, 23000, 24000, 27000, 30000, 60000}


class TestBtuGate:
    def test_btu_outside_catalog_is_fora_escopo(self):
        # 55.000 BTU não existe no catálogo Hi-Wall residencial → FORA_ESCOPO
        r = resolve_depara(
            "Ar Condicionado Agratto 55.000 BTUs Inverter Frio",
            catalog_btus=_CATALOG_BTUS,
        )
        assert r.estado == "FORA_ESCOPO"
        assert r.marca_norm == "AGRATTO"

    def test_btu_7500_outside_catalog(self):
        r = resolve_depara(
            "Ar Condicionado Midea 7.500 BTUs Inverter Frio",
            catalog_btus=_CATALOG_BTUS,
        )
        assert r.estado == "FORA_ESCOPO"

    def test_btu_inside_catalog_stays_mapeado(self):
        r = resolve_depara(
            "Ar Condicionado Midea 12.000 BTUs Inverter Frio",
            catalog_btus=_CATALOG_BTUS,
        )
        assert r.estado == "MAPEADO"
        assert r.familia == "MIDEA-12000-F"

    def test_no_btu_gate_keeps_old_behavior(self):
        # Sem catalog_btus, mantém MAPEADO (compat. com chamadas sem o gate)
        r = resolve_depara("Ar Condicionado Agratto 55.000 BTUs Inverter Frio")
        assert r.estado == "MAPEADO"


class TestForaEscopo:
    def test_brand_outside_catalog(self):
        r = resolve_depara("Ar-condicionado Split Daikin EcoSwing 12000 Frio")
        assert r.estado == "FORA_ESCOPO"
        assert r.marca_norm == "DAIKIN"
        assert r.familia is None

    def test_britania_outside_catalog(self):
        r = resolve_depara("Ar Condicionado Britânia Prime Air 9.000 BTUs Inverter Frio")
        assert r.estado == "FORA_ESCOPO"
        assert r.marca_norm == "BRITANIA"

    def test_extra_non_catalog_brands_not_in_normalize_product(self):
        # Marcas que _identify_brand não conhece, mas o classificador antigo
        # tratava como FORA_ESCOPO — não devem regredir para REVISAR.
        for nome, marca in [
            ("Ar Condicionado Split Aiwa 12000 BTUs Frio", "AIWA"),
            ("Ar Condicionado 9.000 Btus Quente E Frio Split Chigo", "CHIGO"),
            ("Ar Condicionado Split 9.000 Btus Fontaine Frio FCST9F", "FONTAINE"),
            ("Ar Condicionado Split 24000 Btus Master Inverter EOS 220V", "EOS"),
        ]:
            r = resolve_depara(nome)
            assert r.estado == "FORA_ESCOPO", nome
            assert r.marca_norm == marca, nome


class TestRevisar:
    def test_no_brand_stays_revisar(self):
        r = resolve_depara("Ar Condicionado 12.000 Btus Frio 45hifi12c2wa Branco")
        assert r.estado == "REVISAR"

    def test_catalog_brand_without_btu_stays_revisar(self):
        r = resolve_depara("Ar condicionado inverter Springer Midea xtreme")
        assert r.estado == "REVISAR"
        assert r.marca_norm == "MIDEA"  # marca detectada, mas sem BTU

    def test_empty_name(self):
        assert resolve_depara("").estado == "REVISAR"
        assert resolve_depara("   ").estado == "REVISAR"


class TestCatalogPromotion:
    def test_recognized_line_promotes_to_familia_linha(self):
        r = resolve_depara(
            "Ar Condicionado Agratto Liv Top 24.000 BTUs Frio",
            catalog_familias=_CATALOG,
        )
        assert r.estado == "MAPEADO"
        assert r.familia == "AGRATTO-LIV-TOP-24000-F"

    def test_recognized_line_among_several_promotes(self):
        # "Ecomaster" é reconhecido e casa só com a linha ECOMASTER do catálogo
        r = resolve_depara(
            "Ar Condicionado Midea AI Ecomaster 9.000 BTUs Quente/Frio",
            catalog_familias=_CATALOG,
        )
        assert r.familia == "MIDEA-ECOMASTER-9000-QF"

    def test_line_not_in_catalog_stays_generic_no_mislabel(self):
        # "Eco Dream" não casa com "Eco Inverter II" → NÃO promove (sem mislabel)
        r = resolve_depara(
            "Ar Condicionado Eco Dream 9.000 BTUs Frio Elgin",
            catalog_familias=_CATALOG,
        )
        assert r.estado == "MAPEADO"
        assert r.familia == "ELGIN-9000-F"

    def test_no_catalog_map_falls_back_to_generic(self):
        r = resolve_depara("Ar Condicionado Agratto Liv Top 24.000 BTUs Frio")
        assert r.familia == "AGRATTO-24000-F"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
