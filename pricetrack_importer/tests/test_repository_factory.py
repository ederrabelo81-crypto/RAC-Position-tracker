"""
Testes da factory Repository() — escolha de backend baseado nas env vars.

Não testa o I/O real do Supabase (isso requer credenciais e DB rodando);
foca em garantir que:
- DSN tem prioridade sobre URL+KEY
- Sem nenhuma credencial → RuntimeError com mensagem clara
- URL+KEY → backend supabase-py
"""
from unittest.mock import patch

import pytest

from pricetrack_importer.repository import (
    PsycopgRepository,
    Repository,
    SupabasePyRepository,
)


class TestRepositoryFactory:
    def test_sem_credenciais_levanta_runtime_error(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(RuntimeError, match="Nenhuma credencial Supabase"):
                Repository()

    def test_dsn_explicito_devolve_psycopg(self):
        with patch.dict("os.environ", {}, clear=True):
            # PsycopgRepository pode falhar no __init__ se psycopg2 não instalado,
            # mas o caminho da escolha é o que estamos validando
            try:
                repo = Repository(dsn="postgresql://x:y@h:5432/db")
                assert isinstance(repo, PsycopgRepository)
            except RuntimeError as e:
                # Tolerado: psycopg2 não disponível no ambiente de teste
                assert "psycopg2" in str(e).lower()

    def test_url_e_key_devolve_supabase_py(self):
        with patch.dict(
            "os.environ",
            {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_KEY": "key"},
            clear=True,
        ):
            try:
                repo = Repository()
                assert isinstance(repo, SupabasePyRepository)
            except RuntimeError as e:
                # Tolerado: supabase-py não disponível
                assert "supabase-py" in str(e).lower()

    def test_dsn_tem_prioridade_sobre_url_key(self):
        with patch.dict(
            "os.environ",
            {
                "SUPABASE_DSN": "postgresql://x:y@h:5432/db",
                "SUPABASE_URL": "https://x.supabase.co",
                "SUPABASE_KEY": "key",
            },
            clear=True,
        ):
            try:
                repo = Repository()
                assert isinstance(repo, PsycopgRepository)
            except RuntimeError as e:
                assert "psycopg2" in str(e).lower()

    def test_dsn_string_vazia_cai_no_fallback(self):
        """SUPABASE_DSN setado mas vazio não deve impedir o fallback."""
        with patch.dict(
            "os.environ",
            {
                "SUPABASE_DSN": "",
                "SUPABASE_URL": "https://x.supabase.co",
                "SUPABASE_KEY": "key",
            },
            clear=True,
        ):
            try:
                repo = Repository()
                assert isinstance(repo, SupabasePyRepository)
            except RuntimeError as e:
                assert "supabase-py" in str(e).lower()
