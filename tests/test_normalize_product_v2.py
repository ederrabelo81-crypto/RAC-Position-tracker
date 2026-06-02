"""
tests/test_normalize_product_v2.py — formato canônico v2 (SKU-anchored).

Formato:
    AR CONDICIONADO {FORMA} {BTU} BTU {CICLO} {LINHA} - {TIPO} - {MARCA}
                    [ - {VOLTAGEM}] [ - {SKU}]

Rode: pytest tests/test_normalize_product_v2.py
"""
import pytest

from utils.normalize_product import normalize_product_name_v2 as v2


class TestNormalizeProductV2:
    def test_exemplo_com_sku_e_voltagem(self):
        # Caso-âncora do pedido: SKU + voltagem do catálogo são anexados.
        got = v2(
            "Ar Condicionado Split 12.000 Btus Inverter Ai Ecomaster Frio "
            "42EZVCA12M5 Springer Midea",
            "Springer Midea",
            sku="42EZVCA12M5",
            voltagem="220V",
        )
        assert got == (
            "AR CONDICIONADO SPLIT 12000 BTU FRIO AI ECOMASTER - "
            "INVERTER - MIDEA - 220V - 42EZVCA12M5"
        )

    def test_sem_sku_omite_voltagem_e_sku(self):
        # Fallback escolhido: omitir voltagem+SKU quando não resolvidos.
        got = v2("Ar Condicionado Midea AI Ecomaster 12000 BTUs Frio", "Midea")
        assert got == "AR CONDICIONADO SPLIT 12000 BTU FRIO AI ECOMASTER - INVERTER - MIDEA"

    def test_lg_dual_inverter_voice(self):
        got = v2("Ar Split LG Ai Dual Inverter Voice 9000 Btus Frio Branco", "LG")
        assert got == (
            "AR CONDICIONADO SPLIT 9000 BTU FRIO DUAL INVERTER AI VOICE - INVERTER - LG"
        )

    def test_quente_frio(self):
        got = v2(
            "Ar Condicionado Samsung WindFree 12000 BTUs Quente/Frio 220V",
            "Samsung",
            sku="AR12TXXX",
            voltagem="220V",
        )
        assert got == (
            "AR CONDICIONADO SPLIT 12000 BTU QUENTE/FRIO WINDFREE AI - "
            "INVERTER - SAMSUNG - 220V - AR12TXXX"
        )

    def test_forma_nao_split(self):
        got = v2("Ar Condicionado Portatil Elgin 12000 BTUs Frio", "Elgin")
        assert got == "AR CONDICIONADO PORTATIL 12000 BTU FRIO - INVERTER - ELGIN"

    def test_marca_catalogo_sem_linha(self):
        # Marca conhecida mas linha não detectada → omite a linha.
        got = v2("Ar Condicionado Gree 9000 BTUs Frio", "Gree")
        assert got == "AR CONDICIONADO SPLIT 9000 BTU FRIO - INVERTER - GREE"

    def test_marca_desconhecida_retorna_none(self):
        assert v2("Geladeira Brastemp Frost Free 375L", "Brastemp") is None

    def test_sem_btu_retorna_none(self):
        assert v2("Ar Condicionado Midea Inverter Frio", "Midea") is None

    def test_none_e_vazio(self):
        assert v2(None) is None
        assert v2("") is None

    def test_on_off_serie_a1(self):
        # TCL Serie A1 é On/Off por linha (vide _identify_type).
        got = v2("Ar Condicionado TCL Serie A1 9000 BTUs Frio", "TCL")
        assert got == "AR CONDICIONADO SPLIT 9000 BTU FRIO SERIE A1 - ON/OFF - TCL"


if __name__ == "__main__":
    import sys
    raise SystemExit(pytest.main([__file__, "-v"]))
