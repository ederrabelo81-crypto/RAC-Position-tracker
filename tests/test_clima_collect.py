"""scripts/clima_collect.py com o Open-Meteo simulado (sem rede)."""

from datetime import date, timedelta

import pandas as pd

import scripts.clima_collect as cc
from utils.clima import CIDADES, OBSERVADO, PREVISAO


def _resposta(inicio: date, dias: int, tmax: float = 28.0) -> dict:
    datas = [(inicio + timedelta(days=i)).isoformat() for i in range(dias)]
    return {"daily": {
        "time": datas,
        "temperature_2m_max": [tmax] * dias,
        "temperature_2m_min": [tmax - 9] * dias,
        "apparent_temperature_max": [tmax + 1] * dias,
        "precipitation_sum": [0.0] * dias,
    }}


def test_diario_grava_csv_com_observado_e_previsao(tmp_path, monkeypatch, capsys):
    hoje = cc.now_brt().date()
    pedidos = []

    def _get(url, params, tentativas=3):
        pedidos.append((url, params))
        return _resposta(hoje - timedelta(days=params["past_days"]), params["past_days"] + 16)

    monkeypatch.setattr(cc, "_get", _get)
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    csv = tmp_path / "clima.csv"

    assert cc.main(["--sem-supabase", "--csv", str(csv)]) == 0

    assert len(pedidos) == len(CIDADES)
    assert all(u == cc._FORECAST for u, _ in pedidos)
    df = cc.ler_csv(csv)
    assert set(df["tipo"]) == {OBSERVADO, PREVISAO}
    assert df["cidade"].nunique() == len(CIDADES)
    saida = capsys.readouterr().out
    assert "Radar de clima" in saida and "Sudeste" in saida
    assert "--backfill" in saida  # sem histórico, pede o backfill


def test_backfill_usa_archive_e_mescla(tmp_path, monkeypatch):
    def _get(url, params, tentativas=3):
        assert url == cc._ARCHIVE
        inicio = date.fromisoformat(params["start_date"])
        fim = date.fromisoformat(params["end_date"])
        return _resposta(inicio, (fim - inicio).days + 1)

    monkeypatch.setattr(cc, "_get", _get)
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    csv = tmp_path / "clima.csv"
    args = ["--backfill", "--desde", "2026-01-01", "--ate", "2026-01-10",
            "--sem-supabase", "--csv", str(csv)]
    assert cc.main(args) == 0
    assert cc.main(args) == 0  # idempotente: regravar não duplica
    df = cc.ler_csv(csv)
    assert len(df) == 10 * len(CIDADES)
    assert set(df["fonte"]) == {"archive"}


def test_nenhuma_cidade_responde(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "_get", lambda url, params, tentativas=3: None)
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    assert cc.main(["--sem-supabase", "--csv", str(tmp_path / "x.csv")]) == 1
