"""Models fiéis aos schemas Offer/Shipping — camelCase, snake_case, nullables."""
from datetime import date

import pytest

from pricetrack_api.models import (
    CollectQuery,
    ExportJob,
    ExportRequest,
    Offer,
    PageMeta,
    Shipping,
    record_id,
    to_hour,
)
from .conftest import offer_payload


class TestOfferFromApi:
    def test_camelcase_completo(self):
        offer = Offer.from_api(offer_payload())
        assert offer.id == "of-1"
        assert offer.sku == "42MACA09S5"
        assert offer.brand == "MIDEA"
        assert offer.spot_price == 1994.91
        assert offer.forward_price == 2099.90
        assert offer.price_from == 2599.00
        assert offer.installment_number == 10
        assert offer.installment_value == 209.99
        assert offer.collection_date == date(2026, 7, 1)
        assert offer.collection_hour == 9
        assert offer.is_available is True

    def test_campos_nullable_do_schema(self):
        offer = Offer.from_api(offer_payload(
            color=None, pixPrice=None, screenshotUrl=None,
        ))
        assert offer.color is None
        assert offer.pix_price is None
        assert offer.screenshot_url is None

    def test_nullable_preenchidos(self):
        offer = Offer.from_api(offer_payload(
            color="Branco", pixPrice=1899.90, screenshotUrl="https://s3/shot.png",
        ))
        assert offer.color == "Branco"
        assert offer.pix_price == 1899.90
        assert offer.screenshot_url == "https://s3/shot.png"

    def test_snake_case_do_export_ndjson(self):
        raw = {
            "id": "of-9",
            "sku": "SKU9",
            "product_name": "Produto",
            "brand": "GREE",
            "category": "AR CONDICIONADO",
            "marketplace": "AMAZON",
            "seller": "AMAZON BR",
            "spot_price": 1500.5,
            "pix_price": 1450.0,
            "price_from": 1999.0,
            "status": "unavailable",
            "collection_date": "2026-07-01",
            "collection_hour": 18,
        }
        offer = Offer.from_api(raw)
        assert offer.product_name == "Produto"
        assert offer.spot_price == 1500.5
        assert offer.pix_price == 1450.0
        assert offer.price_from == 1999.0
        assert offer.status == "UNAVAILABLE"
        assert offer.is_available is False
        assert offer.collection_hour == 18

    def test_precos_invalidos_viram_none(self):
        offer = Offer.from_api(offer_payload(
            spotPrice="abc", forwardPrice="", pixPrice=None,
        ))
        assert offer.spot_price is None
        assert offer.forward_price is None
        assert offer.pix_price is None


class TestShippingFromApi:
    def test_schema_shipping(self):
        raw = {
            "id": "sh-1", "sku": "SKU1", "title": "t", "productName": "P",
            "brand": "MIDEA", "category": "AR", "subcategory": "S",
            "family": "F", "marketplace": "MAGALU", "seller": "MAGALU",
            "cep": "01310-100", "shippingCost": 49.9, "deadline": 7,
            "transporterType": "CORREIOS", "status": "AVAILABLE",
            "collectionDate": "2026-07-01", "collectionHour": "10",
            "url": "https://x",
        }
        ship = Shipping.from_api(raw)
        assert ship.cep == "01310-100"
        assert ship.shipping_cost == 49.9
        assert ship.deadline == 7
        assert ship.transporter_type == "CORREIOS"
        assert ship.is_available


class TestPageMeta:
    def test_parse(self):
        meta = PageMeta.from_api({
            "page": 2, "take": 10, "pageCount": 5,
            "hasNextPage": True, "hasPreviousPage": True,
        })
        assert meta.page == 2
        assert meta.page_count == 5
        assert meta.has_next_page is True

    def test_has_next_false_nao_vira_true(self):
        meta = PageMeta.from_api({"page": 5, "take": 10, "pageCount": 5,
                                  "hasNextPage": False, "hasPreviousPage": True})
        assert meta.has_next_page is False


class TestCollectQuery:
    def test_collection_date_obrigatorio(self):
        with pytest.raises(ValueError, match="collectionDate obrigatório"):
            CollectQuery(collection_date="not-a-date")

    def test_params_arrays_e_escalares(self):
        query = CollectQuery(
            collection_date="2026-07-01",
            marketplace=["MERCADO LIVRE", "AMAZON"],
            product_brand=["MIDEA"],
            status="AVAILABLE",
            spot_price_min=1000.0,
            order="desc",
            page=3,
            take=50,
        )
        params = query.to_params()
        assert params["collectionDate"] == "2026-07-01"
        assert params["marketplace"] == ["MERCADO LIVRE", "AMAZON"]
        assert params["productBrand"] == ["MIDEA"]
        assert params["status"] == "AVAILABLE"
        assert params["spotPriceMin"] == 1000.0
        assert params["order"] == "DESC"
        assert params["page"] == 3 and params["take"] == 50
        assert "seller" not in params            # filtros vazios não viajam

    def test_hour_range_validado(self):
        with pytest.raises(ValueError, match="HH:MM-HH:MM"):
            CollectQuery(collection_date="2026-07-01",
                         collection_hour_range="8h-12h")
        query = CollectQuery(collection_date="2026-07-01",
                             collection_hour_range="08:00-12:00")
        assert query.to_params()["collectionHourRange"] == "08:00-12:00"

    def test_order_validado(self):
        with pytest.raises(ValueError, match="ASC ou DESC"):
            CollectQuery(collection_date="2026-07-01", order="sideways")


class TestExportModels:
    def test_export_request_body(self):
        req = ExportRequest(
            collection_date=date(2026, 7, 1),
            marketplaces=["SHOPEE"],
            collection_hour_execution_range="08:00-12:00",
        )
        assert req.to_body() == {
            "collectionDate": "2026-07-01",
            "marketplaces": ["SHOPEE"],
            "collectionHourExecutionRange": "08:00-12:00",
        }

    def test_export_request_minimo(self):
        assert ExportRequest("2026-07-01").to_body() == {
            "collectionDate": "2026-07-01"
        }

    def test_export_job_normaliza_status(self):
        job = ExportJob.from_api(
            {"exportId": "exp-1", "status": "pending",
             "statusUrl": "/exports-external/exp-1"},
            fetched_at=10.0,
        )
        assert job.status == "PENDING"
        assert job.is_active and not job.is_terminal

    def test_export_job_done_com_download(self):
        job = ExportJob.from_api({
            "exportId": "exp-1", "status": "DONE",
            "downloadUrl": "https://s3/presigned", "format": "ndjson.gz",
            "rowCount": 1200000, "fileSizeBytes": 98765432, "progress": 100,
        }, fetched_at=50.0)
        assert job.is_terminal
        assert job.row_count == 1_200_000
        assert job.download_url_stale(ttl_seconds=3000, now=3051.0) is True
        assert job.download_url_stale(ttl_seconds=3000, now=3049.0) is False


class TestRecordId:
    def test_usa_id_quando_presente(self):
        assert record_id({"id": "abc"}) == "abc"

    def test_sintetico_deterministico_sem_id(self):
        raw = {"sku": "X", "spotPrice": 10}
        assert record_id(raw) == record_id(dict(raw))
        assert record_id(raw).startswith("synthetic-")
        assert record_id(raw) != record_id({"sku": "Y", "spotPrice": 10})


class TestToHour:
    @pytest.mark.parametrize("value,expected", [
        ("09", 9), (8, 8), ("18:00", 18), ("23", 23), ("24", None),
        (None, None), ("", None), ("xx", None),
    ])
    def test_formatos(self, value, expected):
        assert to_hour(value) == expected
