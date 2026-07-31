"""
tests/test_recover_from_artifacts.py — Seleção dos artifacts a resgatar.

O resgate é a última linha de defesa: quando ele roda, o dado já não está no
banco nem no histórico, e o prazo dos artifacts está correndo. Errar a
seleção aqui significa recuperar o dia errado — ou pior, achar que recuperou.

A suíte não fala com a rede: exercita o filtro sobre respostas da API
montadas à mão.
"""

from __future__ import annotations

import sys
import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.recover_from_artifacts import (  # noqa: E402
    RecoveryError,
    _artifact_day,
    _baixar_csvs,
    _selecionar,
    _token,
)


def _art(nome: str, criado: str, expirado: bool = False) -> dict:
    """Artifact no formato que a API do GitHub devolve."""
    return {
        "name": nome,
        "created_at": f"{criado}T15:23:21Z",
        "expires_at": "2026-08-30T15:23:21Z",
        "expired": expirado,
        "size_in_bytes": 194011,
        "archive_download_url": f"https://api.github.com/artifacts/{nome}/zip",
    }


def test_ignora_artifacts_de_log():
    """`rac-logs-*` não tem CSV — entrar nele só gastaria download."""
    artifacts = [
        _art("rac-coleta-Abertura-30642667753", "2026-07-31"),
        _art("rac-logs-Abertura-30642667753", "2026-07-31"),
    ]

    escolhidos = _selecionar(artifacts, None, None)

    assert [a["name"] for a in escolhidos] == ["rac-coleta-Abertura-30642667753"]


def test_intervalo_e_inclusivo_nas_duas_pontas():
    artifacts = [
        _art("rac-coleta-A-1", "2026-07-15"),
        _art("rac-coleta-A-2", "2026-07-16"),
        _art("rac-coleta-A-3", "2026-07-31"),
        _art("rac-coleta-A-4", "2026-08-01"),
    ]

    escolhidos = _selecionar(artifacts, date(2026, 7, 16), date(2026, 7, 31))

    assert [a["name"] for a in escolhidos] == ["rac-coleta-A-2", "rac-coleta-A-3"]


def test_artifact_expirado_fica_de_fora():
    """Expirado não é recuperável — melhor dizer isso que tentar baixar."""
    artifacts = [
        _art("rac-coleta-velho", "2026-06-01", expirado=True),
        _art("rac-coleta-novo", "2026-07-31"),
    ]

    escolhidos = _selecionar(artifacts, None, None)

    assert [a["name"] for a in escolhidos] == ["rac-coleta-novo"]


def test_ordena_do_mais_antigo_para_o_mais_novo():
    """A ordem cronológica deixa o log do resgate legível dia a dia."""
    artifacts = [
        _art("rac-coleta-C", "2026-07-31"),
        _art("rac-coleta-A", "2026-07-16"),
        _art("rac-coleta-B", "2026-07-20"),
    ]

    escolhidos = _selecionar(artifacts, None, None)

    assert [a["name"] for a in escolhidos] == [
        "rac-coleta-A", "rac-coleta-B", "rac-coleta-C",
    ]


def test_created_at_invalido_nao_derruba_a_selecao():
    artifacts = [_art("rac-coleta-torto", "sem-data"), _art("rac-coleta-ok", "2026-07-31")]

    assert _artifact_day(artifacts[0]) is None
    assert [a["name"] for a in _selecionar(artifacts, None, None)] == ["rac-coleta-ok"]


def test_token_ausente_falha_com_instrucao(monkeypatch):
    """Sem token o script não deve começar a baixar para falhar no meio."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    with pytest.raises(RecoveryError, match="GITHUB_TOKEN"):
        _token()


def test_extrai_so_os_csvs_e_achata_o_caminho(tmp_path, monkeypatch):
    """O zip traz `output/*.csv` e `data/history/**/*.parquet` — só o CSV serve.

    O caminho de dentro do zip é descartado: o nome do CSV já carrega data e
    hora, e é dele que sai o run_id determinístico da partição.
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("output/rac_monitoramento_20260731_1402.csv", "Data;Plataforma\n")
        zf.writestr("data/history/coletas/data=2026-07-31__run-abc.parquet", "PAR1")

    class _Resp:
        status_code = 200
        content = buf.getvalue()

    monkeypatch.setattr(
        "scripts.recover_from_artifacts.requests.get",
        lambda *a, **k: _Resp(),
    )

    extraidos = _baixar_csvs(_art("rac-coleta-X", "2026-07-31"), "tok", tmp_path)

    assert [p.name for p in extraidos] == ["rac_monitoramento_20260731_1402.csv"]
    assert (tmp_path / "rac_monitoramento_20260731_1402.csv").exists()
