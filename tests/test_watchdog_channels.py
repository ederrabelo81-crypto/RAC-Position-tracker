"""
tests/test_watchdog_channels.py — canal offline × plataforma quebrada.

Regressão de 10/08/2026: o watchdog acumulou 16 execuções seguidas vermelhas.
Não estava quebrado — ele saía com exit 1 sempre que uma plataforma crítica
vinha zerada, e ML/Magalu/Shopee/Casas Bahia rodam no PC local, que fica
desligado nos fins de semana. Alerta que é vermelho todo dia deixa de alertar.

A regra coberta aqui: se TODAS as plataformas de um canal vieram zeradas, o
diagnóstico é "canal offline" (WARN, não conta no exit code). Se ao menos uma
coletou, a máquina rodou — e quem veio zerada quebrou de verdade (FAIL crítico).

Rode: pytest tests/test_watchdog_channels.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.daily_status_check import (  # noqa: E402
    CHANNEL_ACTIONS,
    CHANNEL_LOCAL,
    _build_report,
    _format_telegram,
    _offline_channels,
)

TODAS = [
    "Mercado Livre", "Amazon", "Magalu",
    "Leroy Merlin", "Casas Bahia", "Shopee", "Google Shopping",
]


def _counts(turno: str, **por_plataforma: int) -> dict:
    return {(plat, turno): n for plat, n in por_plataforma.items()}


class TestOfflineChannels:
    def test_pc_local_desligado_e_detectado(self):
        """Fim de semana real: só Actions coletou."""
        counts = _counts(
            "Abertura",
            **{"Amazon": 2351, "Leroy Merlin": 1068, "Google Shopping": 0},
        )
        offline = _offline_channels(counts, "Abertura", TODAS)
        assert offline == {CHANNEL_LOCAL}

    def test_tudo_coletando_nenhum_canal_offline(self):
        counts = _counts(
            "Abertura",
            **{"Amazon": 2000, "Leroy Merlin": 1000, "Google Shopping": 300,
               "Mercado Livre": 3000, "Magalu": 1800,
               "Shopee": 2400, "Casas Bahia": 900},
        )
        assert _offline_channels(counts, "Abertura", TODAS) == set()

    def test_uma_plataforma_viva_prova_canal_de_pe(self):
        """Google Shopping zerado com Amazon/Leroy OK = scraper quebrado."""
        counts = _counts(
            "Abertura",
            **{"Amazon": 2000, "Leroy Merlin": 1000, "Google Shopping": 0,
               "Mercado Livre": 3000, "Magalu": 1800,
               "Shopee": 2400, "Casas Bahia": 900},
        )
        assert CHANNEL_ACTIONS not in _offline_channels(counts, "Abertura", TODAS)

    def test_apagao_total_nao_e_canal_offline(self):
        """
        Nenhum canal é "offline" quando TODOS vieram zerados.

        Esta asserção já esteve invertida aqui: eu tinha escrito que o dia sem
        nenhuma coleta marcava os dois canais como offline. O efeito era que
        toda falha crítica virava WARN e o watchdog saía com exit 0 — ou seja,
        o alerta silenciava exatamente no caso mais grave, um apagão total de
        coleta ou de upload. "Máquina desligada" só é diagnóstico plausível
        quando ALGUM canal produziu dado; sem isso, o que houve foi apagão.
        """
        assert _offline_channels({}, "Abertura", TODAS) == set()

    def test_apagao_total_mantem_falha_critica_no_exit_code(self):
        rows, summary = _build_report("2026-08-10", "Abertura", {})
        assert summary["critical_fail"] > 0, (
            "apagão total precisa gritar — é o caso mais grave, não o mais quieto"
        )


class TestBuildReportExitCode:
    def test_canal_offline_nao_gera_falha_critica(self):
        """O caso do fim de semana: nada quebrou, exit code deve ficar limpo."""
        counts = _counts(
            "Abertura",
            **{"Amazon": 2351, "Leroy Merlin": 1068, "Google Shopping": 300},
        )
        rows, summary = _build_report("2026-08-09", "Abertura", counts)
        assert summary["critical_fail"] == 0
        assert summary["offline_names"] == [CHANNEL_LOCAL]

        ml = next(r for r in rows if r["platform"] == "Mercado Livre")
        assert ml["status"] == "WARN"
        assert ml["critical"] is False
        assert "canal offline" in ml["desc"]

    def test_plataforma_quebrada_com_canal_de_pe_continua_critica(self):
        """Magalu zerado enquanto ML e Shopee coletam = quebra real."""
        counts = _counts(
            "Abertura",
            **{"Amazon": 2000, "Leroy Merlin": 1000, "Google Shopping": 300,
               "Mercado Livre": 3000, "Magalu": 0,
               "Shopee": 2400, "Casas Bahia": 900},
        )
        rows, summary = _build_report("2026-08-10", "Abertura", counts)
        assert summary["critical_fail"] >= 1
        assert summary["offline_names"] == []

        magalu = next(r for r in rows if r["platform"] == "Magalu")
        assert magalu["status"] == "FAIL"
        assert magalu["critical"] is True


class TestTelegramAgrupaCanalOffline:
    """
    Um canal offline é UM fato, não N plataformas quietas.

    O alerta tinha virado ruído — 16 runs vermelhos seguidos. Rebaixar o
    status sem agrupar a mensagem trocaria "N linhas vermelhas" por "N linhas
    amarelas", o que não resolve o problema que motivou a mudança.
    """

    def _msg(self):
        counts = _counts(
            "Abertura",
            **{"Amazon": 2351, "Leroy Merlin": 1068, "Google Shopping": 300},
        )
        rows, summary = _build_report("2026-08-09", "Abertura", counts)
        return _format_telegram("2026-08-09", "Abertura", rows, summary)

    def test_uma_linha_por_canal(self):
        msg = self._msg()
        assert msg.count("Canal offline") == 1

    def test_nomeia_o_canal_e_conta_as_plataformas(self):
        msg = self._msg()
        assert CHANNEL_LOCAL in msg
        assert "plataforma(s) sem coleta" in msg

    def test_plataformas_do_canal_nao_ganham_linha_propria(self):
        msg = self._msg()
        for plataforma in ("Mercado Livre", "Magalu", "Shopee"):
            assert f"<code>{plataforma}</code>: 0 reg" not in msg

    def test_plataformas_que_coletaram_seguem_visiveis(self):
        msg = self._msg()
        assert "Amazon" in msg and "2351" in msg
