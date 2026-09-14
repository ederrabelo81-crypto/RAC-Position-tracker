"""
Testes da factory Repository() — escolha de backend baseado nas env vars.

Não testa o I/O real do Supabase (isso requer credenciais e DB rodando);
foca em garantir que:
- DSN tem prioridade sobre URL+KEY
- RAC_DB_DSN (banco novo, Set/2026) tem precedência sobre SUPABASE_DSN
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
            with pytest.raises(RuntimeError, match="Nenhuma credencial de banco"):
                Repository()

    def test_mensagem_de_erro_documenta_rac_db_dsn(self):
        # A mensagem é a única orientação que o operador recebe quando o
        # importador não acha credencial. Depois da virada, omitir RAC_DB_DSN
        # ali o mandaria configurar justamente o banco restrito por cota.
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(RuntimeError) as exc:
                Repository()
        assert "RAC_DB_DSN" in str(exc.value)

    def test_rac_db_dsn_tem_precedencia_sobre_supabase_dsn(self):
        # Depois da virada as duas variáveis apontam para bancos DIFERENTES:
        # RAC_DB_DSN é o novo, SUPABASE_DSN é o velho (de onde saem as
        # referências e o pricetrack evacuado). Escolher o errado aqui faria o
        # importador reconstruir a tabela que a migração acabou de esvaziar.
        with patch.dict(
            "os.environ",
            {
                "RAC_DB_DSN": "postgresql://novo:s@aiven:5432/defaultdb",
                "SUPABASE_DSN": "postgresql://velho:s@supabase:5432/postgres",
            },
            clear=True,
        ):
            try:
                repo = Repository()
                assert isinstance(repo, PsycopgRepository)
                assert "aiven" in repo.dsn
                assert "supabase" not in repo.dsn
            except RuntimeError as e:
                assert "psycopg2" in str(e).lower()

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
