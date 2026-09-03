"""
Diário de exports: o que impede um Ctrl+C de virar zumbi que trava o import.

A API do PriceTrack permite 3 exports concorrentes por organização e **não
oferece cancelamento**. Enquanto o id do export vivia só na memória do
processo, interromper o import largava um export rodando que ninguém mais
conhecia — ele segurava um slot, a execução seguinte criava outro para a mesma
data e tomava 429. Duas ou três interrupções travavam todo import posterior no
loop "aguardando slot", que foi o sintoma relatado em 03/09/2026 no backfill de
36 dias (2026-07-28 → 2026-09-01).

Estes testes prendem o comportamento que quebra o ciclo:
adotar em vez de duplicar, dizer quem segura o slot, e dar sinal de vida.
"""
from __future__ import annotations

import gzip
import json
from contextlib import contextmanager
from typing import Dict, List

import pytest
from loguru import logger

from pricetrack_api.client import PriceTrackClient
from pricetrack_api.exports import (
    OUTCOME_OK,
    OUTCOME_TIMEOUT,
    ExportManager,
)
from pricetrack_api.http import HttpTransport
from pricetrack_api.journal import ExportJournal, journal_key
from pricetrack_api.models import ExportRequest

from .conftest import FakeClock, FakeResponse, FakeSession, offer_payload

NDJSON_BODY = gzip.compress(
    ("\n".join(json.dumps(offer_payload(oid=f"of-{i}")) for i in range(3)) + "\n")
    .encode("utf-8")
)


class RelogioParede:
    """Relógio de parede controlável para o diário (epoch em segundos)."""

    def __init__(self, agora: float = 1_700_000_000.0):
        self.agora = agora

    def __call__(self) -> float:
        return self.agora

    def avanca(self, segundos: float) -> None:
        self.agora += segundos


def _status(export_id: str, status: str, progress: float = 0) -> Dict:
    payload = {
        "exportId": export_id, "status": status, "progress": progress,
        "statusUrl": f"/exports-external/{export_id}",
    }
    if status == "DONE":
        payload.update({
            "format": "ndjson.gz", "rowCount": 3,
            "fileSizeBytes": len(NDJSON_BODY), "progress": 100,
            "downloadUrl": f"https://s3.example/{export_id}",
        })
    return payload


def _manager(settings, session, clock: FakeClock, **kwargs) -> ExportManager:
    transport = HttpTransport(settings, session=session,
                              sleep_fn=clock.sleep, rng=lambda: 1.0)
    client = PriceTrackClient(settings, transport=transport, clock=clock)
    return ExportManager(client, sleep_fn=clock.sleep, clock=clock, **kwargs)


@contextmanager
def captura_log(level: str = "INFO"):
    """Coleta as mensagens do loguru emitidas dentro do bloco."""
    linhas: List[str] = []
    sink = logger.add(lambda m: linhas.append(str(m)), level=level,
                      format="{message}")
    try:
        yield linhas
    finally:
        logger.remove(sink)


def _posts(session: FakeSession) -> List:
    return [c for c in session.calls if c.method == "POST"]


def _listagens(session: FakeSession) -> List:
    return [c for c in session.calls
            if c.method == "GET" and c.url.endswith("/exports-external")]


# ── O diário em si ───────────────────────────────────────────────────────────


class TestExportJournal:
    def test_grava_e_recupera(self, tmp_path):
        diario = ExportJournal(tmp_path / "j.json")
        pedido = ExportRequest("2026-07-28")
        assert diario.get("offers", pedido) is None

        diario.record("offers", pedido, "exp-1")
        entrada = diario.get("offers", pedido)
        assert entrada is not None
        assert entrada.export_id == "exp-1"
        assert entrada.collection_date == "2026-07-28"

    def test_esquece(self, tmp_path):
        diario = ExportJournal(tmp_path / "j.json")
        pedido = ExportRequest("2026-07-28")
        diario.record("offers", pedido, "exp-1")
        diario.forget("exp-1")
        assert diario.get("offers", pedido) is None

    def test_dataset_e_filtros_nao_se_confundem(self, tmp_path):
        """Export filtrado não pode adotar o export cheio do mesmo dia: o
        arquivo teria menos linhas que o pedido, em silêncio."""
        cheio = ExportRequest("2026-07-28")
        filtrado = ExportRequest("2026-07-28", marketplaces=["AMAZON"])
        assert journal_key("offers", cheio) != journal_key("offers", filtrado)
        assert journal_key("offers", cheio) != journal_key("shipping", cheio)

        diario = ExportJournal(tmp_path / "j.json")
        diario.record("offers", cheio, "exp-cheio")
        assert diario.get("offers", filtrado) is None
        assert diario.get("shipping", cheio) is None

    def test_entrada_velha_e_ignorada(self, tmp_path):
        relogio = RelogioParede()
        diario = ExportJournal(tmp_path / "j.json", clock=relogio,
                               max_age_seconds=3600)
        pedido = ExportRequest("2026-07-28")
        diario.record("offers", pedido, "exp-1")
        relogio.avanca(3601)
        assert diario.get("offers", pedido) is None

    def test_arquivo_corrompido_nao_derruba(self, tmp_path):
        caminho = tmp_path / "j.json"
        caminho.write_text("{isso não é json", encoding="utf-8")
        diario = ExportJournal(caminho)
        assert diario.get("offers", ExportRequest("2026-07-28")) is None
        assert diario.entries() == []

    def test_diretorio_inexistente_e_criado(self, tmp_path):
        diario = ExportJournal(tmp_path / "fundo" / "do" / "poco" / "j.json")
        diario.record("offers", ExportRequest("2026-07-28"), "exp-1")
        assert diario.get("offers", ExportRequest("2026-07-28")) is not None

    def test_idade_em_segundos(self, tmp_path):
        relogio = RelogioParede()
        diario = ExportJournal(tmp_path / "j.json", clock=relogio)
        diario.record("offers", ExportRequest("2026-07-28"), "exp-1")
        relogio.avanca(600)
        entrada = diario.get("offers", ExportRequest("2026-07-28"))
        assert diario.age_of(entrada) == pytest.approx(600)


# ── Gravação no momento certo ────────────────────────────────────────────────


class TestGravaAntesDeEsperar:
    def test_ctrl_c_apos_o_post_deixa_o_id_gravado(self, settings, clock, tmp_path):
        """O caso que gerava zumbi: o processo morre logo depois do POST."""
        def handler(call):
            if call.method == "POST":
                return FakeResponse(json_data=_status("exp-zumbi", "pending"))
            raise KeyboardInterrupt()          # operador cansou de esperar

        diario = ExportJournal(tmp_path / "j.json")
        manager = _manager(settings, FakeSession(handler=handler), clock,
                           journal=diario)
        with pytest.raises(KeyboardInterrupt):
            manager.run_many([ExportRequest("2026-07-28")],
                             dest_fn=lambda r: tmp_path / "x.gz")

        entrada = diario.get("offers", ExportRequest("2026-07-28"))
        assert entrada is not None and entrada.export_id == "exp-zumbi"

    def test_sucesso_limpa_a_entrada(self, settings, clock, tmp_path):
        session = FakeSession(responses=[
            FakeResponse(json_data=_status("exp-1", "pending")),
            FakeResponse(json_data=_status("exp-1", "DONE")),
            FakeResponse(content=NDJSON_BODY),
        ])
        diario = ExportJournal(tmp_path / "j.json")
        manager = _manager(settings, session, clock, journal=diario)
        outcome = manager.run(ExportRequest("2026-07-28"), dest=tmp_path / "x.gz")
        assert outcome.ok
        assert diario.entries() == []

    def test_download_quebrado_preserva_a_entrada(self, settings, clock, tmp_path):
        """Export DONE + rede caindo no download: a próxima execução baixa o
        MESMO export, em vez de pagar outro só por causa da rede."""
        session = FakeSession(responses=[
            FakeResponse(json_data=_status("exp-1", "pending")),
            FakeResponse(json_data=_status("exp-1", "DONE")),
            FakeResponse(status_code=500, content=b"boom"),
            FakeResponse(status_code=500, content=b"boom"),
            FakeResponse(status_code=500, content=b"boom"),
        ])
        diario = ExportJournal(tmp_path / "j.json")
        manager = _manager(settings, session, clock, journal=diario)
        outcomes = manager.run_many([ExportRequest("2026-07-28")],
                                    dest_fn=lambda r: tmp_path / "x.gz")
        assert not outcomes[0].ok
        assert [e.export_id for e in diario.entries()] == ["exp-1"]


# ── Adoção ───────────────────────────────────────────────────────────────────


class TestAdocao:
    def test_adota_export_ainda_rodando(self, settings, clock, tmp_path):
        """Nenhum POST: o export da execução anterior é retomado onde parou."""
        polls = {"n": 0}

        def handler(call):
            if call.method == "POST":
                pytest.fail("não deveria criar export: já havia um em voo")
            if call.stream:
                return FakeResponse(content=NDJSON_BODY)
            polls["n"] += 1
            estado = "DONE" if polls["n"] >= 2 else "processing"
            return FakeResponse(json_data=_status("exp-orfao", estado, 40))

        diario = ExportJournal(tmp_path / "j.json")
        diario.record("offers", ExportRequest("2026-07-28"), "exp-orfao")

        session = FakeSession(handler=handler)
        manager = _manager(settings, session, clock, journal=diario)
        dest = tmp_path / "2026-07-28.gz"
        outcomes = manager.run_many([ExportRequest("2026-07-28")],
                                    dest_fn=lambda r: dest)

        assert outcomes[0].status == OUTCOME_OK
        assert outcomes[0].adopted is True
        assert dest.exists()
        assert _posts(session) == []
        assert diario.entries() == []

    def test_adota_export_ja_pronto_sem_gastar_slot(self, settings, clock, tmp_path):
        """DONE de ontem = só baixar. Zero POST e zero espera de polling."""
        def handler(call):
            if call.method == "POST":
                pytest.fail("export já estava DONE — não há o que criar")
            if call.stream:
                return FakeResponse(content=NDJSON_BODY)
            return FakeResponse(json_data=_status("exp-pronto", "DONE"))

        diario = ExportJournal(tmp_path / "j.json")
        diario.record("offers", ExportRequest("2026-07-28"), "exp-pronto")

        session = FakeSession(handler=handler)
        manager = _manager(settings, session, clock, journal=diario)
        dest = tmp_path / "pronto.gz"
        outcomes = manager.run_many([ExportRequest("2026-07-28")],
                                    dest_fn=lambda r: dest)

        assert outcomes[0].status == OUTCOME_OK and dest.exists()
        assert clock.sleeps == [], "não deveria dormir por um export já pronto"

    def test_so_a_data_certa_e_adotada(self, settings, clock, tmp_path):
        """Diário com 2026-07-28; o pedido é de 2026-07-29 → cria export novo."""
        def handler(call):
            if call.method == "POST":
                return FakeResponse(json_data=_status("exp-novo", "pending"))
            if call.stream:
                return FakeResponse(content=NDJSON_BODY)
            return FakeResponse(json_data=_status("exp-novo", "DONE"))

        diario = ExportJournal(tmp_path / "j.json")
        diario.record("offers", ExportRequest("2026-07-28"), "exp-orfao")

        session = FakeSession(handler=handler)
        manager = _manager(settings, session, clock, journal=diario)
        outcomes = manager.run_many([ExportRequest("2026-07-29")],
                                    dest_fn=lambda r: tmp_path / "x.gz")

        assert outcomes[0].status == OUTCOME_OK
        assert len(_posts(session)) == 1
        # a entrada do 28 continua lá para a execução que pedir aquele dia
        assert [e.collection_date for e in diario.entries()] == ["2026-07-28"]

    def test_export_travado_ha_horas_e_descartado(self, settings, clock, tmp_path):
        """Zumbi mais velho que a vida máxima de um export: não se espera por
        ele — mas o log diz que é provavelmente ele no 429."""
        def handler(call):
            if call.method == "POST":
                return FakeResponse(json_data=_status("exp-novo", "pending"))
            if call.stream:
                return FakeResponse(content=NDJSON_BODY)
            return FakeResponse(json_data=_status("exp-novo", "DONE"))

        relogio = RelogioParede()
        diario = ExportJournal(tmp_path / "j.json", clock=relogio)
        diario.record("offers", ExportRequest("2026-07-28"), "exp-travado")
        relogio.avanca(settings.poll_timeout_seconds + 60)

        session = FakeSession(handler=handler)
        manager = _manager(settings, session, clock, journal=diario)
        with captura_log("WARNING") as linhas:
            outcomes = manager.run_many([ExportRequest("2026-07-28")],
                                        dest_fn=lambda r: tmp_path / "x.gz")

        assert outcomes[0].status == OUTCOME_OK
        assert len(_posts(session)) == 1, "devia criar um export novo"
        assert any("exp-travado" in l for l in linhas)
        # o zumbi sai do diário; quem manda agora é o export novo
        assert [e.export_id for e in diario.entries()] == []

    def test_export_anterior_failed_vira_export_novo(self, settings, clock,
                                                     tmp_path):
        def handler(call):
            if call.method == "POST":
                return FakeResponse(json_data=_status("exp-novo", "pending"))
            if call.stream:
                return FakeResponse(content=NDJSON_BODY)
            eid = call.url.rsplit("/", 1)[-1]
            if eid == "exp-ruim":
                return FakeResponse(json_data=_status("exp-ruim", "FAILED"))
            return FakeResponse(json_data=_status("exp-novo", "DONE"))

        diario = ExportJournal(tmp_path / "j.json")
        diario.record("offers", ExportRequest("2026-07-28"), "exp-ruim")

        session = FakeSession(handler=handler)
        manager = _manager(settings, session, clock, journal=diario)
        outcomes = manager.run_many([ExportRequest("2026-07-28")],
                                    dest_fn=lambda r: tmp_path / "x.gz")
        assert outcomes[0].status == OUTCOME_OK
        assert len(_posts(session)) == 1

    def test_status_desconhecido_nao_prende_o_polling(self, settings, clock,
                                                      tmp_path):
        """Estado que este cliente não conhece (ex.: CANCELED) não é adotado:
        esperar por ele gastaria o polling inteiro até o timeout, para nada."""
        def handler(call):
            if call.method == "POST":
                return FakeResponse(json_data=_status("exp-novo", "pending"))
            if call.stream:
                return FakeResponse(content=NDJSON_BODY)
            eid = call.url.rsplit("/", 1)[-1]
            if eid == "exp-estranho":
                return FakeResponse(json_data=_status("exp-estranho", "CANCELED"))
            return FakeResponse(json_data=_status("exp-novo", "DONE"))

        diario = ExportJournal(tmp_path / "j.json")
        diario.record("offers", ExportRequest("2026-07-28"), "exp-estranho")

        session = FakeSession(handler=handler)
        manager = _manager(settings, session, clock, journal=diario)
        outcomes = manager.run_many([ExportRequest("2026-07-28")],
                                    dest_fn=lambda r: tmp_path / "x.gz")
        assert outcomes[0].status == OUTCOME_OK
        assert len(_posts(session)) == 1

    def test_export_sumido_da_api_vira_export_novo(self, settings, clock,
                                                   tmp_path):
        """404 no id do diário (export expirou do lado da API)."""
        def handler(call):
            if call.method == "POST":
                return FakeResponse(json_data=_status("exp-novo", "pending"))
            if call.stream:
                return FakeResponse(content=NDJSON_BODY)
            eid = call.url.rsplit("/", 1)[-1]
            if eid == "exp-sumido":
                return FakeResponse(status_code=404, json_data={"message": "nope"})
            return FakeResponse(json_data=_status("exp-novo", "DONE"))

        diario = ExportJournal(tmp_path / "j.json")
        diario.record("offers", ExportRequest("2026-07-28"), "exp-sumido")

        session = FakeSession(handler=handler)
        manager = _manager(settings, session, clock, journal=diario)
        outcomes = manager.run_many([ExportRequest("2026-07-28")],
                                    dest_fn=lambda r: tmp_path / "x.gz")
        assert outcomes[0].status == OUTCOME_OK
        assert len(_posts(session)) == 1


# ── Visibilidade do 429 ──────────────────────────────────────────────────────


class TestDiagnosticoDo429:
    def test_censo_nomeia_quem_segura_os_slots(self, settings, clock, tmp_path):
        """Preso em 429 por tempo demais: o manager lista os exports ativos da
        organização e diz de quem é cada slot, em vez de repetir 'aguardando'."""
        settings.poll_interval_seconds = 60.0
        settings.poll_timeout_seconds = 600.0

        def handler(call):
            if call.method == "POST":
                return FakeResponse(status_code=429, headers={"Retry-After": "60"},
                                    json_data={"message": "limit"})
            if call.url.endswith("/exports-external"):
                return FakeResponse(json_data={
                    "data": [
                        {"exportId": "exp-a", "status": "PROCESSING", "progress": 0},
                        {"exportId": "exp-b", "status": "PENDING", "progress": 0},
                        {"exportId": "exp-c", "status": "PROCESSING", "progress": 12},
                    ],
                    "meta": {"hasNextPage": False},
                })
            return FakeResponse(json_data=_status("x", "pending"))

        diario = ExportJournal(tmp_path / "j.json")
        # exp-b é órfão nosso, de uma execução anterior interrompida
        diario.record("offers", ExportRequest("2026-07-01"), "exp-b")

        session = FakeSession(handler=handler)
        manager = _manager(settings, session, clock, journal=diario)
        with captura_log("WARNING") as linhas:
            outcomes = manager.run_many([ExportRequest("2026-07-28")],
                                        dest_fn=lambda r: tmp_path / "x.gz")

        assert outcomes[0].status == OUTCOME_TIMEOUT
        assert _listagens(session), "o censo deveria ter listado os exports"
        censo = "\n".join(l for l in linhas if "slot(s) de export ocupados" in l)
        assert censo, "faltou a linha do censo"
        assert "exp-a" in censo and "exp-c" in censo
        assert "de fora deste import" in censo
        assert "órfão deste projeto (2026-07-01)" in censo

    def test_429_curto_nao_gasta_listagem(self, settings, clock, tmp_path):
        """429 de segundos é disputa normal por slot — não vale um censo."""
        estado = {"posts": 0}

        def handler(call):
            if call.method == "POST":
                estado["posts"] += 1
                if estado["posts"] == 1:
                    return FakeResponse(status_code=429,
                                        headers={"Retry-After": "3"},
                                        json_data={"message": "limit"})
                return FakeResponse(json_data=_status("exp-1", "pending"))
            if call.stream:
                return FakeResponse(content=NDJSON_BODY)
            return FakeResponse(json_data=_status("exp-1", "DONE"))

        session = FakeSession(handler=handler)
        manager = _manager(settings, session, clock,
                           journal=ExportJournal(tmp_path / "j.json"))
        outcome = manager.run(ExportRequest("2026-07-28"), dest=tmp_path / "x.gz")
        assert outcome.ok
        assert _listagens(session) == []

    def test_job_em_voo_da_sinal_de_vida_em_info(self, settings, clock, tmp_path):
        """Sem heartbeat o console só mostrava o 429 e a execução parecia
        travada — foi assim que runs saudáveis foram mortos no meio."""
        polls = {"n": 0}

        def handler(call):
            if call.method == "POST":
                return FakeResponse(json_data=_status("exp-1", "pending"))
            if call.stream:
                return FakeResponse(content=NDJSON_BODY)
            polls["n"] += 1
            estado = "DONE" if polls["n"] >= 3 else "processing"
            return FakeResponse(json_data=_status("exp-1", estado, 30 * polls["n"]))

        session = FakeSession(handler=handler)
        manager = _manager(settings, session, clock,
                           journal=ExportJournal(tmp_path / "j.json"))
        with captura_log("INFO") as linhas:
            outcome = manager.run(ExportRequest("2026-07-28"),
                                  dest=tmp_path / "x.gz")
        assert outcome.ok
        vivos = [l for l in linhas if "em voo" in l and "exp-1" in l]
        assert vivos, "o job em voo precisa aparecer em INFO"
