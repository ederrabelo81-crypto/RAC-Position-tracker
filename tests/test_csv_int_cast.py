"""
tests/test_csv_int_cast.py — Tipagem das colunas inteiras não pode derrubar o CSV.

Regressão do run "RAC Price Collection #174" (Ago/2026): depois de 1h22m de
scraping bem-sucedido (6.047 registros — 960 Google Shopping, 3.500 Amazon,
1.587 Leroy Merlin), a exportação morreu na primeira linha de tipagem:

    df["Qtd Avaliações"] = pd.to_numeric(...).astype("Int64")
    TypeError: cannot safely cast non-equivalent float64 to int64

`astype("Int64")` recusa qualquer valor que não sobreviva à ida e volta por
int64 — fracionário ou grande demais para caber em float64 sem perda. Como o
CSV era a primeira e única gravação, a exceção levou junto o histórico frio e o
Supabase: o dia inteiro se perdeu por causa de uma célula.

Cobre as duas pontas do conserto:
  - `main._to_nullable_int` — conversão tolerante (nunca levanta);
  - `utils.text.parse_review_count` — a origem, que agora devolve int sempre.

Rode: pytest tests/test_csv_int_cast.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import _INT64_SAFE_MAX, _dump_raw_records, _to_nullable_int  # noqa: E402
from utils.text import MAX_COUNT_SANE, parse_review_count  # noqa: E402


# ---------------------------------------------------------------------------
# _to_nullable_int — a conversão que o _export_csv usa
# ---------------------------------------------------------------------------

class TestToNullableInt:
    """Cada entrada plausível vira Int64 ou nulo — nunca uma exceção."""

    def test_inteiro(self):
        resultado = _to_nullable_int(pd.Series([1, 2, 3]))
        assert resultado.dtype == "Int64"
        assert resultado.tolist() == [1, 2, 3]

    def test_float_fracionario_e_arredondado(self):
        """O caso que a tarefa descreve: "1,2 mil" parseado como 1.2."""
        resultado = _to_nullable_int(pd.Series([1.2, 2.5, 3.4], dtype=object))
        assert resultado.dtype == "Int64"
        # round() do pandas é banker's rounding: 2.5 → 2.
        assert resultado.tolist() == [1, 2, 3]

    def test_string_com_separador_de_milhar(self):
        resultado = _to_nullable_int(pd.Series(["1.234", "5678"]))
        assert resultado.dtype == "Int64"
        # "1.234" é ambíguo para o pandas (lê como decimal) — o importante é
        # que ele NÃO derrube a exportação; o valor certo vem do parser.
        assert resultado.tolist() == [1, 5678]

    def test_none_e_nan(self):
        resultado = _to_nullable_int(pd.Series([None, float("nan"), 7]))
        assert resultado.dtype == "Int64"
        assert resultado.isna().tolist() == [True, True, False]
        assert resultado.tolist()[2] == 7

    def test_string_nao_numerica_vira_nulo(self):
        resultado = _to_nullable_int(pd.Series(["mil", "n/d", "12"]))
        assert resultado.dtype == "Int64"
        assert resultado.isna().tolist() == [True, True, False]
        assert resultado.tolist()[2] == 12

    def test_inteiro_alem_da_precisao_de_float64_vira_nulo(self):
        """O valor que produziu a mensagem exata do traceback do run #174.

        Um inteiro que não cabe em int64 força a coluna a float64 e perde
        precisão; o `astype("Int64")` então recusa a volta com
        "cannot safely cast non-equivalent float64 to int64". Uma célula vazia
        é o preço certo a pagar — a coleta inteira, não.
        """
        gigante = _INT64_SAFE_MAX ** 2  # muito além do teto de int64
        resultado = _to_nullable_int(pd.Series([gigante, 42], dtype=object))
        assert resultado.dtype == "Int64"
        assert bool(resultado.isna().iloc[0])
        assert resultado.iloc[1] == 42

    def test_valor_do_traceback_original_nao_levanta(self):
        """Reproduz a coluna exata que abortou o run #174.

        Sem o conserto, esta série levanta
        ``TypeError: cannot safely cast non-equivalent float64 to int64``.
        """
        envenenada = pd.Series([4451243456789012345678, None, 12], dtype=object)
        with pytest.raises(TypeError):
            pd.to_numeric(envenenada, errors="coerce").astype("Int64")
        resultado = _to_nullable_int(envenenada, "Qtd Avaliações")
        assert resultado.dtype == "Int64"
        assert resultado.iloc[2] == 12

    def test_serie_vazia(self):
        resultado = _to_nullable_int(pd.Series([], dtype=object))
        assert resultado.dtype == "Int64"
        assert resultado.empty

    def test_mistura_realista_nao_levanta(self):
        """A coluna real chega como object com tudo misturado."""
        bruto = pd.Series(
            [10, None, 1.2, "1.234", "sem avaliações", 4_500, float("inf")],
            dtype=object,
        )
        resultado = _to_nullable_int(bruto, "Qtd Avaliações")
        assert resultado.dtype == "Int64"
        assert resultado.iloc[0] == 10
        assert resultado.iloc[5] == 4_500


def test_export_csv_sobrevive_a_coluna_envenenada(tmp_path):
    """O teste de ponta a ponta do incidente: o CSV precisa sair mesmo assim."""
    from main import _export_csv

    registros = [
        {
            "Data": "2026-08-04",
            "Plataforma": "Amazon",
            "Produto / SKU": "Ar Condicionado Midea 12000 BTUs",
            "Preço (R$)": 1994.91,
            "Avaliação": 4.7,
            "Qtd Avaliações": 1.2,                 # fracionário
            "Qtd Sellers": _INT64_SAFE_MAX ** 2,   # fora da faixa de int64
        },
        {
            "Data": "2026-08-04",
            "Plataforma": "Leroy Merlin",
            "Produto / SKU": "Ar Condicionado Springer 9000 BTUs",
            "Preço (R$)": 2184.05,
            "Avaliação": None,
            "Qtd Avaliações": 348,
            "Qtd Sellers": 3,
        },
    ]

    caminho = _export_csv(registros, str(tmp_path))

    assert caminho.exists()
    df = pd.read_csv(caminho, sep=";", encoding="utf-8-sig")
    assert len(df) == 2
    # Linha boa preservada; linha envenenada virou célula vazia, não exceção.
    assert df["Qtd Avaliações"].tolist()[1] == 348
    assert pd.isna(df["Qtd Sellers"].tolist()[0])


# ---------------------------------------------------------------------------
# Dump bruto — a rede de segurança que não depende de formatação
# ---------------------------------------------------------------------------

class TestDumpBruto:
    """Grava antes de qualquer tipagem, e no formato que a recuperação lê."""

    def test_grava_o_que_derrubaria_o_csv_formatado(self, tmp_path):
        registros = [
            {
                "Data": "2026-08-04",
                "Plataforma": "Amazon",
                "Qtd Avaliações": 1.2,                # fracionário
                "Qtd Sellers": _INT64_SAFE_MAX ** 2,  # fora da faixa
            }
        ]

        caminho = _dump_raw_records(registros, str(tmp_path), "run-abc")

        assert caminho.name == "raw_run-abc.csv"
        conteudo = caminho.read_text(encoding="utf-8-sig")
        assert "1.2" in conteudo
        assert str(_INT64_SAFE_MAX ** 2) in conteudo

    def test_preserva_campos_fora_do_column_order(self, tmp_path):
        """O CSV formatado descarta esses campos; o bruto é a cópia fiel."""
        registros = [{"Plataforma": "Magalu", "Produto Normalizado": "SPLIT 12K"}]

        caminho = _dump_raw_records(registros, str(tmp_path), "run-extra")

        df = pd.read_csv(caminho, sep=";", encoding="utf-8-sig")
        assert df["Produto Normalizado"].tolist() == ["SPLIT 12K"]

    def test_dialeto_igual_ao_da_rota_de_recuperacao(self, tmp_path):
        """`scripts/upload_csv.py` precisa conseguir ler o dump sem adaptação."""
        from scripts.upload_csv import _load_csv

        registros = [
            {
                "Data": "2026-08-04",
                "Turno": "Abertura",
                "Plataforma": "Leroy Merlin",
                "Marca Monitorada": "Midea",
                "Produto / SKU": "Ar Condicionado Midea 12000 BTUs",
                "Preço (R$)": 1994.91,
            }
        ]

        caminho = _dump_raw_records(registros, str(tmp_path), "run-load")
        carregado = _load_csv(caminho)

        assert len(carregado) == 1
        assert carregado[0]["Plataforma"] == "Leroy Merlin"


# ---------------------------------------------------------------------------
# parse_review_count — a origem do valor não-inteiro
# ---------------------------------------------------------------------------

class TestParseReviewCount:
    """O parser devolve inteiro ou None — jamais float, jamais dígito colado."""

    @pytest.mark.parametrize(
        "bruto, esperado",
        [
            ("(1.234)", 1234),
            ("1234 avaliações", 1234),
            ("1,234", 1234),
            ("1 234 avaliações", 1234),
            ("+5 ofertas", 5),
            ("(0)", 0),
        ],
    )
    def test_formatos_simples(self, bruto, esperado):
        assert parse_review_count(bruto) == esperado

    @pytest.mark.parametrize(
        "bruto, esperado",
        [
            ("1,2 mil", 1200),
            ("1,2 mil avaliações", 1200),
            ("2 mil avaliações", 2000),
            ("1.5k reviews", 1500),
            ("1,2 mi avaliações", 1_200_000),
        ],
    )
    def test_sufixos_de_escala_viram_inteiro(self, bruto, esperado):
        """"1,2 mil" era 12 (dígitos colados); agora é 1200, e é int."""
        resultado = parse_review_count(bruto)
        assert resultado == esperado
        assert isinstance(resultado, int)

    @pytest.mark.parametrize(
        "bruto, esperado",
        [
            ("4,7 de 5 estrelas, 12.345 classificações", 12345),
            ("4,4 de 5 estrelas de 1.243 avaliações de produtos", 1243),
            ("4,7 out of 5 stars, 1,234 ratings", 1234),
        ],
    )
    def test_nota_nao_entra_na_contagem(self, bruto, esperado):
        """A causa raiz: os dígitos da nota eram concatenados aos da contagem.

        "4,7 de 5 estrelas, 12.345 classificações" virava 47512345 — inflando
        a métrica e, em textos maiores, estourando a precisão de float64.
        """
        assert parse_review_count(bruto) == esperado

    @pytest.mark.parametrize("bruto", ["", None, "abc", "   "])
    def test_sem_numero_vira_none(self, bruto):
        assert parse_review_count(bruto) is None

    def test_valor_absurdo_e_descartado(self):
        """Dígitos colados demais não podem virar contagem 'válida'."""
        assert parse_review_count("12.345.678.901.234.567.890 avaliações") is None

    def test_teto_de_sanidade_e_inclusivo(self):
        assert parse_review_count(str(MAX_COUNT_SANE)) == MAX_COUNT_SANE
        assert parse_review_count(str(MAX_COUNT_SANE + 1)) is None
