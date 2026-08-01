"""
tests/test_cli_args.py — Expansão de curingas nos CLIs (utils/cli_args).

Existe por um erro real no PC coletor (Windows, 31/07/2026): o comando que a
documentação manda copiar

    python scripts/history_cli.py import-csv output\\rac_monitoramento_*.csv

funciona no bash (o shell expande) e falha no PowerShell (chega literal), com
uma mensagem que parecia erro do usuário: "Arquivo não encontrado:
output\\rac_monitoramento_*.csv".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.cli_args import expand_paths  # noqa: E402


@pytest.fixture
def output_dir(tmp_path, monkeypatch) -> Path:
    """Três CSVs de coleta e um arquivo que não deve casar com o padrão."""
    out = tmp_path / "output"
    out.mkdir()
    for nome in (
        "rac_monitoramento_20260729_1013.csv",
        "rac_monitoramento_20260730_2107.csv",
        "rac_monitoramento_20260731_1713.csv",
        "outro_arquivo.txt",
    ):
        (out / nome).write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return out


def test_expande_curinga_relativo(output_dir):
    caminhos, sem_match = expand_paths(["output/rac_monitoramento_*.csv"])

    assert [p.name for p in caminhos] == [
        "rac_monitoramento_20260729_1013.csv",
        "rac_monitoramento_20260730_2107.csv",
        "rac_monitoramento_20260731_1713.csv",
    ]
    assert sem_match == []


def test_ordem_cronologica_pelo_nome(output_dir):
    """Nome do CSV começa com a data, então ordenar por nome ordena por tempo."""
    caminhos, _ = expand_paths(["output/rac_monitoramento_*.csv"])

    assert caminhos == sorted(caminhos, key=lambda p: p.name)


def test_curinga_absoluto(output_dir):
    """Path.glob levanta ValueError em padrão absoluto; glob.glob não."""
    caminhos, sem_match = expand_paths([str(output_dir / "rac_*.csv")])

    assert len(caminhos) == 3
    assert sem_match == []


def test_padrao_sem_match_e_reportado(output_dir):
    caminhos, sem_match = expand_paths(["output/nao_existe_*.csv"])

    assert caminhos == []
    assert sem_match == ["output/nao_existe_*.csv"]


def test_caminho_literal_inexistente_passa_adiante(tmp_path, monkeypatch):
    """Sem curinga o arquivo passa direto: o erro de "não existe" é do chamador,
    que tem uma mensagem mais específica que a daqui."""
    monkeypatch.chdir(tmp_path)

    caminhos, sem_match = expand_paths(["output/sumiu.csv"])

    assert caminhos == [Path("output/sumiu.csv")]
    assert sem_match == []


def test_nao_duplica_arquivo_coberto_por_dois_padroes(output_dir):
    caminhos, _ = expand_paths(
        ["output/rac_monitoramento_2026073*.csv", "output/rac_*.csv"]
    )

    assert len(caminhos) == len({str(p) for p in caminhos}) == 3


def test_nao_duplica_literal_e_curinga_com_grafias_diferentes(output_dir):
    """``output/a.csv`` e ``./output/*.csv`` são o mesmo arquivo.

    O glob devolve o caminho com o ``./`` na frente; comparar as strings cruas
    deixaria passar duas entradas para o mesmo CSV — e aí o import/upload
    aconteceria duas vezes (linhas duplicadas no Supabase, espelho repetido).
    """
    alvo = "output/rac_monitoramento_20260731_1713.csv"

    caminhos, sem_match = expand_paths([alvo, "./output/rac_monitoramento_2026073*.csv"])

    assert sem_match == []
    # O literal vem primeiro (ordem dos argumentos) e não se repete quando o
    # curinga o alcança de novo, com outra grafia.
    assert [p.name for p in caminhos] == [
        "rac_monitoramento_20260731_1713.csv",
        "rac_monitoramento_20260730_2107.csv",
    ]


def test_nao_duplica_caminho_absoluto_e_relativo(output_dir):
    """Mesmo arquivo por caminho absoluto e por curinga relativo."""
    absoluto = str(output_dir / "rac_monitoramento_20260729_1013.csv")

    caminhos, _ = expand_paths([absoluto, "output/rac_monitoramento_*.csv"])

    assert len(caminhos) == 3


def test_mistura_de_literais_e_padroes(output_dir):
    caminhos, sem_match = expand_paths(
        ["output/outro_arquivo.txt", "output/rac_monitoramento_202607_3*.csv"]
    )

    assert [p.name for p in caminhos] == ["outro_arquivo.txt"]
    assert sem_match == ["output/rac_monitoramento_202607_3*.csv"]
