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


def _art(
    nome: str,
    criado: str,
    expirado: bool = False,
    hora: str = "15:23:21",
    art_id: int | None = None,
) -> dict:
    """Artifact no formato que a API do GitHub devolve.

    ``hora`` é UTC, como a API entrega — os testes de fuso dependem disso.
    """
    art = {
        "name": nome,
        "created_at": f"{criado}T{hora}Z",
        "expires_at": "2026-08-30T15:23:21Z",
        "expired": expirado,
        "size_in_bytes": 194011,
        "archive_download_url": f"https://api.github.com/artifacts/{nome}/zip",
    }
    if art_id is not None:
        art["id"] = art_id
    return art


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

    extraidos = _baixar_csvs(
        _art("rac-coleta-X", "2026-07-31", art_id=77), "tok", tmp_path
    )

    assert [p.name for p in extraidos] == ["rac_monitoramento_20260731_1402.csv"]
    assert (
        tmp_path / "artifact-77" / "rac_monitoramento_20260731_1402.csv"
    ).exists()


def test_fechamento_da_meia_noite_utc_conta_como_o_dia_brt_anterior():
    """O caso que o `--end` perdia: Fechamento é `0 0 * * *` = 21:00 BRT.

    O artifact da coleta de 31/07 nasce com `created_at` em 01/08 UTC. Cortar
    pela data UTC descartaria em silêncio o último Fechamento do intervalo.
    """
    fechamento_31 = _art("rac-coleta-Fechamento", "2026-08-01", hora="00:04:11")

    assert _artifact_day(fechamento_31) == date(2026, 7, 31)

    escolhidos = _selecionar(
        [fechamento_31], date(2026, 7, 16), date(2026, 7, 31)
    )
    assert [a["name"] for a in escolhidos] == ["rac-coleta-Fechamento"]


def test_artifacts_com_csv_de_mesmo_nome_nao_se_sobrescrevem(tmp_path, monkeypatch):
    """Dois runs no mesmo minuto geram CSVs homônimos — nenhum pode sumir."""
    def _zip_com(conteudo: str) -> bytes:
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "output/rac_monitoramento_20260731_1402.csv", conteudo
            )
        return buf.getvalue()

    respostas = {
        "https://api.github.com/artifacts/rac-coleta-A/zip": _zip_com("A\n"),
        "https://api.github.com/artifacts/rac-coleta-B/zip": _zip_com("B\n"),
    }

    class _Resp:
        def __init__(self, url):
            self.status_code = 200
            self.content = respostas[url]

    monkeypatch.setattr(
        "scripts.recover_from_artifacts.requests.get",
        lambda url, *a, **k: _Resp(url),
    )

    a = _baixar_csvs(_art("rac-coleta-A", "2026-07-31", art_id=1), "tok", tmp_path)
    b = _baixar_csvs(_art("rac-coleta-B", "2026-07-31", art_id=2), "tok", tmp_path)

    assert a[0] != b[0]
    assert a[0].read_text() == "A\n"
    assert b[0].read_text() == "B\n"
    # O nome do CSV é preservado: é dele que sai o run_id da partição.
    assert a[0].name == b[0].name == "rac_monitoramento_20260731_1402.csv"


def test_csv_ilegivel_nao_aborta_o_resgate_dos_demais(tmp_path, monkeypatch):
    """Um CSV torto conta como falha e o resgate segue nos dias seguintes."""
    import scripts.upload_csv as upload_csv
    from scripts import recover_from_artifacts as rfa

    bom = tmp_path / "rac_monitoramento_20260731_1402.csv"
    ruim = tmp_path / "rac_monitoramento_20260730_1000.csv"
    bom.write_text("ok\n")
    ruim.write_text("torto\n")

    def _fake_load(path):
        if path.name.startswith("rac_monitoramento_20260730"):
            raise ValueError("coluna Data ausente")
        return [{"Data": "2026-07-31"}]

    monkeypatch.setattr(upload_csv, "_load_csv", _fake_load)
    monkeypatch.setattr(rfa, "get_store", lambda: object())
    monkeypatch.setattr(
        rfa, "write_records", lambda registros, run_id, store: ["chave"]
    )

    totais = rfa._carregar([bom, ruim], dry_run=False, para_supabase=False)

    assert totais["falhas"] == 1
    assert totais["particoes"] == 1  # o CSV bom foi gravado mesmo assim
    assert totais["linhas"] == 1


def test_duplicata_grava_na_ordem_cronologica_nao_alfabetica(tmp_path, monkeypatch):
    """CSVs homônimos caem na mesma partição — tem de vencer o run mais novo.

    Ordenar pelo caminho inteiro punha `artifact-10` antes de `artifact-9`, e o
    artifact ANTIGO gravava por último, ficando no histórico.
    """
    import scripts.upload_csv as upload_csv
    from scripts import recover_from_artifacts as rfa

    nome = "rac_monitoramento_20260731_1402.csv"
    velho = tmp_path / "artifact-9" / nome     # run mais antigo, id menor
    novo = tmp_path / "artifact-10" / nome     # run mais recente, id maior
    for caminho, marca in ((velho, "velho"), (novo, "novo")):
        caminho.parent.mkdir(parents=True)
        caminho.write_text(marca)

    gravados: list[str] = []
    monkeypatch.setattr(
        upload_csv, "_load_csv", lambda p: [{"marca": p.read_text()}]
    )
    monkeypatch.setattr(rfa, "get_store", lambda: object())
    monkeypatch.setattr(
        rfa, "write_records",
        lambda registros, run_id, store: gravados.append(
            registros[0]["marca"]
        ) or ["chave"],
    )

    # `main` monta a lista por `created_at` crescente: o mais novo vem por último.
    rfa._carregar([velho, novo], dry_run=False, para_supabase=False)

    assert gravados[-1] == "novo", (
        f"o último a gravar deveria ser o artifact mais recente, foi {gravados}"
    )
