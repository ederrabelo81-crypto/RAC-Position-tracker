"""
scripts/n8n_upload.py — Ponte de upload para o workflow n8n.

Chamado pelo nó "Upload CSV" do workflow rac_coleta_monitor. Valida o CSV
da coleta manual (existência + cabeçalho de 19 colunas) e, se passar,
delega o envio ao Supabase para reenviar_csv.reenviar().

Funciona em Windows e Linux — o nó n8n só precisa apontar o Python do venv
para este arquivo. A raiz do projeto é deduzida da localização do script.

Uso:
    python scripts/n8n_upload.py rac_monitoramento_20260522_1030_magalu.csv

Saída:
    Imprime "RAC_FAIL|<motivo>" no stdout quando a validação falha; o nó
    "Montar Resultado" do n8n lê esse marcador. Quando passa, reenviar()
    loga o resultado ("Upload concluído sem discrepâncias" / "Discrepância").
"""

import os
import sys
from pathlib import Path

EXPECTED_COLUMNS = 19


def main() -> None:
    """Valida o CSV recebido e dispara o upload ao Supabase."""
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("RAC_FAIL|nome de arquivo nao informado")
        return

    filename = sys.argv[1].strip()
    project_root = Path(__file__).resolve().parent.parent
    csv_path = project_root / "output" / filename

    if not csv_path.is_file():
        print(f"RAC_FAIL|arquivo nao encontrado: output/{filename}")
        return

    try:
        with csv_path.open("r", encoding="utf-8-sig") as fh:
            header = fh.readline().rstrip("\r\n")
    except OSError as exc:
        print(f"RAC_FAIL|nao foi possivel ler o arquivo: {exc}")
        return

    n_cols = len(header.split(";"))
    if n_cols != EXPECTED_COLUMNS:
        print(
            f"RAC_FAIL|cabecalho invalido: {n_cols} colunas "
            f"(esperado {EXPECTED_COLUMNS})"
        )
        return

    # Validação OK — delega o upload (reenviar_csv loga via loguru).
    os.chdir(project_root)
    sys.path.insert(0, str(project_root))
    try:
        from reenviar_csv import reenviar
        reenviar(str(csv_path))
    except Exception as exc:  # boundary: reporta a falha ao n8n
        print(f"RAC_FAIL|erro no upload: {exc}")


if __name__ == "__main__":
    main()
