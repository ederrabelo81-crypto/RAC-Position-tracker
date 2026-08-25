"""
tests/test_supabase_network_guard.py — Guarda contra falha de REDE/DNS no
upload para o Supabase (ex.: "[Errno 11001] getaddrinfo failed" no Windows).

Log real que motivou este guard: a coleta local ficou sem DNS por alguns
segundos e todos os 19 lotes do upload falharam com o mesmo erro, seguidos
por mais 5 falhas idênticas nas etapas da Automação ADMIN — 24 avisos para
um único problema (sem internet no computador no momento do upload).

Este guard: (1) dá a um DNS que caiu por um instante a chance de voltar
sozinho (retentativa com backoff, como o Drive já faz); (2) se persistir,
aborta o restante com UMA mensagem clara em vez de repetir a mesma falha.

Sem rede/Supabase — clients falsos que levantam o erro real de socket/DNS.
"""
import pytest
from loguru import logger

import utils.admin_automation as admin
import utils.supabase_client as sc
from utils.supabase_client import is_network_error, upload_to_supabase


# Erro real do log que motivou este guard (Windows, DNS indisponível).
_DNS_ERROR = Exception(
    "HTTPSConnectionPool(host='ailbsczkrympslpjwwko.supabase.co', port=443): "
    "Max retries exceeded with url: /rest/v1/coletas "
    "(Caused by NameResolutionError(\"Failed to resolve "
    "'ailbsczkrympslpjwwko.supabase.co' ([Errno 11001] getaddrinfo failed)\"))"
)


class TestIsNetworkError:
    def test_detecta_getaddrinfo_failed_windows(self):
        assert is_network_error(_DNS_ERROR) is True

    def test_detecta_name_or_service_not_known_linux(self):
        exc = Exception("[Errno -2] Name or service not known")
        assert is_network_error(exc) is True

    def test_detecta_connection_refused(self):
        assert is_network_error(Exception("Connection refused")) is True

    def test_detecta_network_is_unreachable(self):
        assert is_network_error(Exception("Network is unreachable")) is True

    def test_ignora_timeout(self):
        # Timeout é o banco lento/sobrecarregado, não falta de rede — não deve
        # disparar a mesma remediação ("sem internet").
        assert is_network_error(Exception("read timed out")) is False

    def test_ignora_erro_de_coluna_ausente(self):
        exc = Exception("{'code':'PGRST204','message':'column x could not be found'}")
        assert is_network_error(exc) is False

    def test_ignora_cota_excedida(self):
        exc = Exception("exceed_db_size_quota")
        assert is_network_error(exc) is False


def _records(n: int):
    return [
        {
            "Plataforma": "Magalu",
            "Keyword Buscada": "ar condicionado 12000 btus inverter",
            "Data": "2026-08-25",
            "Turno": "Abertura",
            "Produto / SKU": f"Ar Condicionado Midea Inverter 12000 BTU {i}",
            "Marca Monitorada": "Midea",
            "Preço (R$)": "1999.90",
        }
        for i in range(n)
    ]


def _fake_client_raising(calls, exc_factory=lambda: _DNS_ERROR):
    """Client falso cuja chamada (upsert OU select/limit do probe) sempre
    levanta o erro de rede — conta as tentativas em calls["n"]."""

    class _Exec:
        def execute(self):
            calls["n"] = calls.get("n", 0) + 1
            raise exc_factory()

    class _Tbl:
        def upsert(self, *a, **k):
            return _Exec()

        def select(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return _Exec()

    class _Client:
        def table(self, *a, **k):
            return _Tbl()

    return _Client()


class TestUploadRetryEntaoDesiste:
    def test_retenta_3x_por_lote_antes_de_desistir(self, monkeypatch):
        monkeypatch.setattr("utils.supabase_client.time.sleep", lambda _s: None)
        calls: dict = {}
        monkeypatch.setattr(sc, "_get_client", lambda: _fake_client_raising(calls))

        ok = upload_to_supabase(_records(10), run_id="test-network")

        assert ok is False
        # 3 tentativas no único lote — não desiste na primeira falha de rede.
        assert calls.get("n") == 3

    def test_aborta_lotes_seguintes_apos_esgotar_retentativas(self, monkeypatch):
        monkeypatch.setattr("utils.supabase_client.time.sleep", lambda _s: None)
        calls: dict = {}
        monkeypatch.setattr(sc, "_get_client", lambda: _fake_client_raising(calls))

        # 1200 registros → 3 lotes de 500. Sem fail-fast seriam 9 tentativas
        # (3 por lote); com fail-fast, só o 1º lote é tentado (e retentado).
        ok = upload_to_supabase(_records(1200), run_id="test-network-abort")

        assert ok is False
        assert calls.get("n") == 3

    def test_recupera_se_a_rede_volta_antes_de_esgotar(self, monkeypatch):
        monkeypatch.setattr("utils.supabase_client.time.sleep", lambda _s: None)
        state = {"n": 0}

        class _Result:
            def __init__(self, data):
                self.data = data

        class _Exec:
            def execute(self):
                state["n"] += 1
                if state["n"] < 3:
                    raise _DNS_ERROR
                return _Result([{}] * 10)  # 3ª tentativa: rede voltou

        class _Tbl:
            def upsert(self, *a, **k):
                return _Exec()

        class _Client:
            def table(self, *a, **k):
                return _Tbl()

        monkeypatch.setattr(sc, "_get_client", lambda: _Client())

        ok = upload_to_supabase(_records(10), run_id="test-network-recovers")

        assert ok is True
        assert state["n"] == 3


class TestUploadMensagemDeRemediacao:
    def test_mensagem_nao_confunde_com_erro_de_credencial(self, monkeypatch):
        monkeypatch.setattr("utils.supabase_client.time.sleep", lambda _s: None)
        calls: dict = {}
        monkeypatch.setattr(sc, "_get_client", lambda: _fake_client_raising(calls))

        msgs: list = []
        sink = logger.add(lambda m: msgs.append(str(m)), level="INFO")
        try:
            ok = upload_to_supabase(_records(10), run_id="test-network-msg")
        finally:
            logger.remove(sink)

        assert ok is False
        blob = "".join(msgs)
        assert "REDE/DNS" in blob
        assert "Não é problema de credencial" in blob
        assert "python scripts/upload_csv.py" in blob


class TestAdminAutomationSkipPorRede:
    def test_pula_pipeline_inteira_com_skip_reason(self, monkeypatch):
        calls: dict = {}
        client = _fake_client_raising(calls)

        # Se qualquer etapa rodar, falha o teste (não deveriam ser chamadas).
        for name in list(admin._STEP_FUNCS):
            monkeypatch.setitem(
                admin._STEP_FUNCS,
                name,
                lambda *a, **k: pytest.fail("etapa não deveria rodar sem rede"),
            )
        persisted: list = []
        monkeypatch.setattr(admin, "_persist_run", lambda c, r: persisted.append((c, r)))

        report = admin.run_admin_automation(
            trigger="pos_coleta", client=client, notify=False
        )

        assert report["status"] == "skipped"
        assert report["skip_reason"] == "network_unreachable"
        assert report["steps"] == []
        # Só o probe barato tocou o banco.
        assert calls.get("n") == 1
        assert persisted and persisted[-1][0] is None
