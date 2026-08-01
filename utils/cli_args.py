"""
utils/cli_args.py — Helpers de argumentos de linha de comando.

Curingas em CLI multiplataforma
-------------------------------
No bash o **shell** expande ``output/rac_monitoramento_*.csv`` antes de o Python
ver os argumentos. No PowerShell e no cmd.exe **não**: o programa recebe a
string literal, com o asterisco. O resultado era um erro que parecia culpa do
usuário::

    PS> python scripts/history_cli.py import-csv output\\rac_monitoramento_*.csv
    ERROR | Arquivo não encontrado: output\\rac_monitoramento_*.csv

Como toda a documentação do projeto (e o PC coletor) é Windows, quem expande é
o Python — assim o mesmo comando copiado do README funciona nas duas shells.
"""

from __future__ import annotations

import glob
from pathlib import Path
from typing import List, Sequence, Tuple

#: Caracteres que fazem de um argumento um padrão, não um caminho.
_WILDCARDS = ("*", "?", "[")


def expand_paths(raw_args: Sequence[str]) -> Tuple[List[Path], List[str]]:
    """Expande curingas que a shell não expandiu, preservando a ordem.

    Args:
        raw_args: Argumentos crus (caminhos e/ou padrões com curinga).

    Returns:
        ``(caminhos, padroes_sem_match)``. Argumentos **sem** curinga passam
        direto, mesmo que não existam — quem chama é que decide o que fazer com
        um arquivo ausente (a mensagem de erro dele é mais específica). Padrões
        que não casaram com nada voltam na segunda lista, para o chamador
        reportá-los em vez de seguir em silêncio.

    Example:
        >>> expand_paths(["output/a.csv", "output/rac_*.csv"])  # doctest: +SKIP
        ([Path('output/a.csv'), Path('output/rac_20260731.csv')], [])
    """
    caminhos: List[Path] = []
    sem_match: List[str] = []
    vistos = set()

    for raw in raw_args:
        if not any(c in raw for c in _WILDCARDS):
            if raw not in vistos:
                vistos.add(raw)
                caminhos.append(Path(raw))
            continue

        # glob.glob (e não Path.glob) porque aceita padrão absoluto e com letra
        # de unidade — "C:\\...\\*.csv" faz Path.glob levantar ValueError.
        achados = sorted(glob.glob(raw))
        if not achados:
            sem_match.append(raw)
            continue
        for achado in achados:
            if achado not in vistos:
                vistos.add(achado)
                caminhos.append(Path(achado))

    return caminhos, sem_match
