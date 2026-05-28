"""Testes específicos da deduplicação intra-batch antes do upsert."""
from pricetrack_importer.__main__ import _dedupe_by_conflict_key
from pricetrack_importer.logger import create_execution_log


def _r(**kw):
    base = {
        "collection_date": "2026-05-27",
        "brand": "MIDEA",
        "sku": "38EZVQA12M5",
        "title": "Ar Condicionado",
        "marketplace": "MERCADO LIVRE",
        "seller": "FRIOPEÇAS",
        "seller_canonical": "FRIOPEÇAS",
        "min_price": 100.0,
        "avg_price": 110.0,
        "mode_price": 110.0,
        "max_price": 120.0,
        "source_file": "test.md",
    }
    base.update(kw)
    return base


class TestDedup:
    def test_sem_duplicatas_nao_colapsa(self):
        log = create_execution_log("test.md")
        rows = [
            _r(seller="A"),
            _r(seller="B"),
            _r(seller="C"),
        ]
        out = _dedupe_by_conflict_key(rows, log)
        assert len(out) == 3
        assert log.rows.duplicates_collapsed == 0

    def test_duplicata_pela_chave_completa_e_colapsada(self):
        log = create_execution_log("test.md")
        rows = [
            _r(min_price=100.0),
            _r(min_price=200.0),  # mesma chave, último ganha
        ]
        out = _dedupe_by_conflict_key(rows, log)
        assert len(out) == 1
        assert out[0]["min_price"] == 200.0
        assert log.rows.duplicates_collapsed == 1

    def test_chave_diferindo_em_seller_nao_colapsa(self):
        log = create_execution_log("test.md")
        rows = [
            _r(seller="FRIOPEÇAS"),
            _r(seller="FRIOPECAS"),  # raw diferente, ainda que canonical igual
        ]
        out = _dedupe_by_conflict_key(rows, log)
        # A constraint UNIQUE é sobre seller (raw), não seller_canonical
        assert len(out) == 2
        assert log.rows.duplicates_collapsed == 0

    def test_multiplas_duplicatas_da_mesma_chave(self):
        log = create_execution_log("test.md")
        rows = [
            _r(max_price=100.0),
            _r(max_price=200.0),
            _r(max_price=300.0),
            _r(max_price=400.0),
        ]
        out = _dedupe_by_conflict_key(rows, log)
        assert len(out) == 1
        assert out[0]["max_price"] == 400.0
        assert log.rows.duplicates_collapsed == 3
