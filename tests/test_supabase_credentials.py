"""
tests/test_supabase_credentials.py — validação das credenciais do Supabase.

Motivação (05/08/2026): uma coleta de 2.223 registros terminou com
`❌ Falha ao criar client: Invalid IPv6 URL`. A causa era um `.env` que ainda
tinha o placeholder do `.env.example` (`https://[YOUR-REF].supabase.co`) — os
colchetes viram sintaxe de IPv6 no parser de URL. A mensagem não tinha
nenhuma relação com a causa nem com a solução.

Rode: pytest tests/test_supabase_credentials.py
"""
import pytest

from utils.supabase_client import _looks_like_placeholder, _validate_credentials

_URL_OK = "https://abcdefghijkl.supabase.co"
_KEY_OK = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.fake-service-role"


class TestLooksLikePlaceholder:
    @pytest.mark.parametrize("valor", [
        "https://[YOUR-REF].supabase.co",     # o do .env.example
        "https://your-project.supabase.co",
        "https://xxxx.supabase.co",
        "<sua-chave-aqui>",
        "https://seu-projeto.supabase.co",
    ])
    def test_placeholders_conhecidos(self, valor):
        assert _looks_like_placeholder(valor) is True

    @pytest.mark.parametrize("valor", [_URL_OK, _KEY_OK])
    def test_credencial_real_passa(self, valor):
        assert _looks_like_placeholder(valor) is False


class TestValidateCredentials:
    def test_url_placeholder_reprovada(self):
        assert _validate_credentials("https://[YOUR-REF].supabase.co", _KEY_OK) is False

    def test_url_sem_esquema_reprovada(self):
        """Sem https:// o create_client falha lá na frente, sem explicar."""
        assert _validate_credentials("abcdefghijkl.supabase.co", _KEY_OK) is False

    def test_key_placeholder_reprovada(self):
        assert _validate_credentials(_URL_OK, "<sua-chave>") is False

    def test_credenciais_reais_aprovadas(self):
        assert _validate_credentials(_URL_OK, _KEY_OK) is True

    def test_url_http_local_aprovada(self):
        """Supabase self-hosted em dev roda em http — não pode ser barrado."""
        assert _validate_credentials("http://localhost:54321", _KEY_OK) is True
