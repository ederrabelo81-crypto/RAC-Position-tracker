"""
Teste de integração: parser → validator → normalizer (sem DB).

Cobre dois critérios de aceite que dependem da pipeline inteira:

- Seller corrompido (`38EZVQA12M5 - 220V`, `(ZQK215BB)`, `530290740`) NÃO entra
  na lista que iria pro DB.
- Sellers normalizados (FRIOPECAS / FRIOPEÇAS / LOJA OFICIAL FRIOPEÇAS) viram
  o mesmo `seller_canonical`.
"""
from pathlib import Path

from pricetrack_importer.__main__ import _process_rows
from pricetrack_importer.logger import create_execution_log
from pricetrack_importer.parser import parse_file


FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_MD = FIXTURES / "sample.md"


def _run_pipeline():
    exec_log = create_execution_log(str(SAMPLE_MD))
    raw = list(parse_file(SAMPLE_MD))
    normalized = _process_rows(raw, str(SAMPLE_MD), exec_log)
    return normalized, exec_log


class TestPipeline:
    def test_seller_corrompido_nao_vai_pro_db(self):
        normalized, exec_log = _run_pipeline()
        sellers = [r["seller"] for r in normalized]
        # Nenhum dos padrões corrompidos deve aparecer
        assert not any(" - 220V" in s for s in sellers)
        assert not any(s.startswith("(") and s.endswith(")") for s in sellers)
        assert not any(s.isdigit() for s in sellers)
        # E deve ter sido contado como invalid_seller no log
        assert exec_log.rows.invalid_seller >= 3

    def test_friopecas_normalizado_para_canonical_unico(self):
        normalized, _ = _run_pipeline()
        friopecas_rows = [r for r in normalized if "FRIOP" in r["seller_canonical"]]
        # Temos 3 grafias diferentes no fixture: FRIOPEÇAS, FRIOPECAS, LOJA OFICIAL FRIOPEÇAS
        assert len(friopecas_rows) >= 3
        canonicals = {r["seller_canonical"] for r in friopecas_rows}
        assert canonicals == {"FRIOPEÇAS"}

    def test_data_sempre_iso(self):
        normalized, _ = _run_pipeline()
        for r in normalized:
            assert r["collection_date"] == "2026-05-27"

    def test_decimais_sao_float(self):
        normalized, _ = _run_pipeline()
        for r in normalized:
            assert isinstance(r["min_price"], float)
            assert isinstance(r["max_price"], float)

    def test_contadores_consistentes(self):
        normalized, exec_log = _run_pipeline()
        # total_parsed = válidas (pré-dedup) + rejeitadas + metadata
        # válidas = duplicates_collapsed + len(normalized) (pós-dedup)
        soma = (
            exec_log.rows.valid
            + exec_log.rows.invalid_seller
            + exec_log.rows.invalid_other
            + exec_log.rows.metadata_skipped
        )
        assert soma == exec_log.rows.total_parsed
        assert (
            exec_log.rows.valid
            == len(normalized) + exec_log.rows.duplicates_collapsed
        )
