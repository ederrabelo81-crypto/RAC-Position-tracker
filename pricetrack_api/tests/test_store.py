"""Partições por collectionDate: idempotência e dedup por id da oferta."""
import gzip
import json

import pytest

from pricetrack_api.store import NdjsonStore

from .conftest import offer_payload


@pytest.fixture
def store(tmp_path) -> NdjsonStore:
    return NdjsonStore(tmp_path / "partitions")


class TestUpsertDedup:
    def test_dedup_por_id_reimport_idempotente(self, store):
        offers = [offer_payload(oid="a"), offer_payload(oid="b")]

        first = store.upsert("offers", "2026-07-01", offers)
        assert (first.new, first.updated, first.total) == (2, 0, 2)

        # Reimportar o MESMO lote não duplica nada
        second = store.upsert("offers", "2026-07-01", offers)
        assert (second.new, second.updated, second.total) == (0, 2, 2)
        assert store.count("offers", "2026-07-01") == 2

    def test_ultimo_snapshot_vence(self, store):
        store.upsert("offers", "2026-07-01",
                     [offer_payload(oid="a", spotPrice=2000.0)])
        store.upsert("offers", "2026-07-01",
                     [offer_payload(oid="a", spotPrice=1899.0)])
        rows = list(store.read("offers", "2026-07-01"))
        assert len(rows) == 1
        assert rows[0]["spotPrice"] == 1899.0

    def test_multiplas_coletas_do_dia_coexistem(self, store):
        """Coleta da manhã + coleta da tarde: ids distintos, nada se perde."""
        manha = [offer_payload(oid=f"m-{i}", collectionHour="09")
                 for i in range(3)]
        tarde = [offer_payload(oid=f"t-{i}", collectionHour="19")
                 for i in range(2)]

        store.upsert("offers", "2026-07-01", manha, source="export:exp-1")
        stats = store.upsert("offers", "2026-07-01", tarde, source="export:exp-2")

        assert stats.total == 5
        manifest = store.manifest("offers", "2026-07-01")
        assert manifest["collection_hours"] == [9, 19]
        assert manifest["sources"] == ["export:exp-1", "export:exp-2"]
        assert manifest["row_count"] == 5

    def test_registro_sem_id_nao_e_perdido(self, store):
        sem_id = {k: v for k, v in offer_payload().items() if k != "id"}
        outro = {**sem_id, "sku": "OUTRO-SKU"}
        stats = store.upsert("offers", "2026-07-01", [sem_id, outro, dict(sem_id)])
        # dois registros distintos; a cópia idêntica deduplica
        assert stats.total == 2


class TestParticionamento:
    def test_uma_particao_por_data(self, store):
        store.upsert("offers", "2026-07-01", [offer_payload(oid="a")])
        store.upsert("offers", "2026-07-02", [offer_payload(oid="a")])

        p1 = store.data_path("offers", "2026-07-01")
        p2 = store.data_path("offers", "2026-07-02")
        assert p1 != p2
        assert "collection_date=2026-07-01" in str(p1)
        assert store.count("offers", "2026-07-01") == 1
        assert store.count("offers", "2026-07-02") == 1

    def test_datasets_separados(self, store):
        store.upsert("offers", "2026-07-01", [offer_payload(oid="a")])
        store.upsert("shipping", "2026-07-01", [{"id": "sh-1", "cep": "01310"}])
        assert store.count("offers", "2026-07-01") == 1
        assert store.count("shipping", "2026-07-01") == 1

    def test_arquivo_e_ndjson_gz_valido(self, store):
        store.upsert("offers", "2026-07-01",
                     [offer_payload(oid="a"), offer_payload(oid="b")])
        with gzip.open(store.data_path("offers", "2026-07-01"), "rt") as fh:
            rows = [json.loads(line) for line in fh]
        assert {r["id"] for r in rows} == {"a", "b"}

    def test_data_invalida_rejeitada(self, store):
        with pytest.raises(ValueError):
            store.upsert("offers", "31/12/2026", [offer_payload()])

    def test_leitura_de_particao_inexistente_e_vazia(self, store):
        assert list(store.read("offers", "2030-01-01")) == []
        assert store.count("offers", "2030-01-01") == 0
