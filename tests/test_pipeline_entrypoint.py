"""
tests/test_pipeline_entrypoint.py — Guardas estáticas do entrypoint da coleta.

Existe por causa de um incidente real: um `import os` dentro de `main()` tornou
`os` um nome local em todo o corpo da função, e a linha que decide se o
histórico frio é gravado (`os.getenv("RAC_HISTORY", ...)`) — executada **antes**
daquele import — passou a levantar `UnboundLocalError`.

O efeito foi silencioso do jeito pior: o CSV era exportado normalmente e o
processo morria logo depois, sem gravar histórico nem subir para o Supabase.
Doze coletas agendadas (26–31/07/2026) terminaram assim.

Nenhum teste de unidade dos scrapers pega isso — só o run inteiro pegaria. Uma
checagem de AST pega, e é barata.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import List, Set, Tuple

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Módulos de entrada: funções longas, com imports locais legítimos (adiados
# para não pesar o import do módulo) misturados a nomes já importados no topo.
ENTRYPOINTS = ["main.py", "app.py"]


def _top_level_import_names(tree: ast.Module) -> Set[str]:
    """Nomes ligados por imports no escopo de módulo."""
    nomes: Set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                nomes.add((alias.asname or alias.name).split(".")[0])
    return nomes


def _shadowing_imports(source: str) -> List[Tuple[str, str, int]]:
    """Imports dentro de funções que resombreiam um import de módulo.

    Args:
        source: Código-fonte do arquivo.

    Returns:
        Tuplas ``(função, nome, linha)`` — vazia quando não há sombreamento.
    """
    tree = ast.parse(source)
    do_topo = _top_level_import_names(tree)

    achados: List[Tuple[str, str, int]] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(func):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for alias in node.names:
                nome = (alias.asname or alias.name).split(".")[0]
                if nome in do_topo:
                    achados.append((func.name, nome, node.lineno))
    return achados


@pytest.mark.parametrize("arquivo", ENTRYPOINTS)
def test_sem_import_local_que_resombreia_o_topo(arquivo: str):
    """Um import local de nome já importado no topo quebra a função inteira."""
    caminho = REPO_ROOT / arquivo
    achados = _shadowing_imports(caminho.read_text(encoding="utf-8"))
    assert not achados, (
        f"{arquivo}: import local resombreia nome do topo — toda referência "
        f"anterior na mesma função vira UnboundLocalError. Remova o import "
        f"local. Ocorrências (função, nome, linha): {achados}"
    )


def test_historico_e_gravado_antes_do_upload_ao_supabase():
    """A escrita do histórico não pode depender do Supabase ter dado certo.

    A ordem é a garantia de durabilidade documentada em `docs/HISTORICO_DRIVE.md`:
    com o banco restrito por cota, o dia ainda entra no Parquet.
    """
    fonte = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
    pos_historico = fonte.find("from utils.history import write_records")
    pos_supabase = fonte.find("from utils.supabase_client import upload_to_supabase")
    assert pos_historico != -1, "escrita do histórico sumiu de main.py"
    assert pos_supabase != -1, "upload ao Supabase sumiu de main.py"
    assert pos_historico < pos_supabase, (
        "o histórico frio precisa ser gravado ANTES do upload ao Supabase"
    )
