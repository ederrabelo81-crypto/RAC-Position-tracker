"""Testes do run_id que viaja com o CSV (`utils/run_sidecar.py`).

Regressão de 28–29/09/2026: o reforço `upload_csv.py` do .bat de coleta
derivava um run_id NOVO para o CSV que o `main.py` já tinha gravado, e o
turno inteiro entrava duas vezes em `coletas`.

Rodar:
    pytest tests/test_run_sidecar.py -q
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.run_sidecar import (  # noqa: E402
    derive_run_id,
    read_run_id,
    resolve_run_id,
    sidecar_path,
    write_run_id,
)


def _csv(tmp_path: Path) -> Path:
    p = tmp_path / "rac_monitoramento_20260929_0800.csv"
    p.write_text("Data;Plataforma\n", encoding="utf-8")
    return p


class TestArquivoIrmao:
    def test_extensao_fica_fora_do_curinga_dos_csvs(self, tmp_path):
        irmao = sidecar_path(_csv(tmp_path))
        assert irmao.name == "rac_monitoramento_20260929_0800.run_id"
        assert not irmao.match("rac_monitoramento_*.csv")

    def test_ida_e_volta(self, tmp_path):
        csv = _csv(tmp_path)
        rid = str(uuid.uuid4())
        write_run_id(csv, rid)
        assert read_run_id(csv) == rid

    def test_reenvio_reusa_a_run_da_coleta(self, tmp_path):
        """O ponto do módulo: mesmo run_id = mesma chave única = sem cópia."""
        csv = _csv(tmp_path)
        rid = str(uuid.uuid4())
        write_run_id(csv, rid)
        assert resolve_run_id(csv) == (rid, "coleta")

    def test_sem_irmao_cai_no_nome_do_arquivo(self, tmp_path):
        csv = _csv(tmp_path)
        rid, origem = resolve_run_id(csv)
        assert origem == "nome do arquivo"
        assert rid == derive_run_id(csv)
        assert uuid.UUID(rid).version == 5

    def test_derivacao_mantem_o_namespace_historico(self, tmp_path):
        """Reimportar CSV antigo não pode mudar de run_id (idempotência)."""
        csv = _csv(tmp_path)
        esperado = uuid.uuid5(uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8"), csv.name)
        assert derive_run_id(csv) == str(esperado)

    def test_irmao_corrompido_e_ignorado(self, tmp_path):
        csv = _csv(tmp_path)
        sidecar_path(csv).write_text("não-é-uuid", encoding="utf-8")
        assert read_run_id(csv) is None
        assert resolve_run_id(csv)[1] == "nome do arquivo"


class TestChamadores:
    def test_upload_csv_usa_a_run_da_coleta(self, tmp_path):
        from scripts.upload_csv import _derive_run_id
        csv = _csv(tmp_path)
        rid = str(uuid.uuid4())
        write_run_id(csv, rid)
        assert _derive_run_id(csv) == rid

    def test_history_cli_usa_a_run_da_coleta(self, tmp_path):
        from scripts.history_cli import _derive_run_id
        csv = _csv(tmp_path)
        rid = str(uuid.uuid4())
        write_run_id(csv, rid)
        assert _derive_run_id(csv) == rid

    def test_main_grava_o_irmao_logo_apos_o_csv(self):
        """Guarda de regressão: a chamada tem que existir no fluxo do main.py."""
        fonte = (ROOT / "main.py").read_text(encoding="utf-8")
        i_csv = fonte.index("csv_path = _export_csv(all_records, args.output_dir)")
        i_run = fonte.index("_write_run_id(csv_path, RUN_ID)")
        i_up = fonte.index("upload_to_supabase(all_records, run_id=RUN_ID)")
        assert i_csv < i_run < i_up
