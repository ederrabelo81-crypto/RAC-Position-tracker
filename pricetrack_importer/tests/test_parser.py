"""Testes do parser de .md (e equivalência com .xlsx quando openpyxl está disponível)."""
from pathlib import Path

import pytest

from pricetrack_importer.parser import parse_file


FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_MD = FIXTURES / "sample.md"


def _data_rows(rows):
    """Filtra cabeçalho/separador, deixando só as 18 linhas de dados + 2 metadata."""
    return [
        r for r in rows
        if not r.get("_is_header") and not r.get("_unparseable")
    ]


class TestParserMarkdown:
    def test_parsea_arquivo_sample(self):
        rows = list(parse_file(SAMPLE_MD))
        # Cabeçalho + 18 linhas de dados + 2 linhas de metadata = 21 (separador é
        # ignorado como None pela _split_pipe_row e produz None → "unparseable")
        assert len(rows) >= 18

    def test_nao_perde_linhas_com_pipe_no_titulo(self):
        """Critério de aceite: pipe no título não deve perder a linha."""
        rows = list(parse_file(SAMPLE_MD))
        # A linha com `Ar Condicionado | Midea 9000 BTUs | Frio | 220V` tem 3 pipes extra
        # no título. O parser deve recuperá-la.
        amazon_rows = [
            r for r in rows
            if r.get("marketplace", "").upper() == "AMAZON.COM.BR"
        ]
        assert len(amazon_rows) == 1
        row = amazon_rows[0]
        assert row["sku"] == "38AFVQA09M5"
        # O título deve incluir os pipes (ou conteúdo que estava entre eles)
        assert "Midea" in row["title"]
        assert "220V" in row["title"]
        # E os preços devem estar corretos
        assert row["MIN PRICE"] == "1999.00"
        assert row["MAX PRICE"] == "2199.00"

    def test_pipe_in_title(self):
        """Critério de aceite nomeado explicitamente no prompt."""
        rows = list(parse_file(SAMPLE_MD))
        pipe_rows = [r for r in rows if "|" in r.get("title", "")]
        assert len(pipe_rows) >= 1

    def test_data_em_formato_mdyy(self):
        rows = list(parse_file(SAMPLE_MD))
        data_rows = [r for r in rows if r.get("collectionDate") == "5/27/26"]
        assert len(data_rows) >= 15

    def test_seller_corrompido_aparece_no_parse(self):
        """O parser não filtra — apenas o validator. Aqui só conferimos que ele
        passa adiante."""
        rows = list(parse_file(SAMPLE_MD))
        sellers = [r.get("seller", "") for r in rows]
        assert any("- 220V" in s for s in sellers)
        assert any(s == "(ZQK215BB)" for s in sellers)
        assert any(s == "530290740" for s in sellers)

    def test_metadata_lines_passam(self):
        """Linhas de metadata no fim do arquivo passam adiante com collectionDate
        inválido/vazio — o validator é quem rejeita."""
        rows = list(parse_file(SAMPLE_MD))
        # "Filtros aplicados:" e "Total" vêm com collectionDate vazio (sem pipes)
        # e a linha separadora `|---|...|---|` também
        metadata_like = [
            r for r in rows
            if not r.get("collectionDate") or r.get("_unparseable") or r.get("_is_header")
        ]
        assert len(metadata_like) >= 2  # rodapé Filtros + Total no mínimo


class TestParserXlsx:
    """Testes que dependem de openpyxl. Pulam se não estiver instalado."""

    @pytest.fixture
    def sample_xlsx(self, tmp_path):
        openpyxl = pytest.importorskip("openpyxl")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append([
            "collectionDate", "brand", "sku", "title",
            "marketplace", "seller",
            "MIN PRICE", "AVG PRICE", "MODE PRICE", "MAX PRICE",
        ])
        ws.append([
            "5/27/26", "MIDEA", "38EZVQA12M5", "Ar Condicionado Midea 12000 BTUs",
            "MERCADO LIVRE", "FRIOPEÇAS",
            2499.00, 2599.00, 2599.00, 2799.00,
        ])
        ws.append([
            "5/27/26", "MIDEA", "38AFVCI18S5", "Ar Midea Inverter 18000 BTUs",
            "AMAZON", "AMAZON.COM.BR",
            5199.00, 5299.00, 5299.00, 5399.00,
        ])
        path = tmp_path / "sample.xlsx"
        wb.save(path)
        return path

    def test_xlsx_basico(self, sample_xlsx):
        rows = list(parse_file(sample_xlsx))
        assert len(rows) == 2
        assert rows[0]["sku"] == "38EZVQA12M5"
        assert rows[0]["brand"] == "MIDEA"
        assert rows[1]["marketplace"] == "AMAZON"

    def test_xlsx_equivalent_to_md(self, sample_xlsx, tmp_path):
        """Critério de aceite: schema parseado de .xlsx == .md."""
        rows_xlsx = list(parse_file(sample_xlsx))
        required_keys = {
            "collectionDate", "brand", "sku", "title",
            "marketplace", "seller",
            "MIN PRICE", "AVG PRICE", "MODE PRICE", "MAX PRICE",
        }
        for r in rows_xlsx:
            assert required_keys.issubset(set(r.keys()))

    def test_xlsx_collection_date_como_datetime_nativo(self, tmp_path):
        """
        Regressão: PriceTrack exporta .xlsx com `collectionDate` como data
        nativa do Excel. openpyxl devolve `datetime` → `str(v)` produz
        `"2026-05-27 00:00:00"` que NÃO casa com o regex M/D/YY do validator
        e marca tudo como METADATA (bug observado em 28/05/2026).
        """
        import datetime as dt

        openpyxl = pytest.importorskip("openpyxl")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append([
            "collectionDate", "brand", "sku", "title",
            "marketplace", "seller",
            "MIN PRICE", "AVG PRICE", "MODE PRICE", "MAX PRICE",
        ])
        ws.append([
            dt.datetime(2026, 5, 27), "MIDEA", "38EZVQA12M5",
            "Ar Condicionado Midea 12000 BTUs",
            "MERCADO LIVRE", "FRIOPEÇAS",
            2499.00, 2599.00, 2599.00, 2799.00,
        ])
        path = tmp_path / "with_native_date.xlsx"
        wb.save(path)

        rows = list(parse_file(path))
        assert len(rows) == 1
        # Tem que sair no formato M/D/YY pro validator aceitar
        assert rows[0]["collectionDate"] == "5/27/26"
