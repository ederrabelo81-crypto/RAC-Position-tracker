"""Testes do script de descoberta do PriceTrack (sem rede, sem banco)."""
from __future__ import annotations

import gzip
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pricetrack_discovery as pd_  # noqa: E402


def _linhas() -> list:
    base = {
        "category": "AR CONDICIONADO", "subcategory": "SPLIT HI-WALL",
        "family": "AI ECOMASTER", "brand": "MIDEA", "status": "AVAILABLE",
        "collection_hour": "10", "screenshot_url": "https://x/p.png",
    }
    return [
        # mesma oferta (sku|seller) coletada 2x no dia → conta 1 oferta
        {**base, "marketplace": "CASAS BAHIA", "sku": "A1", "seller": "LOJA X",
         "title": "Ar Split Midea Inverter 12.000 BTUs Só Frio 220V"},
        {**base, "marketplace": "CASAS BAHIA", "sku": "A1", "seller": "LOJA X",
         "title": None, "product_name": "Split 9000 BTU quente e frio"},
        {**base, "marketplace": "CASAS BAHIA", "sku": "A1", "seller": "LOJA Y",
         "status": "UNAVAILABLE", "screenshot_url": None, "title": "x"},
        {**base, "marketplace": "AMAZON", "sku": "B2", "seller": "", "title": "y"},
        # fora do alvo
        {"category": "LAVADORAS", "marketplace": "CASAS BAHIA", "sku": "L1", "seller": "Z"},
    ]


def test_analisar_conta_ofertas_distintas_e_caracteristicas():
    r = pd_.analisar(iter(_linhas()), ["ar condicionado "])

    assert r["linhas_total"] == 5
    assert r["linhas_alvo"] == 4
    ofertas = {o["marketplace"]: o for o in r["alvo"]["ofertas_por_marketplace"]}
    assert ofertas["CASAS BAHIA"]["ofertas"] == 2
    assert ofertas["CASAS BAHIA"]["ofertas_available"] == 1
    assert ofertas["AMAZON"]["ofertas"] == 1
    assert r["alvo"]["sem_seller"] == 1
    assert r["alvo"]["com_print_pct"] == 75.0
    assert r["alvo"]["title_nulo_pct"] == 25.0
    assert dict(r["alvo"]["btu_no_texto"]) == {"12000": 1, "9000": 1}
    assert r["alvo"]["caracteristica_no_texto_pct"]["inverter"] == 25.0
    assert r["alvo"]["caracteristica_no_texto_pct"]["quente_frio"] == 25.0
    assert ("LAVADORAS", 1) in r["categorias"]
    assert len(r["exemplos"]) == 3


def test_camelcase_e_colunas_de_caracteristica():
    linha = {"productCategory": "AR CONDICIONADO", "marketplace": "M",
             "sku": "S", "seller": "V", "capacidadeBtu": 12000}
    r = pd_.analisar(iter([linha]), ["AR CONDICIONADO"])
    assert r["linhas_alvo"] == 1
    assert r["colunas_candidatas_caracteristica"] == ["capacidadeBtu"]


def test_iter_ndjson_gz_streaming_ignora_linha_invalida(tmp_path):
    arq = tmp_path / "x.ndjson.gz"
    with gzip.open(arq, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"a": 1}) + "\n\nnão-json\n" + json.dumps({"a": 2}) + "\n")
    assert [r["a"] for r in pd_.iter_ndjson_gz(arq)] == [1, 2]


def test_main_com_arquivo_gera_relatorio_sem_rede(tmp_path, monkeypatch):
    arq = tmp_path / "offers.ndjson.gz"
    with gzip.open(arq, "wt", encoding="utf-8") as fh:
        for linha in _linhas():
            fh.write(json.dumps(linha) + "\n")
    monkeypatch.setattr(pd_, "_LOG_DIR", tmp_path)

    assert pd_.main(["--arquivo", str(arq), "--data", "2026-11-03"]) == 0

    md = (tmp_path / "pricetrack_discovery_2026-11-03_arquivo.md").read_text(encoding="utf-8")
    assert "| CASAS BAHIA | 2 | 1 |" in md
    assert (tmp_path / "pricetrack_discovery_2026-11-03_arquivo.json").exists()


def test_versao_auto_respeita_janela_do_redis():
    hoje = date(2026, 11, 4)
    assert pd_._versao_efetiva("auto", date(2026, 11, 3), hoje) == "v2"
    assert pd_._versao_efetiva("auto", date(2026, 7, 1), hoje) == "v1"
    assert pd_._versao_efetiva("v1", date(2026, 11, 3), hoje) == "v1"


class _Job:
    def __init__(self, status):
        self.status = status
        self.export_id = "abc"
        self.progress = None

    @property
    def is_terminal(self):
        return self.status in ("DONE", "FAILED")


def test_esperar_terminar_faz_polling_ate_done():
    sequencia = iter([_Job("PROCESSING"), _Job("DONE")])

    class _Cliente:
        chamadas = 0

        def get_export(self, _id):
            _Cliente.chamadas += 1
            return next(sequencia)

    job = pd_._esperar_terminar(_Cliente(), _Job("PENDING"), 5, dormir=lambda s: None)
    assert job.status == "DONE"
    assert _Cliente.chamadas == 2


def test_esperar_terminar_estoura_prazo_com_id_para_retomar():
    class _Cliente:
        def get_export(self, _id):
            return _Job("PROCESSING")

    import pytest
    with pytest.raises(TimeoutError, match="--export-id abc"):
        pd_._esperar_terminar(_Cliente(), _Job("PENDING"), 0, dormir=lambda s: None)
