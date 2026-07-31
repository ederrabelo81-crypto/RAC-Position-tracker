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

    A varredura desce por classes e funções aninhadas, e atribui cada import à
    função **mais interna** que o contém — que é a única cujo escopo ele
    resombreia. Reportar pelo escopo de fora apontaria a linha certa com o nome
    errado.

    Args:
        source: Código-fonte do arquivo.

    Returns:
        Tuplas ``(função, nome, linha)``, uma por ocorrência — vazia quando não
        há sombreamento.
    """
    tree = ast.parse(source)
    do_topo = _top_level_import_names(tree)
    achados: List[Tuple[str, str, int]] = []

    def visitar(no: ast.AST, funcao_atual: str) -> None:
        for filho in ast.iter_child_nodes(no):
            if isinstance(filho, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visitar(filho, filho.name)
                continue
            if funcao_atual and isinstance(filho, (ast.Import, ast.ImportFrom)):
                for alias in filho.names:
                    nome = (alias.asname or alias.name).split(".")[0]
                    if nome in do_topo:
                        achados.append((funcao_atual, nome, filho.lineno))
            visitar(filho, funcao_atual)

    visitar(tree, "")
    return achados


def test_detector_aponta_a_funcao_mais_interna():
    """Import em função aninhada é reportado uma vez, com o nome de dentro."""
    fonte = (
        "import os\n"
        "\n"
        "def externa():\n"
        "    def interna():\n"
        "        import os\n"
        "        return os\n"
        "    return interna\n"
    )
    assert _shadowing_imports(fonte) == [("interna", "os", 5)]


def test_detector_desce_em_metodos_de_classe():
    """Método de classe também tem escopo próprio — precisa ser varrido."""
    fonte = (
        "import json\n"
        "\n"
        "class C:\n"
        "    def metodo(self):\n"
        "        import json\n"
        "        return json\n"
    )
    assert _shadowing_imports(fonte) == [("metodo", "json", 5)]


def test_detector_ignora_import_local_de_nome_novo():
    """Só resombreamento importa: import local de nome inédito é legítimo."""
    fonte = (
        "import os\n"
        "\n"
        "def f():\n"
        "    from utils.history import get_store\n"
        "    return get_store, os\n"
    )
    assert _shadowing_imports(fonte) == []


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
