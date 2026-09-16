"""
tests/test_pricetrack_csv_import.py — import do export MANUAL (CSV) do painel
do PriceTrack, reaproveitando a agregação de scripts/pricetrack_api_import.py.

Cobre o caso que motivou o script: a fila de exports assíncronos da API ficou
travada por horas com os 3 slots da organização ocupados (ver
docs/PRICETRACK_INSIGHTS.md), e o CSV baixado à mão do painel virou a via de
importação — com cabeçalho em português e granularidade por oferta (Preço À
Vista / Preço Pix separados), diferente do NDJSON da API.

Rode: pytest tests/test_pricetrack_csv_import.py
"""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parent.parent

_SPEC_API = importlib.util.spec_from_file_location(
    "pricetrack_api_import", _ROOT / "scripts" / "pricetrack_api_import.py",
)
ptai = importlib.util.module_from_spec(_SPEC_API)
_SPEC_API.loader.exec_module(ptai)

_SPEC_CSV = importlib.util.spec_from_file_location(
    "pricetrack_csv_import", _ROOT / "scripts" / "pricetrack_csv_import.py",
)
ptci = importlib.util.module_from_spec(_SPEC_CSV)
_SPEC_CSV.loader.exec_module(ptci)


class TestExtractHour:
    def test_hora_execucao_valida(self):
        assert ptci._extract_hour("05:22") == 5
        assert ptci._extract_hour("11:05") == 11
        assert ptci._extract_hour("00:00") == 0

    def test_valor_ilegivel_vira_none(self):
        assert ptci._extract_hour("") is None
        assert ptci._extract_hour(None) is None
        assert ptci._extract_hour(float("nan")) is None
        assert ptci._extract_hour("indisponível") is None


class TestPrepare:
    def test_deriva_collection_hour_de_hora_execucao(self):
        df = pd.DataFrame({
            "Hora de Execução": ["05:22", "11:05", "20:59"],
            "Produto": ["a", "b", "c"],
        })
        out = ptci._prepare(df)
        assert list(out["collection_hour"]) == [5, 11, 20]
        # Não muta o DataFrame original (aggregate_offers já faz seu próprio
        # .copy(), mas _prepare precisa ser igualmente não-destrutivo).
        assert "collection_hour" not in df.columns

    def test_sem_coluna_hora_nao_quebra(self):
        df = pd.DataFrame({"Produto": ["a"]})
        out = ptci._prepare(df)
        assert "collection_hour" not in out.columns


def _oferta_manual(sku: str = "42MACA09S5", spot=1999.90, pix=None, **overrides) -> dict:
    """Oferta no formato de linha do export manual (cabeçalho em português)."""
    base = {
        "Categoria": "AR CONDICIONADO",
        "Marca": "MIDEA",
        "SKU": sku,
        "Produto": "Ar Condicionado Split Midea 9000 Btus Frio",
        "Marketplace": "MERCADO LIVRE",
        "Seller": "WEBCONTINENTAL",
        "Preço À Vista": spot,
        "Preço Pix": pix,
        "Preço A Prazo": None,
    }
    base.update(overrides)
    return base


class TestColunasPortuguesasNoAggregateOffers:
    """`aggregate_offers` é a mesma função usada pelo caminho da API — o CSV
    manual só precisa que suas colunas apareçam nos candidatos certos."""

    def test_reconhece_preco_a_vista_e_pix_acentuados(self):
        df = pd.DataFrame([_oferta_manual(spot=2000.00, pix=1800.00)])
        agg, rejections = ptai.aggregate_offers(df, "2026-09-16")
        assert not rejections
        assert len(agg) == 1
        row = agg.iloc[0]
        # best_cash = menor entre à vista e PIX — não pode cair no spot puro.
        assert row["min_price"] == 1800.00
        assert row["price_basis"] == ptai.PRICE_BASIS_BEST_CASH

    def test_sem_pix_usa_a_vista(self):
        df = pd.DataFrame([_oferta_manual(spot=2000.00, pix=None)])
        agg, _ = ptai.aggregate_offers(df, "2026-09-16")
        assert agg.iloc[0]["min_price"] == 2000.00

    def test_export_sem_status_trata_tudo_como_disponivel(self):
        # O export manual não traz coluna de status — aggregate_offers já
        # degrada para "tudo disponível" com warning; aqui só confirmamos
        # que a linha não é descartada por isso.
        df = pd.DataFrame([_oferta_manual()])
        agg, rejections = ptai.aggregate_offers(df, "2026-09-16")
        assert len(agg) == 1
        assert "NO_CASH_PRICE" not in rejections


class TestProcessCsvDryRun:
    """Smoke test do fluxo completo (leitura → agregação) sem tocar banco.

    `dry_run=True` já faz `date_exists`/`insert_rows` retornarem antes de
    consultar `_banco_disponivel()` (ver seus próprios guards em
    `pricetrack_api_import.py`), então nenhum monkeypatch de backend é
    necessário aqui — só valida que o fluxo não levanta exceção.
    """

    def test_roda_sem_erro_em_dry_run(self, tmp_path):
        csv_path = tmp_path / "collects.csv"
        pd.DataFrame([
            {**_oferta_manual(sku="SKU1"), "Data de Coleta": "2026-09-16",
             "Hora de Execução": "05:22"},
            {**_oferta_manual(sku="SKU2", pix=1500.0), "Data de Coleta": "2026-09-16",
             "Hora de Execução": "11:05"},
        ]).to_csv(csv_path, index=False)

        ptci.process_csv(csv_path, dry_run=True)  # não deve levantar exceção

    def test_arquivo_sem_coluna_data_de_coleta_nao_quebra(self, tmp_path):
        csv_path = tmp_path / "invalido.csv"
        pd.DataFrame([{"Produto": "x"}]).to_csv(csv_path, index=False)
        ptci.process_csv(csv_path, dry_run=True)  # loga erro, não levanta


def _stub_persistence(monkeypatch, insert_return=None, write_history_return=None):
    """Substitui as chamadas de persistência de `process_csv` — SEMPRE no
    módulo `ptci` (o que `process_csv` de fato usa), nunca em `ptai`: as duas
    variáveis apontam para instâncias de módulo DIFERENTES (`ptai` é carregado
    à parte aqui no teste; `ptci` importa a sua própria cópia de
    `pricetrack_api_import` via `from ... import ...`), então um patch em
    `ptai` não é visto por `process_csv`. Retorna um dict que acumula as
    chamadas para inspeção.
    """
    calls = {"insert_rows": [], "log_import": [], "purge_stale_basis": []}

    def _insert_rows(records, dry_run=False):
        calls["insert_rows"].append(records)
        return len(records) if insert_return is None else insert_return

    monkeypatch.setattr(ptci, "insert_rows", _insert_rows)
    monkeypatch.setattr(ptci, "write_history", lambda *a, **k: write_history_return or [])
    monkeypatch.setattr(ptci, "date_exists", lambda *a, **k: False)
    monkeypatch.setattr(
        ptci, "log_import",
        lambda **kwargs: calls["log_import"].append(kwargs),
    )
    monkeypatch.setattr(
        ptci, "purge_stale_basis",
        lambda *a, **k: calls["purge_stale_basis"].append((a, k)) or 0,
    )
    return calls


class TestSkuNumericoPreservaZeroAEsquerda:
    def test_sku_puramente_numerico_nao_vira_int(self, tmp_path, monkeypatch):
        calls = _stub_persistence(monkeypatch)
        csv_path = tmp_path / "collects.csv"
        pd.DataFrame([
            {**_oferta_manual(sku="001234"), "Data de Coleta": "2026-09-16",
             "Hora de Execução": "05:22"},
        ]).to_csv(csv_path, index=False)

        ptci.process_csv(csv_path, dry_run=False)

        assert calls["insert_rows"], "insert_rows não foi chamado"
        assert calls["insert_rows"][0][0]["sku"] == "001234"


class TestStatusRefleteInsercaoParcial:
    def _csv(self, tmp_path) -> Path:
        csv_path = tmp_path / "collects.csv"
        pd.DataFrame([
            {**_oferta_manual(sku="SKU1"), "Data de Coleta": "2026-09-16",
             "Hora de Execução": "05:22"},
        ]).to_csv(csv_path, index=False)
        return csv_path

    def test_insercao_completa_e_success(self, tmp_path, monkeypatch):
        calls = _stub_persistence(monkeypatch)  # insert_return=None -> len(records)
        ptci.process_csv(self._csv(tmp_path), dry_run=False)
        assert calls["log_import"][0]["status"] == "SUCCESS"

    def test_insercao_parcial_e_partial_nao_success(self, tmp_path, monkeypatch):
        calls = _stub_persistence(monkeypatch, insert_return=0)  # lote "falhou"
        ptci.process_csv(self._csv(tmp_path), dry_run=False)
        assert calls["log_import"][0]["status"] == "PARTIAL"
        assert not calls["purge_stale_basis"]  # não purga com escrita incompleta


class TestMainAbortaSemBackend:
    """Mesma regra de `pricetrack_api_import.py::main()`: pedir gravação sem
    nenhum backend configurado é ERRO, não um "concluído" silencioso com zero
    linha gravada."""

    def test_aborta_fora_de_dry_run_sem_backend(self, tmp_path, monkeypatch):
        csv_path = tmp_path / "collects.csv"
        pd.DataFrame([_oferta_manual()]).to_csv(csv_path, index=False)
        monkeypatch.setattr(ptci, "_banco_disponivel", lambda: False)
        monkeypatch.setattr(sys, "argv", ["pricetrack_csv_import.py", "--file", str(csv_path)])

        with pytest.raises(SystemExit):
            ptci.main()

    def test_dry_run_nao_exige_backend(self, tmp_path, monkeypatch):
        csv_path = tmp_path / "collects.csv"
        pd.DataFrame([_oferta_manual()]).to_csv(csv_path, index=False)
        monkeypatch.setattr(ptci, "_banco_disponivel", lambda: False)
        monkeypatch.setattr(
            sys, "argv",
            ["pricetrack_csv_import.py", "--file", str(csv_path), "--dry-run"],
        )

        ptci.main()  # não deve levantar
