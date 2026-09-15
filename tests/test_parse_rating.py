"""
tests/test_parse_rating.py — Prova de `utils.text.parse_rating`.

Achado em campo (Set/2026, carga do Passo 5 da migração Aiven): a bifurcação
decimal de `parse_rating` não tinha o teto de sanidade (0–5) que a bifurcação
inteira já tinha. Um texto malformado virava avaliação tipo 123.456 —
gravado no Parquet (histórico grava ANTES do banco), silenciosamente
rejeitado pelo upload antigo ao Supabase, e só apareceu ao recarregar o frio
num INSERT que não perdoa overflow de `avaliacao numeric(3,2)`.
"""
from utils.text import parse_rating


class TestParseRating:
    def test_formato_padrao_ponto(self):
        assert parse_rating("4.8") == 4.8

    def test_formato_padrao_virgula(self):
        assert parse_rating("4,8") == 4.8

    def test_com_parenteses(self):
        assert parse_rating("(4.8)") == 4.8

    def test_inteiro_valido(self):
        assert parse_rating("5") == 5.0

    def test_inteiro_acima_do_teto_rejeitado(self):
        assert parse_rating("12") is None

    def test_decimal_acima_do_teto_rejeitado(self):
        # A regressão: "123.456" batia no primeiro regex (dígitos.dígitos)
        # e voltava 123.456 sem checagem nenhuma.
        assert parse_rating("123.456") is None

    def test_decimal_no_limite_aceito(self):
        assert parse_rating("5.0") == 5.0

    def test_vazio(self):
        assert parse_rating("") is None

    def test_none(self):
        assert parse_rating(None) is None

    def test_sem_digito(self):
        assert parse_rating("sem avaliação") is None
