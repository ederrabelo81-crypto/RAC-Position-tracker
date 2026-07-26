"""
tests/test_csv_nan_guard.py — Célula vazia de CSV não pode derrubar o import.

Regressão de 26/07/2026, encontrada importando artifacts reais para o
histórico: uma linha com "Produto / SKU" vazio chegava como ``float('nan')``,
que é **truthy**, passava pelo `if base_name:` de `_map_record` e estourava
``AttributeError: 'float' object has no attribute 'lower'``, abortando o
import inteiro no meio.

Atingia os dois caminhos de CSV — `history_cli.py import-csv` e o
`scripts/upload_csv.py`, que é justamente a rota de recuperação documentada.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.upload_csv import _load_csv  # noqa: E402
from utils.normalize_product import (  # noqa: E402
    normalize_product_name,
    normalize_product_name_v2,
)
from utils.supabase_client import map_record  # noqa: E402

_NAN = float("nan")


@pytest.fixture
def csv_com_celula_vazia(tmp_path) -> Path:
    """CSV no formato de produção, com a coluna de produto vazia numa linha."""
    caminho = tmp_path / "rac_monitoramento_20260708_1925.csv"
    caminho.write_text(
        "Data;Turno;Plataforma;Marca Monitorada;Produto / SKU;Preço (R$)\n"
        "2026-07-08;Abertura;Magalu;Midea;Ar Condicionado Midea 12000 BTUs;2199,90\n"
        "2026-07-08;Abertura;Magalu;Midea;;1899,00\n",
        encoding="utf-8-sig",
    )
    return caminho


class TestNormalizadoresRejeitamNaoTexto:
    """`nan` é truthy — o guarda tem de ser de TIPO, não de veracidade."""

    def test_v1_com_nan(self):
        assert normalize_product_name(_NAN) is None

    def test_v2_com_nan(self):
        assert normalize_product_name_v2(_NAN) is None

    def test_v1_com_none(self):
        assert normalize_product_name(None) is None

    def test_v2_com_none(self):
        assert normalize_product_name_v2(None) is None

    def test_texto_valido_segue_normalizando(self):
        assert normalize_product_name_v2("Ar Condicionado Midea 12000 BTUs") is not None


class TestMapRecordSobreviveANaN:
    def test_produto_nan_nao_levanta(self):
        row = map_record({"Data": "2026-07-08", "Produto / SKU": _NAN})
        assert row["produto"] is None
        assert row["produto_normalizado"] is None

    def test_linha_boa_continua_normalizando(self):
        row = map_record({
            "Data": "2026-07-08",
            "Produto / SKU": "Ar Condicionado Midea 12000 BTUs Inverter",
            "Marca Monitorada": "Midea",
        })
        assert row["produto"]
        assert row["produto_normalizado"]


class TestLoadCsvEntregaNone:
    """`df.where(pd.notna(df), None)` não converte de forma confiável em
    coluna object do pandas 2.x — a célula vazia continuava saindo como NaN."""

    def test_celula_vazia_vira_none(self, csv_com_celula_vazia):
        registros = _load_csv(csv_com_celula_vazia)
        vazio = registros[1]["Produto / SKU"]
        assert vazio is None, f"esperava None, veio {vazio!r} ({type(vazio).__name__})"

    def test_nenhum_nan_sobra(self, csv_com_celula_vazia):
        for registro in _load_csv(csv_com_celula_vazia):
            for chave, valor in registro.items():
                assert valor is None or valor == valor, f"NaN em {chave!r}"

    def test_import_completo_nao_quebra(self, csv_com_celula_vazia):
        """O caso real: o import percorria os arquivos e morria no meio."""
        registros = _load_csv(csv_com_celula_vazia)
        linhas = [map_record(r) for r in registros]
        assert len(linhas) == 2
        assert linhas[0]["produto"]
        assert linhas[1]["produto"] is None


class TestHistoricoImportaCsvComCelulaVazia:
    """Ponta a ponta: o `import-csv` do histórico grava a partição mesmo com
    linha defeituosa, em vez de abortar o lote inteiro."""

    def test_grava_a_particao(self, csv_com_celula_vazia, tmp_path):
        pytest.importorskip("pyarrow")
        from utils.history import HistoryStore, LocalBackend, write_records

        store = HistoryStore(LocalBackend(tmp_path / "hist"))
        keys = write_records(_load_csv(csv_com_celula_vazia), run_id="r1", store=store)
        assert keys, "nenhuma partição gravada"
        # A linha sem produto é descartada pelo filtro AC (não dá para validar
        # que é ar-condicionado sem nome); a boa sobrevive.
        assert len(store.read("coletas")) >= 1
