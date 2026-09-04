"""
Contrato da classificação de superfície (Fase 1 do Track Position Seller).

O que estes testes protegem, em uma frase: que loja própria nunca entre no
denominador de buy box. Nas lojas próprias o `buy_box_seller` vem preenchido em
100% das linhas e o vencedor é sempre o dono do site — somar isso ao win rate
faria o dealer aparecer ganhando 100% de um campeonato que ele joga sozinho.
"""
import pytest

from utils import seller_surface as ss


class TestClassificacao:
    def test_marketplace_disputa_buybox(self):
        for plataforma in ("Amazon", "Mercado Livre", "Magalu", "Casas Bahia",
                           "Shopee", "Leroy Merlin"):
            assert ss.superficie_de(plataforma) == ss.SUPERFICIE_MARKETPLACE
            assert ss.disputa_buybox(plataforma) is True

    def test_loja_propria_nao_disputa(self):
        for plataforma in ("Dufrio", "Frigelar", "CentralAr", "Leveros",
                           "WebContinental", "PoloAr", "ArCerto"):
            assert ss.superficie_de(plataforma) == ss.SUPERFICIE_LOJA_PROPRIA
            assert ss.disputa_buybox(plataforma) is False

    def test_comparador_nao_e_marketplace(self):
        # O Google Shopping não tem buy box: lista lojas concorrendo por clique.
        # Somá-lo a marketplace misturaria duas perguntas diferentes.
        assert ss.superficie_de("Google Shopping") == ss.SUPERFICIE_COMPARADOR
        assert ss.disputa_buybox("Google Shopping") is False

    def test_webcontinental_nao_segue_o_tipo_da_plataforma(self):
        # WebContinental e Leroy Merlin compartilham o mesmo PLATFORM_TYPE
        # ("Nacional Varejo Especializado"). Derivar a superfície dali colocaria
        # uma loja própria no denominador de buy box.
        assert ss.superficie_de("WebContinental") == ss.SUPERFICIE_LOJA_PROPRIA
        assert ss.superficie_de("Leroy Merlin") == ss.SUPERFICIE_MARKETPLACE

    def test_plataforma_desconhecida_erra_para_o_lado_seguro(self):
        # Plataforma nova que ninguém classificou fica FORA do win rate.
        assert ss.superficie_de("LojaQueNinguemClassificou") == ss.SUPERFICIE_LOJA_PROPRIA
        assert ss.disputa_buybox("LojaQueNinguemClassificou") is False


class TestRegistro:
    def test_validar_registro_passa(self):
        ss.validar_registro()

    def test_mapa_cobre_as_tres_superficies(self):
        valores = set(ss.mapa_superficies().values())
        assert valores == {ss.SUPERFICIE_MARKETPLACE,
                           ss.SUPERFICIE_LOJA_PROPRIA,
                           ss.SUPERFICIE_COMPARADOR}

    def test_nenhuma_plataforma_em_duas_superficies(self):
        assert not (ss.MARKETPLACES & ss.LOJAS_PROPRIAS)
        assert not (ss.COMPARADORES & ss.LOJAS_PROPRIAS)
        assert not (ss.MARKETPLACES & ss.COMPARADORES)

    def test_dealer_listado_como_marketplace_reprova(self, monkeypatch):
        # A regressão que mais custa caro: um dealer promovido a marketplace
        # volta ao denominador de buy box e infla o share de todo mundo.
        monkeypatch.setattr(ss, "MARKETPLACES", ss.MARKETPLACES | {"Dufrio"})
        with pytest.raises(ValueError, match="duas superfícies"):
            ss.validar_registro()

    def test_divergencia_com_dealer_configs_reprova(self, monkeypatch):
        monkeypatch.setattr(ss, "_dealers_do_coletor",
                            lambda: frozenset(ss.LOJAS_PROPRIAS) | {"DealerNovo"})
        with pytest.raises(ValueError, match="divergiu"):
            ss.validar_registro()

    def test_sem_stack_de_coleta_a_conferencia_e_pulada(self, monkeypatch):
        # No app magro do seller o import de DEALER_CONFIGS falha; a validação
        # não pode quebrar por isso, mas também não pode dar por conferido.
        monkeypatch.setattr(ss, "_dealers_do_coletor", lambda: None)
        ss.validar_registro()
