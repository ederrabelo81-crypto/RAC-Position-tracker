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
        if url == cc._ARCHIVE:
            inicio = date.fromisoformat(params["start_date"])
            fim = date.fromisoformat(params["end_date"])
            return _resposta(inicio, (fim - inicio).days + 1, tmax=27.0)
        return _resposta(hoje - timedelta(days=params["past_days"]), params["past_days"] + 16)

    monkeypatch.setattr(cc, "_get", _get)
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    csv = tmp_path / "clima.csv"

    assert cc.main(["--sem-supabase", "--csv", str(csv)]) == 0

    # previsão + archive de D-15..D-6 (o ERA5 chega com ~5 dias de atraso)
    assert sorted(u for u, _ in pedidos) == sorted([cc._FORECAST, cc._ARCHIVE] * len(CIDADES))
    df = cc.ler_csv(csv)
    sp = df[(df["cidade"] == "São Paulo") & (df["data"] == hoje - timedelta(days=8))]
    assert list(sp["fonte"]) == ["archive"]   # archive substitui a análise do modelo
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


class _Resp:
    def __init__(self, data):
        self.data = data


class _ClientArchive:
    """Banco falso: já tem o ERA5 de 27/10 em São Paulo; registra o upsert."""

    def __init__(self):
        self.upsert_lotes = []

    def table(self, nome):
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a):
        return self

    def gte(self, *a):
        return self

    def lte(self, *a):
        return self

    def limit(self, *a):
        return self

    def execute(self):
        return _Resp([{"data": "2026-10-27", "cidade": "São Paulo"}])

    def upsert(self, lote, on_conflict=None):
        self.upsert_lotes.append(lote)
        return self


def test_rodada_diaria_nao_sobrescreve_o_archive(monkeypatch):
    client = _ClientArchive()
    monkeypatch.setattr(cc, "_client", lambda: client)
    novo = pd.DataFrame([
        {"data": date(2026, 10, 27), "cidade": "São Paulo", "uf": "SP", "regiao": "Sudeste",
         "tipo": OBSERVADO, "tmax": 20.0, "tmin": 15.0, "tmed": 17.5, "sensacao_max": 20.0,
         "precip_mm": 0.0, "fonte": "forecast"},
        {"data": date(2026, 10, 28), "cidade": "São Paulo", "uf": "SP", "regiao": "Sudeste",
         "tipo": OBSERVADO, "tmax": 22.0, "tmin": 15.0, "tmed": 18.5, "sensacao_max": 22.0,
         "precip_mm": 0.0, "fonte": "forecast"},
    ])
    assert cc.gravar_supabase(novo) == 1
    gravadas = [r["data"] for lote in client.upsert_lotes for r in lote]
    assert gravadas == ["2026-10-28"]
