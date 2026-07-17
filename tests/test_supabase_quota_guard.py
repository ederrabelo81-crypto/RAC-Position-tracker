"""
tests/test_supabase_quota_guard.py — Guarda contra o estado "projeto restrito
por cota" do Supabase (HTTP 402 / exceed_db_size_quota).

Quando o banco estoura a cota de armazenamento, a API REST responde 402 em
TODAS as operações. Antes do guard, o upload repetia a mesma falha em cada lote
(7 avisos) e a automação ADMIN rodava as 11 etapas — todas falhando igual. Estes
testes fixam o comportamento fail-fast: detectar cedo e abortar com UMA mensagem.

Sem rede/Supabase — clients falsos que levantam o erro real do PostgREST.
"""
import pytest
from loguru import logger

import utils.admin_automation as admin
import utils.supabase_client as sc
from utils.supabase_client import is_quota_restricted_error, upload_to_supabase


# Payload idêntico ao que o postgrest devolve no estado restrito (do log real).
_RESTRICTED = {
    "message": "JSON could not be generated",
    "code": 402,
    "hint": "Refer to full message for details",
    "details": (
        'b\'{"message":"Service for this project is restricted due to the '
        "following violations: exceed_db_size_quota. The project owner must "
        'upgrade their plan or remove spend caps to restore service."}\''
    ),
}

# 402 de restrição SEM ser cota de armazenamento (ex.: egress / pagamento).
# Deve cair no tratamento comum — não na remediação de "disco cheio".
_OTHER_402 = {
    "message": "JSON could not be generated",
    "code": 402,
    "details": (
        'b\'{"message":"Service for this project is restricted due to the '
        'following violations: exceed_egress_quota."}\''
    ),
}


class _APIError(Exception):
    """Imita postgrest.exceptions.APIError: tem .code e str() = repr do dict."""

    def __init__(self, payload: dict):
        self.code = payload.get("code")
        super().__init__(str(payload))


class TestIsQuotaRestrictedError:
    def test_detecta_payload_completo(self):
        assert is_quota_restricted_error(_APIError(_RESTRICTED)) is True

    def test_detecta_por_dict_repr(self):
        assert is_quota_restricted_error(Exception(str(_RESTRICTED))) is True

    def test_detecta_so_pela_string_details(self):
        assert is_quota_restricted_error(Exception(_RESTRICTED["details"])) is True

    def test_ignora_402_de_outra_restricao(self):
        # 402 de egress/pagamento não é cota de disco — remediação diferente.
        assert is_quota_restricted_error(_APIError(_OTHER_402)) is False

    def test_ignora_erro_de_coluna_ausente(self):
        exc = Exception("{'code':'PGRST204','message':'column x could not be found'}")
        assert is_quota_restricted_error(exc) is False

    def test_ignora_timeout(self):
        assert is_quota_restricted_error(Exception("read timed out")) is False

    def test_nao_confunde_402_solto_no_texto(self):
        assert is_quota_restricted_error(Exception("processed 402 items ok")) is False


def _fake_client_raising(calls):
    """Client falso cujas operações levantam o erro 402 restrito."""

    class _Exec:
        def __init__(self, kind):
            self._kind = kind

        def execute(self):
            calls[self._kind] = calls.get(self._kind, 0) + 1
            raise _APIError(_RESTRICTED)

    class _Tbl:
        def upsert(self, *a, **k):
            return _Exec("upsert")

        def select(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return _Exec("select")

    class _Client:
        def table(self, *a, **k):
            return _Tbl()

    return _Client()


def _records(n: int):
    return [
        {
            "Plataforma": "Magalu",
            "Keyword Buscada": "ar condicionado 12000 btus inverter",
            "Data": "2026-07-17",
            "Turno": "Abertura",
            "Produto / SKU": f"Ar Condicionado Midea Inverter 12000 BTU {i}",
            "Marca Monitorada": "Midea",
            "Preço (R$)": "1999.90",
        }
        for i in range(n)
    ]


class TestUploadFailFast:
    def test_aborta_no_primeiro_lote_e_retorna_false(self, monkeypatch):
        calls: dict = {}
        monkeypatch.setattr(sc, "_get_client", lambda: _fake_client_raising(calls))

        # 1200 registros → 3 lotes de 500. Sem fail-fast seriam 3 tentativas.
        ok = upload_to_supabase(_records(1200), run_id="test-quota")

        assert ok is False
        # Abortou no 1º lote — apenas 1 upsert, não 3.
        assert calls.get("upsert") == 1


class TestUploadPartialThenQuota:
    """1º lote grava (com duplicatas), 2º estoura a cota: o resumo tem que
    refletir `sent` real e as duplicatas acumuladas — não zerar tudo."""

    def test_reporta_sent_e_duplicatas_reais(self, monkeypatch):
        state = {"n": 0}

        class _Result:
            def __init__(self, data):
                self.data = data

        class _Exec:
            def execute(self):
                state["n"] += 1
                if state["n"] == 1:
                    # 500 do lote → 480 inseridas, 20 já existiam.
                    return _Result([{}] * 480)
                raise _APIError(_RESTRICTED)  # 2º lote: cota estourou

        class _Tbl:
            def upsert(self, *a, **k):
                return _Exec()

        class _Client:
            def table(self, *a, **k):
                return _Tbl()

        monkeypatch.setattr(sc, "_get_client", lambda: _Client())

        msgs: list = []
        sink = logger.add(lambda m: msgs.append(str(m)), level="INFO")
        try:
            ok = upload_to_supabase(_records(1200), run_id="test-partial")
        finally:
            logger.remove(sink)

        assert ok is False
        blob = "".join(msgs)
        # Resumo reconcilia: 480 inseridas, 20 já existiam (não zeradas).
        assert "Inseridas=480" in blob
        assert "Já existiam=20" in blob
        # Mensagem de remediação usa o `sent` real, não "0".
        assert "480 de 1200 registros gravados" in blob


class TestAdminAutomationSkip:
    def test_pula_pipeline_inteira_com_skip_reason(self, monkeypatch):
        calls: dict = {}
        client = _fake_client_raising(calls)

        # Se qualquer etapa rodar, falha o teste (não deveriam ser chamadas).
        for name in list(admin._STEP_FUNCS):
            monkeypatch.setitem(
                admin._STEP_FUNCS,
                name,
                lambda *a, **k: pytest.fail("etapa não deveria rodar com banco restrito"),
            )
        # Não escreve no JSONL local durante o teste.
        persisted: list = []
        monkeypatch.setattr(admin, "_persist_run", lambda c, r: persisted.append((c, r)))

        report = admin.run_admin_automation(
            trigger="pos_coleta", client=client, notify=False
        )

        assert report["status"] == "skipped"
        assert report["skip_reason"] == "quota_restricted"
        assert report["steps"] == []
        # Só o probe barato tocou o banco.
        assert calls.get("select") == 1
        # Skip foi auditado localmente (client=None → não reinsere no banco restrito).
        assert persisted and persisted[-1][0] is None
