"""
tests/test_volume_drop.py — Queda brusca de volume por plataforma.

O caso-mãe é o Mercado Livre de 09–14/10/2026: ~5.000 linhas por turno, depois
0–136 durante cinco dias, sem que nada alarmasse, porque a contagem quase nunca
chegava a zero. Tudo aqui é puro: as batidas de ponto são dicts montados à mão,
no mesmo formato que `main.py::_bater_ponto` grava em `pipeline_heartbeat`.

Rode: pytest tests/test_volume_drop.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.pipeline_registry import JOBS_POR_ID, MARKETPLACES  # noqa: E402
from utils.volume_drop import (  # noqa: E402
    JobDeColeta,
    Observacao,
    classificar_quedas,
    ler_detail,
    linhas_por_dono,
    observacoes_de_batidas,
)

TURNOS = ("Abertura", "Tarde", "Fechamento")
JOB_DO_TURNO = {"Abertura": "local_manha", "Tarde": "local_tarde", "Fechamento": "local_noite"}

#: Os jobs reais do registro: se alguém mudar o dono de uma plataforma, os
#: testes acompanham em vez de validar uma cópia desatualizada.
JOBS = [
    JobDeColeta(j.id, j.turno, tuple(j.plataformas))
    for j in (JOBS_POR_ID[i] for i in JOB_DO_TURNO.values())
]

NORMAL = {
    "Mercado Livre": 5000,
    "Magalu": 1800,
    "Casas Bahia": 2200,
    "Google Shopping": 900,
    "Leroy Merlin": 700,
    "Shopee": 400,
    "WebContinental": 300,
    "Frigelar": 250,
}


def _detail(contagens: Dict[str, int], extra: Optional[str] = None) -> str:
    """Reproduz o formato de `_bater_ponto` (+ a linha de diagnóstico do PR #400)."""
    com_dado = {k: v for k, v in contagens.items() if v}
    partes = [", ".join(f"{k}={v}" for k, v in sorted(com_dado.items()))]
    zeradas = [k for k, v in contagens.items() if not v and k in MARKETPLACES]
    if zeradas:
        partes.append("ZERADAS: " + ", ".join(zeradas))
    if extra:
        partes.append(extra)
    return " | ".join(partes)


class _Livro:
    """Livro-razão em memória."""

    def __init__(self) -> None:
        self.batidas: List[dict] = []

    def bater(
        self,
        dia: date,
        turno: str,
        contagens: Dict[str, int],
        status: str = "SUCCESS",
        extra: Optional[str] = None,
    ) -> None:
        self.batidas.append({
            "id": len(self.batidas) + 1,
            "job_id": JOB_DO_TURNO[turno],
            "data_ref": dia.isoformat(),
            "status": status,
            "rows_written": sum(contagens.values()),
            "detail": _detail(contagens, extra),
        })

    def quedas(self, ate: date):
        obs = observacoes_de_batidas(self.batidas, JOBS, MARKETPLACES)
        return classificar_quedas(obs, ate)


def _com_ml(ml: int) -> Dict[str, int]:
    return {**NORMAL, "Mercado Livre": ml}


def _semana_normal(livro: _Livro, inicio: date, dias: int = 9) -> None:
    for i in range(dias):
        for turno in TURNOS:
            livro.bater(inicio + timedelta(days=i), turno, dict(NORMAL))


# ---------------------------------------------------------------------------
# Leitura do detail
# ---------------------------------------------------------------------------

class TestLerDetail:
    def test_contagens_zeradas_e_evidencia_do_ml(self):
        detail = (
            "Casas Bahia=2200, Mercado Livre=48, WebContinental=300 | "
            "ZERADAS: Shopee | "
            "ML: 2/40 kw ok, 38 gate [login/verificação ×36], API: sem credenciais"
        )
        lido = ler_detail(detail)
        assert lido.contagens == {
            "Casas Bahia": 2200, "Mercado Livre": 48, "WebContinental": 300,
        }
        assert lido.zeradas == {"Shopee"}
        assert lido.evidencias["Mercado Livre"].startswith("ML: 2/40 kw ok")
        assert lido.completo

    def test_detail_sem_linha_do_ml_continua_valendo(self):
        """Antes do PR #400 não há linha `ML:` — a contagem não pode depender dela."""
        lido = ler_detail("Magalu=10, Mercado Livre=5000")
        assert lido.contagens["Mercado Livre"] == 5000
        assert lido.evidencias == {}

    def test_detail_vazio(self):
        lido = ler_detail(None)
        assert not lido.tem_contagem and lido.contagens == {}

    def test_texto_livre_nao_vira_contagem(self):
        """Batida de falha (`persistência falhou: ...`) não é lista de contagem."""
        lido = ler_detail("persistência falhou: supabase")
        assert not lido.tem_contagem

    def test_detail_cortado_no_limite_nao_inventa_zero(self):
        """Detail cortado em 500 chars: plataforma que sumiu do fim é DESCONHECIDA."""
        base = ", ".join(f"Dealer{i:03d}=10" for i in range(60))
        detail = ("Casas Bahia=2200, " + base)[:500]
        lido = ler_detail(detail)
        assert lido.tem_contagem and not lido.completo
        linhas = linhas_por_dono(lido, ("Shopee", "dealers", "Casas Bahia"), MARKETPLACES)
        assert linhas["Shopee"] is None       # pode estar no pedaço cortado
        assert linhas["dealers"] is None      # soma parcial mentiria
        assert linhas["Casas Bahia"] == 2200  # o que veio antes do corte vale


class TestLinhasPorDono:
    def test_dealers_somam_tudo_que_nao_e_marketplace(self):
        lido = ler_detail("Frigelar=250, Magalu=1800, WebContinental=300")
        linhas = linhas_por_dono(lido, ("Magalu", "dealers"), MARKETPLACES)
        assert linhas == {"Magalu": 1800, "dealers": 550}

    def test_ausente_da_lista_completa_e_zero(self):
        lido = ler_detail("Magalu=1800")
        assert linhas_por_dono(lido, ("Shopee",), MARKETPLACES) == {"Shopee": 0}

    def test_listada_em_zeradas_e_zero(self):
        lido = ler_detail("Magalu=1800 | ZERADAS: Mercado Livre")
        assert linhas_por_dono(lido, ("Mercado Livre",), MARKETPLACES) == {
            "Mercado Livre": 0
        }


# ---------------------------------------------------------------------------
# Observações
# ---------------------------------------------------------------------------

class TestObservacoes:
    def test_so_plataformas_de_que_o_job_e_dono(self):
        """Amazon no PC não é cobrada dele: o dono são os jobs gh_amazon_*."""
        livro = _Livro()
        livro.bater(date(2026, 10, 1), "Abertura", {**NORMAL, "Amazon": 12})
        obs = observacoes_de_batidas(livro.batidas, JOBS, MARKETPLACES)
        assert "Amazon" not in {o.plataforma for o in obs}
        assert "dealers" in {o.plataforma for o in obs}

    def test_batida_sem_sucesso_ou_sem_linhas_fica_fora(self):
        """FALHOU/SEM_DADO já alertam no nível do job — não pode alertar de novo."""
        livro = _Livro()
        dia = date(2026, 10, 1)
        livro.bater(dia, "Abertura", dict(NORMAL), status="FAILED")
        livro.bater(dia, "Tarde", {k: 0 for k in NORMAL})  # rows_written=0
        livro.bater(dia, "Fechamento", dict(NORMAL), status="PARTIAL")
        assert observacoes_de_batidas(livro.batidas, JOBS, MARKETPLACES) == []

    def test_ultima_batida_do_dia_vence(self):
        livro = _Livro()
        dia = date(2026, 10, 1)
        livro.bater(dia, "Abertura", _com_ml(48))
        livro.bater(dia, "Abertura", _com_ml(4900))  # catch-up no logon
        obs = observacoes_de_batidas(livro.batidas, JOBS, MARKETPLACES)
        ml = [o for o in obs if o.plataforma == "Mercado Livre"]
        assert [o.linhas for o in ml] == [4900]


# ---------------------------------------------------------------------------
# Classificação
# ---------------------------------------------------------------------------

class TestIncidenteML:
    """09–14/10/2026, com a série real do livro-razão."""

    #: (dia, turno) → linhas do ML, do PR #400.
    QUEBRA = [
        (date(2026, 10, 9), "Tarde", 0), (date(2026, 10, 9), "Fechamento", 0),
        (date(2026, 10, 10), "Abertura", 0), (date(2026, 10, 10), "Tarde", 0),
        (date(2026, 10, 10), "Fechamento", 68),
        (date(2026, 10, 11), "Abertura", 0), (date(2026, 10, 11), "Tarde", 0),
        (date(2026, 10, 11), "Fechamento", 0),
        (date(2026, 10, 12), "Abertura", 0), (date(2026, 10, 12), "Tarde", 48),
        (date(2026, 10, 12), "Fechamento", 0),
        (date(2026, 10, 13), "Abertura", 68), (date(2026, 10, 13), "Tarde", 136),
        (date(2026, 10, 13), "Fechamento", 0),
        (date(2026, 10, 14), "Abertura", 0),
    ]

    def _livro(self) -> _Livro:
        livro = _Livro()
        _semana_normal(livro, date(2026, 9, 30), dias=9)  # 30/09 – 08/10
        livro.bater(date(2026, 10, 9), "Abertura", _com_ml(5100))
        for dia, turno, ml in self.QUEBRA:
            livro.bater(
                dia, turno, _com_ml(ml),
                extra=f"ML: {1 if ml else 0}/40 kw ok, 39 gate [login/verificação ×39]",
            )
        return livro

    def test_alarma_ja_no_primeiro_dia(self):
        """Às 20h de 09/10 a tarde e a noite já vieram magras: alarme no mesmo dia."""
        quedas = self._livro().quedas(date(2026, 10, 9))
        assert [q.plataforma for q in quedas] == ["Mercado Livre"]
        assert quedas[0].dias == 1
        assert not quedas[0].cronica
        assert [t.turno for t in quedas[0].turnos] == ["Tarde", "Fechamento"]

    def test_vira_cronica_e_nao_se_cala(self):
        """No 14/10 a sequência é de 5 dias: a mediana não "aprendeu" os 68."""
        quedas = self._livro().quedas(date(2026, 10, 14))
        assert len(quedas) == 1
        ml = quedas[0]
        assert ml.plataforma == "Mercado Livre"
        # 14/10 só tem a manhã: o último dia avaliável é 13/10.
        assert ml.dia == date(2026, 10, 13)
        assert ml.dias == 5
        assert ml.cronica
        assert {t.turno for t in ml.turnos} == {"Abertura", "Tarde", "Fechamento"}
        assert all(t.mediana >= 4900 for t in ml.turnos)
        assert ml.evidencia and ml.evidencia.startswith("ML: ")

    def test_outras_plataformas_seguem_limpas(self):
        quedas = self._livro().quedas(date(2026, 10, 14))
        assert {q.plataforma for q in quedas} == {"Mercado Livre"}

    def test_recuperacao_apaga_o_alarme(self):
        livro = self._livro()
        for turno in TURNOS:
            livro.bater(date(2026, 10, 15), turno, dict(NORMAL))
        assert livro.quedas(date(2026, 10, 15)) == []


class TestLimiares:
    INICIO = date(2026, 10, 1)
    DIA = date(2026, 10, 8)

    def _livro(self, ml_por_turno: Dict[str, int]) -> _Livro:
        livro = _Livro()
        _semana_normal(livro, self.INICIO, dias=7)
        for turno, ml in ml_por_turno.items():
            livro.bater(self.DIA, turno, _com_ml(ml))
        return livro

    def test_um_turno_magro_nao_basta(self):
        """Bloqueio momentâneo num turno só, que a repescagem resolve, não é padrão."""
        livro = self._livro({"Abertura": 30, "Tarde": 5000, "Fechamento": 5000})
        assert livro.quedas(self.DIA) == []

    def test_dois_turnos_magros_bastam(self):
        livro = self._livro({"Abertura": 30, "Tarde": 900, "Fechamento": 5000})
        quedas = livro.quedas(self.DIA)
        assert [q.plataforma for q in quedas] == ["Mercado Livre"]
        assert [t.turno for t in quedas[0].turnos] == ["Abertura", "Tarde"]

    def test_queda_moderada_nao_alarma(self):
        """40% abaixo do normal é variação de mercado, não quebra."""
        livro = self._livro({t: 3000 for t in TURNOS})
        assert livro.quedas(self.DIA) == []

    def test_limiar_de_20_por_cento(self):
        # mediana 5000 → limiar 1000: 999 é magro, 1000 não.
        assert self._livro({"Abertura": 999, "Tarde": 999}).quedas(self.DIA)
        assert not self._livro({"Abertura": 1000, "Tarde": 1000}).quedas(self.DIA)

    def test_historico_curto_nao_julga(self):
        """Plataforma nova (ou recém-instrumentada): sem 3 amostras, sem veredito."""
        livro = _Livro()
        _semana_normal(livro, self.INICIO, dias=2)
        for turno in TURNOS:
            livro.bater(self.INICIO + timedelta(days=2), turno, _com_ml(0))
        assert livro.quedas(self.INICIO + timedelta(days=2)) == []

    def test_volume_baixo_demais_nao_e_avaliado(self):
        """Mediana de 20 linhas: 20% são 4, e o ruído de uma keyword viraria alarme."""
        obs = [
            Observacao(self.INICIO + timedelta(days=d), t, "Mini", 20, "local_manha")
            for d in range(7) for t in TURNOS
        ] + [
            Observacao(self.DIA, t, "Mini", 1, "local_manha") for t in TURNOS
        ]
        assert classificar_quedas(obs, self.DIA) == []

    def test_retrato_velho_nao_e_estado_atual(self):
        """Queda de semana passada sem observação depois não é reportada hoje."""
        livro = self._livro({t: 0 for t in TURNOS})
        assert livro.quedas(self.DIA + timedelta(days=5)) == []

    def test_dia_sem_coleta_nao_quebra_a_sequencia(self):
        """PC desligado num dia: silêncio não é mercado, nem recuperação."""
        livro = _Livro()
        _semana_normal(livro, self.INICIO, dias=7)
        for d in (7, 9):  # dia 8 sem nenhuma batida
            for turno in TURNOS:
                livro.bater(self.INICIO + timedelta(days=d), turno, _com_ml(10))
        quedas = livro.quedas(self.INICIO + timedelta(days=9))
        assert quedas and quedas[0].dias == 2

    def test_dias_futuros_sao_ignorados(self):
        livro = self._livro({t: 0 for t in TURNOS})
        assert livro.quedas(self.DIA - timedelta(days=1)) == []


class TestSerializacao:
    def test_to_dict(self):
        livro = _Livro()
        _semana_normal(livro, date(2026, 10, 1), dias=7)
        for turno in TURNOS:
            livro.bater(date(2026, 10, 8), turno, _com_ml(50), extra="ML: 1/40 kw ok")
        (queda,) = livro.quedas(date(2026, 10, 8))
        dados = queda.to_dict()
        assert dados["plataforma"] == "Mercado Livre"
        assert dados["dia"] == "2026-10-08"
        assert dados["dias_degradada"] == 1
        assert dados["jobs"] == ["local_manha", "local_tarde", "local_noite"]
        assert dados["turnos"][0]["percentual"] == 1.0
        assert dados["evidencia"] == "ML: 1/40 kw ok"
