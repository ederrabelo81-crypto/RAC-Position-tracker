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

from utils.supabase_client import (
    _key_role,
    _log_key_role,
    _looks_like_placeholder,
    _validate_credentials,
)

_URL_OK = "https://abcdefghijkl.supabase.co"
_KEY_OK = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.fake-service-role"


class TestLooksLikePlaceholder:
    @pytest.mark.parametrize("valor", [
        "https://[YOUR-REF].supabase.co",     # o do .env.example
        "your_service_role_key",              # idem — a CHAVE do .env.example
        "sua_service_role_key",
        "eyJ...",                             # docstring do supabase_client.py
        "https://SEU-REF.supabase.co",        # exemplo da própria msg de erro
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


class TestColchetes:
    """
    Colchete em URL tem UM uso legítimo: literal IPv6. Barrar todos pegaria o
    Supabase self-hosted em `[::1]` — irônico, já que a validação nasceu de um
    placeholder sendo confundido com IPv6.
    """

    def test_ipv6_loopback_aceito(self):
        assert _validate_credentials("http://[::1]:54321", _KEY_OK) is True

    def test_ipv6_completo_aceito(self):
        assert _validate_credentials(
            "http://[2001:db8::1]:8000", _KEY_OK
        ) is True

    def test_colchete_de_placeholder_reprovado(self):
        assert _validate_credentials("https://[YOUR-REF].supabase.co", _KEY_OK) is False

    def test_colchete_com_host_invalido_reprovado(self):
        assert _validate_credentials("https://[nao-e-ipv6].supabase.co", _KEY_OK) is False

    def test_colchete_na_chave_reprovado(self):
        """Chave real é base64/JWT — colchete ali só vem de documentação."""
        assert _validate_credentials(_URL_OK, "[COLE-SUA-CHAVE]") is False

    def test_colchete_fora_do_host_nao_reprova(self):
        """Colchete em path/query não tem relação com o bug do `[YOUR-REF]`."""
        assert _validate_credentials(
            "https://abcdefghijkl.supabase.co/rest/v1/[id]", _KEY_OK
        ) is True


class TestValidateCredentials:
    def test_url_placeholder_reprovada(self):
        assert _validate_credentials("https://[YOUR-REF].supabase.co", _KEY_OK) is False

    def test_key_do_env_example_reprovada(self):
        """O caso real: .env copiado do .env.example sem trocar a chave."""
        assert _validate_credentials(_URL_OK, "your_service_role_key") is False

    def test_esquema_maiusculo_aceito(self):
        """`HTTPS://` é URL válida — não pode virar 'sem esquema'."""
        assert _validate_credentials("HTTPS://abcdefghijkl.supabase.co", _KEY_OK) is True

    def test_url_sem_esquema_reprovada(self):
        """Sem https:// o create_client falha lá na frente, sem explicar."""
        assert _validate_credentials("abcdefghijkl.supabase.co", _KEY_OK) is False

    @pytest.mark.parametrize("url", ["https://", "http://", "https:///rest/v1"])
    def test_url_so_com_esquema_reprovada(self, url):
        """Tem esquema mas não tem endereço — não pode chegar no create_client."""
        assert _validate_credentials(url, _KEY_OK) is False

    def test_key_placeholder_reprovada(self):
        assert _validate_credentials(_URL_OK, "<sua-chave>") is False

    def test_credenciais_reais_aprovadas(self):
        assert _validate_credentials(_URL_OK, _KEY_OK) is True

    def test_url_http_local_aprovada(self):
        """Supabase self-hosted em dev roda em http — não pode ser barrado."""
        assert _validate_credentials("http://localhost:54321", _KEY_OK) is True


# ---------------------------------------------------------------------------
# Papel embutido na chave (anon x service_role)
# ---------------------------------------------------------------------------
#
# Motivação (11/08/2026): a coleta do notebook Windows subiu 2.921 registros
# sem um erro, e logo depois a etapa `seed_depara` da Automação ADMIN morreu
# com `57014 canceling statement due to statement timeout` em 3,1s. Os dois
# anti-joins do seed levam ~2,4s medidos em produção: cabem nos 120s do
# service_role e estouram nos 3s do anon. Como `coletas` está sem RLS, a chave
# anon insere normalmente — nada no log dizia qual chave estava em uso.

def _jwt(role: str) -> str:
    """JWT sintético (base64url SEM padding, como o Supabase emite)."""
    import base64
    import json

    payload = base64.urlsafe_b64encode(
        json.dumps({"iss": "supabase", "role": role}).encode()
    ).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{payload}.assinatura-falsa"


class TestKeyRole:
    @pytest.mark.parametrize("role", ["anon", "authenticated", "service_role"])
    def test_jwt_legado(self, role):
        assert _key_role(_jwt(role)) == role

    def test_chave_nova_secret_e_service_role(self):
        assert _key_role("sb_secret_1a2b3c4d5e6f") == "service_role"

    def test_chave_nova_publishable_e_anon(self):
        assert _key_role("sb_publishable_1a2b3c4d5e6f") == "anon"

    @pytest.mark.parametrize("valor", ["", "   ", "chave-opaca", "a.b", "a.b.c"])
    def test_formato_desconhecido_nao_afirma_nada(self, valor):
        """Sem formato reconhecido não inventamos papel — None é a resposta."""
        assert _key_role(valor) is None

    def test_jwt_sem_claim_role(self):
        import base64
        import json

        payload = base64.urlsafe_b64encode(json.dumps({"iss": "x"}).encode()).decode()
        assert _key_role(f"eyJ0eXAiOiJKV1QifQ.{payload}.sig") is None


class TestLogKeyRole:
    def test_avisa_uma_vez_por_processo(self, monkeypatch):
        """O client é criado várias vezes por run — o aviso não pode repetir."""
        import utils.supabase_client as sc

        monkeypatch.setattr(sc, "_KEY_ROLE_LOGGED", False)
        assert sc._log_key_role(_jwt("anon")) == "anon"
        assert sc._KEY_ROLE_LOGGED is True
        # 2ª chamada continua devolvendo o papel (sem logar de novo)
        assert sc._log_key_role(_jwt("anon")) == "anon"
