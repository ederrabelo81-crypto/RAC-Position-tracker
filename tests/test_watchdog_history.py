"""
tests/test_watchdog_history.py — O watchdog enxerga o histórico frio?

Existe por causa de um incidente real: o histórico no Drive foi montado em
26/07/2026 como redundância do Supabase e passou cinco dias sem gravar um
único arquivo — a coleta morria antes. Ninguém percebeu porque o watchdog só
sabia perguntar ao banco, e um backup que ninguém verifica não é backup.

O que estes testes travam:

* o check reprova quando o dia não está no frio (o caso do incidente);
* reprova quando o Drive está configurado mas o store caiu para o disco —
  esse é o modo de falha *silencioso*, em que tudo parece verde;
* aprova relendo o Parquet, não só listando o arquivo: partição corrompida
  precisa aparecer agora, não no dia do resgate.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

pytest.importorskip("pyarrow", reason="histórico em Parquet exige pyarrow")

from scripts.daily_status_check import _check_history  # noqa: E402
from utils.history import HistoryStore, LocalBackend, write_records  # noqa: E402

DIA = "2026-07-31"


def _linhas(qtd: int) -> list:
    """Linhas já no schema do banco (o que a coleta manda ao histórico)."""
    return [
        {
            "data": DIA,
            "turno": "Abertura",
            "plataforma": "Amazon",
            "marca": "Midea",
            "produto": f"Ar Condicionado Midea 12000 #{i}",
            "posicao_geral": i + 1,
            "preco": 1994.91,
        }
        for i in range(qtd)
    ]


@pytest.fixture
def frio(tmp_path, monkeypatch):
    """Aponta o histórico para um diretório temporário, em modo local."""
    monkeypatch.setenv("RAC_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("RAC_HISTORY_BACKEND", "local")
    monkeypatch.delenv("GDRIVE_FOLDER_ID", raising=False)
    return HistoryStore(LocalBackend(tmp_path / "history"))


def test_dia_ausente_reprova(frio):
    """O caso do incidente: pasta montada, nenhuma partição dentro."""
    resultado = _check_history(DIA)

    assert resultado["status"] == "FAIL"
    assert resultado["partitions"] == 0
    assert "nenhuma partição" in resultado["detail"]


def test_dia_presente_e_legivel_passa_com_ressalva_de_disco(frio):
    """Gravado e relido: aprova — mas disco local não é redundância."""
    write_records(_linhas(500), run_id="teste123", store=frio, already_mapped=True)

    resultado = _check_history(DIA)

    # WARN, não PASS: o dado está salvo e legível, porém na mesma máquina que
    # o produziu. PASS é reservado ao destino que sobrevive à perda dela.
    assert resultado["status"] == "WARN"
    assert resultado["rows"] == 500
    assert resultado["partitions"] == 1
    assert "sem cópia fora da máquina" in resultado["detail"]


def test_particao_anemica_vira_warn(frio):
    """Poucas linhas indicam coleta pela metade, não um dia inteiro."""
    write_records(_linhas(10), run_id="teste123", store=frio, already_mapped=True)

    resultado = _check_history(DIA)

    assert resultado["status"] == "WARN"
    assert "abaixo do mínimo" in resultado["detail"]


def test_particao_corrompida_reprova(frio):
    """Arquivo existe, listagem enxerga, leitura falha → FAIL.

    É a diferença entre verificar o backup e apenas conferir que ele está lá.
    """
    write_records(_linhas(500), run_id="teste123", store=frio, already_mapped=True)
    parquet = next((frio.backend.root / "coletas").glob("*.parquet"))
    parquet.write_bytes(b"nao sou um parquet")

    resultado = _check_history(DIA)

    assert resultado["status"] == "FAIL"
    assert resultado["partitions"] == 1
    assert resultado["rows"] == 0


def test_drive_configurado_mas_caindo_em_disco_reprova(tmp_path, monkeypatch):
    """O modo de falha silencioso: pediram Drive, o store degradou para disco.

    `get_store()` cai para o disco de propósito — perder o dia seria pior. Só
    que sem alguém dizendo isso em voz alta, o backup "existe" num diretório
    efêmero e a redundância vira ficção.
    """
    monkeypatch.setenv("RAC_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("RAC_HISTORY_BACKEND", "drive")
    # Sem GDRIVE_FOLDER_ID o backend do Drive nem constrói.
    monkeypatch.delenv("GDRIVE_FOLDER_ID", raising=False)

    resultado = _check_history(DIA)

    assert resultado["status"] == "FAIL"
    assert "Drive configurado mas indisponível" in resultado["detail"]


def test_dia_vizinho_nao_conta(frio):
    """A janela é o dia pedido — véspera no frio não salva o dia de hoje."""
    ontem = [dict(linha, data="2026-07-30") for linha in _linhas(500)]
    write_records(ontem, run_id="teste123", store=frio, already_mapped=True)

    assert _check_history(DIA)["status"] == "FAIL"
    assert _check_history("2026-07-30")["rows"] == 500


def test_check_que_nao_roda_reprova_em_vez_de_sumir(monkeypatch, capsys):
    """pyarrow ausente/credencial podre não pode devolver watchdog verde.

    Antes a exceção só virava `logger.warning` e `hist` ficava `None`: com o
    Supabase saudável, o job terminava verde tendo pulado a verificação do
    backup inteira — a mesma cegueira do incidente, por outro caminho.
    """
    import scripts.daily_status_check as dsc

    monkeypatch.setattr(
        dsc, "_check_history",
        lambda _: (_ for _ in ()).throw(ImportError("No module named 'pyarrow'")),
    )
    monkeypatch.setattr(dsc, "_fetch_counts", lambda *a, **k: {})
    monkeypatch.setattr(
        dsc, "_build_report",
        lambda *a, **k: ([], {"pass": 1, "warn": 0, "fail": 0, "critical_fail": 0}),
    )
    monkeypatch.setattr(sys, "argv", ["daily_status_check.py", "--no-notify"])

    codigo = dsc.main()

    assert codigo != 0, "check indisponível não pode terminar verde"
    assert "HISTÓRICO FRIO: ❌ FAIL" in capsys.readouterr().out
