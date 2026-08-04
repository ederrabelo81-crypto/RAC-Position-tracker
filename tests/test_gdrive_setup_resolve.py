"""
tests/test_gdrive_setup_resolve.py — Resolução do `--client-secrets`.

Existe por um erro real no PC coletor (Windows, 03/08/2026). O nome do arquivo
que o Google entrega é ``client_secret_<id>.apps.googleusercontent.com.json``,
e a página do Console mostra o *client id* — a mesma cauda
``.apps.googleusercontent.com``, sem o ``.json``. O mantenedor copiou o client
id quatro vezes seguidas e levou "Arquivo de credenciais não encontrado", que
parecia dizer que o download tinha falhado.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.gdrive_setup import _resolver_client_secrets  # noqa: E402

_ID1 = "client_secret_116189622340-35eto3kqi96ric6u98kbb4nuudogiou7.apps.googleusercontent.com"
_ID2 = "client_secret_2_116189622340-v5s36tf401lgmjk8tnkgc3pakk1aa9kp.apps.googleusercontent.com"


def _escreve_cliente(pasta: Path, nome: str) -> Path:
    caminho = pasta / f"{nome}.json"
    caminho.write_text(
        json.dumps({"installed": {"client_id": nome, "client_secret": "GOCSPX-x"}}),
        encoding="utf-8",
    )
    return caminho


@pytest.fixture
def downloads(tmp_path) -> Path:
    d = tmp_path / "Downloads"
    d.mkdir()
    return d


def test_client_id_sem_json_no_fim(downloads):
    """O caso que travou o mantenedor: falta só a extensão."""
    esperado = _escreve_cliente(downloads, _ID1)

    assert _resolver_client_secrets(str(downloads / _ID1)) == esperado


def test_pasta_com_um_cliente(downloads):
    esperado = _escreve_cliente(downloads, _ID1)

    assert _resolver_client_secrets(str(downloads)) == esperado


def test_pasta_com_dois_clientes_nao_escolhe(downloads):
    """Cada JSON é um cliente OAuth distinto e o refresh token vale só para o
    que autorizou — escolher pelo usuário autorizaria o cliente errado."""
    _escreve_cliente(downloads, _ID1)
    _escreve_cliente(downloads, _ID2)

    assert _resolver_client_secrets(str(downloads)) is None


def test_curinga_que_casa_um(downloads):
    _escreve_cliente(downloads, _ID1)
    esperado = _escreve_cliente(downloads, _ID2)

    assert _resolver_client_secrets(str(downloads / "client_secret_2_*.json")) == esperado


def test_caminho_exato(downloads):
    esperado = _escreve_cliente(downloads, _ID1)

    assert _resolver_client_secrets(str(esperado)) == esperado


def test_inexistente_volta_none(downloads):
    _escreve_cliente(downloads, _ID1)

    assert _resolver_client_secrets(str(downloads / "sumiu.json")) is None


def test_pasta_vazia_volta_none(downloads):
    assert _resolver_client_secrets(str(downloads)) is None
