"""
tests/test_watchdog_exit_codes.py — "não consegui olhar" ≠ "olhei e não tinha".

O watchdog falhou 9 runs seguidos (Ago/2026) porque o Supabase respondia
`401 Unregistered API key`. O script saía com exit 2 — o mesmo código de
qualquer outro problema de banco — e a causa real ficava só no meio do log,
indistinguível de uma coleta que simplesmente não trouxe dado.

Estes testes travam a distinção: credencial recusada, serviço inacessível e
configuração ausente têm códigos e mensagens próprios, e o resultado do
HISTÓRICO FRIO (que não depende do banco) aparece em todos os cenários.

Rode: pytest tests/test_watchdog_exit_codes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.daily_status_check as dsc  # noqa: E402

_HIST_OK = {
    "status": "PASS",
    "backend": "Google Drive",
    "partitions": 2,
    "rows": 5843,
    "detail": "5.843 linhas em 2 partição(ões) → Google Drive",
}


class TestClassificacao:
    """A natureza do erro sai do texto da exceção do PostgREST."""

    @pytest.mark.parametrize(
        "mensagem",
        [
            '{"message":"Unregistered API key","code":401}',
            "HTTPStatusError: 403 Forbidden",
            "JWT expired",
            "No API key found in request",
        ],
    )
    def test_credencial_recusada(self, mensagem):
        falha = dsc._classify_supabase_error(Exception(mensagem))
        assert falha.kind == "auth"
        assert falha.exit_code == dsc.EXIT_SUPABASE_AUTH
        assert falha.remedy, "erro de credencial precisa dizer o que fazer"

    @pytest.mark.parametrize(
        "mensagem",
        [
            "ReadTimeout: connection timed out",
            '{"code":"402","message":"exceed_db_size_quota"}',
            "502 Bad Gateway",
        ],
    )
    def test_servico_inacessivel(self, mensagem):
        falha = dsc._classify_supabase_error(Exception(mensagem))
        assert falha.kind == "conexao"
        assert falha.exit_code == dsc.EXIT_SUPABASE_CONEXAO

    def test_erro_desconhecido_nao_acusa_a_chave(self):
        """Mandar trocar uma chave provavelmente correta custa caro."""
        falha = dsc._classify_supabase_error(Exception("algo inesperado"))
        assert falha.kind != "auth"

    def test_sem_credencial_configurada(self):
        falha = dsc._erro_supabase_nao_configurado()
        assert falha.exit_code == dsc.EXIT_SUPABASE_CONFIG

    def test_e_runtime_error(self):
        """Os checks best-effort capturam RuntimeError — a herança importa."""
        assert issubclass(dsc.SupabaseUnavailable, RuntimeError)


class TestExitCodesDoMain:
    """Os códigos que viram o status do job no GitHub Actions."""

    @pytest.fixture(autouse=True)
    def _frio_verificavel(self, monkeypatch):
        monkeypatch.setattr(dsc, "_check_history", lambda _: dict(_HIST_OK))
        monkeypatch.setattr(sys, "argv", ["daily_status_check.py", "--no-notify"])

    def _falhar_com(self, monkeypatch, exc: Exception) -> None:
        def boom(*_a, **_k):
            raise dsc._classify_supabase_error(exc)
        monkeypatch.setattr(dsc, "_fetch_counts", boom)

    def test_401_sai_com_codigo_de_auth(self, monkeypatch, capsys):
        self._falhar_com(
            monkeypatch, Exception('{"message":"Unregistered API key","code":401}')
        )

        codigo = dsc.main()

        assert codigo == dsc.EXIT_SUPABASE_AUTH
        saida = capsys.readouterr().out
        assert "Supabase rejeitou a credencial" in saida
        # A distinção precisa estar visível, não só implícita no código.
        assert "não é o mesmo que" in saida.lower()

    def test_timeout_sai_com_codigo_de_conexao(self, monkeypatch):
        self._falhar_com(monkeypatch, Exception("ReadTimeout: connection timed out"))
        assert dsc.main() == dsc.EXIT_SUPABASE_CONEXAO

    def test_sem_configuracao_sai_com_codigo_de_config(self, monkeypatch):
        def boom(*_a, **_k):
            raise dsc._erro_supabase_nao_configurado()
        monkeypatch.setattr(dsc, "_fetch_counts", boom)

        assert dsc.main() == dsc.EXIT_SUPABASE_CONFIG

    def test_historico_frio_aparece_mesmo_sem_banco(self, monkeypatch, capsys):
        """O frio é a única fonte verificável quando o banco está fora."""
        self._falhar_com(monkeypatch, Exception("401 Unregistered API key"))

        dsc.main()

        assert "HISTÓRICO FRIO: ✅ PASS" in capsys.readouterr().out

    def test_dado_ausente_continua_com_codigo_proprio(self, monkeypatch):
        """Banco respondendo e coleta vazia é outro problema — e outro código."""
        monkeypatch.setattr(dsc, "_fetch_counts", lambda *a, **k: {})
        monkeypatch.setattr(
            dsc, "_build_report",
            lambda *a, **k: ([], {"pass": 0, "warn": 0, "fail": 3, "critical_fail": 3}),
        )

        codigo = dsc.main()

        assert codigo == dsc.EXIT_DADO_AUSENTE
        assert codigo not in (
            dsc.EXIT_SUPABASE_CONFIG,
            dsc.EXIT_SUPABASE_AUTH,
            dsc.EXIT_SUPABASE_CONEXAO,
        )
