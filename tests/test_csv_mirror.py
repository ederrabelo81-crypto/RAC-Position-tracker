"""
tests/test_csv_mirror.py — Espelho do CSV da coleta no Drive.

O que precisa ser garantido (e o que quebraria em silêncio):

* com o Drive configurado, o CSV é enviado para ``csv_coletas/<nome>``;
* sem credencial (backend local), o espelho **não** finge sucesso — devolve
  ``None`` e avisa, porque nesse cenário o dado existe numa máquina só;
* falha de rede/credencial **não** derruba a coleta (o CSV local é a fonte).

A suíte não fala com a rede: o backend do Drive é um duplo em memória.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.history import backends as backends_mod  # noqa: E402
from utils.history.backends import HistoryBackendError  # noqa: E402
from utils.history.csv_mirror import (  # noqa: E402
    DATASET_CSV,
    csv_mirror_enabled,
    mirror_csv_to_drive,
)


@pytest.fixture
def csv_file(tmp_path) -> Path:
    """CSV mínimo no formato de saída da coleta."""
    path = tmp_path / "rac_monitoramento_20260731_1713.csv"
    path.write_text(
        "Data;Plataforma;Produto / SKU\n2026-07-31;Magalu;Ar Condicionado Midea\n",
        encoding="utf-8-sig",
    )
    return path


class _FakeDrive:
    """Duplo do ``GoogleDriveBackend``: registra os puts, opcionalmente falha."""

    def __init__(self, root_folder_id: str, falha: bool = False) -> None:
        if not root_folder_id:
            raise HistoryBackendError("GDRIVE_FOLDER_ID não definido")
        self.root_folder_id = root_folder_id
        self.falha = falha
        self.puts: dict = {}

    def put(self, key: str, payload: bytes) -> None:
        if self.falha:
            raise HistoryBackendError("rede caiu")
        self.puts[key] = payload


@pytest.fixture
def drive_env(monkeypatch):
    """Ambiente com Drive configurado + backend substituído pelo duplo."""
    monkeypatch.setenv("RAC_HISTORY_BACKEND", "drive")
    monkeypatch.setenv("GDRIVE_FOLDER_ID", "folder-123")
    monkeypatch.delenv("RAC_DRIVE_CSV", raising=False)

    criados = []

    def _factory(root_folder_id, *a, **kw):
        fake = _FakeDrive(root_folder_id, falha=kw.pop("falha", False))
        criados.append(fake)
        return fake

    monkeypatch.setattr("utils.history.csv_mirror.GoogleDriveBackend", _factory)
    return criados


def test_sobe_para_a_subpasta_de_csv(csv_file, drive_env):
    key = mirror_csv_to_drive(csv_file)

    assert key == f"{DATASET_CSV}/{csv_file.name}"
    assert drive_env[0].root_folder_id == "folder-123"
    assert drive_env[0].puts[key] == csv_file.read_bytes()


def test_backend_local_nao_espelha(csv_file, monkeypatch):
    """Sem Drive resolvido, devolve None — nunca 'sucesso' com o dado parado."""
    monkeypatch.setenv("RAC_HISTORY_BACKEND", "local")
    monkeypatch.delenv("GDRIVE_FOLDER_ID", raising=False)

    assert mirror_csv_to_drive(csv_file) is None


def test_desligado_por_env(csv_file, drive_env, monkeypatch):
    monkeypatch.setenv("RAC_DRIVE_CSV", "off")

    assert not csv_mirror_enabled()
    assert mirror_csv_to_drive(csv_file) is None
    assert drive_env == []  # nem tentou construir o backend


def test_falha_do_drive_nao_levanta(csv_file, monkeypatch):
    """Coleta não pode morrer por causa do espelho — o CSV local já está salvo."""
    monkeypatch.setenv("RAC_HISTORY_BACKEND", "drive")
    monkeypatch.setenv("GDRIVE_FOLDER_ID", "folder-123")

    def _quebrado(root_folder_id, *a, **kw):
        return _FakeDrive(root_folder_id, falha=True)

    monkeypatch.setattr("utils.history.csv_mirror.GoogleDriveBackend", _quebrado)

    assert mirror_csv_to_drive(csv_file) is None


def test_erro_inesperado_do_backend_nao_levanta(csv_file, monkeypatch):
    """Dependência ausente/credencial corrompida vira log, não exceção."""
    monkeypatch.setenv("RAC_HISTORY_BACKEND", "drive")
    monkeypatch.setenv("GDRIVE_FOLDER_ID", "folder-123")

    def _explode(*a, **kw):
        raise ImportError("google-api-python-client não instalado")

    monkeypatch.setattr("utils.history.csv_mirror.GoogleDriveBackend", _explode)

    assert mirror_csv_to_drive(csv_file) is None


def test_csv_inexistente(tmp_path, drive_env):
    assert mirror_csv_to_drive(tmp_path / "nao_existe.csv") is None
    assert drive_env == []


def test_arquivo_que_desaparece_entre_o_teste_e_o_stat(csv_file, drive_env, monkeypatch):
    """Corrida is_file()/stat(): OSError vazando quebraria o "nunca levanta"."""
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(
        Path, "stat", lambda self, **kw: (_ for _ in ()).throw(OSError("sumiu"))
    )

    assert mirror_csv_to_drive(csv_file) is None
    assert drive_env == []


def test_nenhum_stat_depois_do_gate_do_backend(csv_file, drive_env, monkeypatch):
    """O tamanho é lido UMA vez, dentro do try — nunca depois dele.

    Este caso existe porque a primeira correção da corrida deixou um segundo
    `path.stat()` para trás, fora de qualquer proteção. O teste da corrida
    passava mesmo assim (o mock quebrava já na primeira chamada, dentro do try)
    enquanto a chamada desprotegida seguia lá.

    Aqui o `stat()` só passa a falhar **depois** do gate `resolve_backend_name()`
    — que é onde a chamada residual ficava. Não conta chamadas (o próprio
    `is_file()` faz stat por dentro, detalhe do CPython): afirma só que nada
    volta ao disco depois de decidido o destino.
    """
    real_stat = Path.stat
    passou_do_gate = {"sim": False}

    def _resolve():
        passou_do_gate["sim"] = True
        return "drive"

    def _stat_proibido_depois(self, **kw):
        if passou_do_gate["sim"]:
            raise OSError("stat fora do try: a corrida TOCTOU voltou")
        return real_stat(self, **kw)

    monkeypatch.setattr("utils.history.csv_mirror.resolve_backend_name", _resolve)
    monkeypatch.setattr(Path, "stat", _stat_proibido_depois)

    assert mirror_csv_to_drive(csv_file) == f"{DATASET_CSV}/{csv_file.name}"
    assert passou_do_gate["sim"], "o gate do backend nem foi consultado"


def test_arquivo_gigante_e_recusado(tmp_path, drive_env, monkeypatch):
    """Teto de tamanho: arquivo errado não vira upload de 500 MB no Drive."""
    monkeypatch.setattr("utils.history.csv_mirror._MAX_MB", 0.0001)
    grande = tmp_path / "rac_monitoramento_gigante.csv"
    grande.write_bytes(b"x" * 2048)

    assert mirror_csv_to_drive(grande) is None
    assert drive_env == []


def test_backend_real_e_o_do_modulo_de_backends():
    """Guarda contra o factory dos testes divergir da classe de produção."""
    from utils.history import csv_mirror

    assert csv_mirror.GoogleDriveBackend is backends_mod.GoogleDriveBackend
