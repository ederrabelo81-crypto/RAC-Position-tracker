"""
tests/test_admin_automation.py — Lógica pura do motor de automação ADMIN.

Cobre a política residual da fila REVISAR (heurística terminal), a validação
das classificações do LLM (o catálogo dispõe), a agregação de status do run e
o gate de notificação. Sem rede/Supabase — só funções puras.
"""
import pytest

from utils.admin_automation import (
    STEP_LABELS,
    STEP_ORDER,
    StepResult,
    _STEP_FUNCS,
    _build_status,
    _build_telegram_message,
    _has_changes,
    _residual_heuristic,
    _validate_llm_item,
)

_CATALOG_BRANDS = {"MIDEA", "LG", "SAMSUNG", "ELGIN"}
_CATALOG_BTUS = {9000, 12000, 18000, 24000}


class TestResidualHeuristic:
    def test_nao_ac_para_nome_sem_termos_ac(self):
        estado, reason = _residual_heuristic("iPhone 15 Pro Max 256GB")
        assert estado == "NAO_AC"
        assert "filtro" in reason

    def test_fora_escopo_para_ac_sem_marca_btu(self):
        # Passa no filtro AC (termo forte) mas não é mapeável a uma família
        estado, reason = _residual_heuristic("Ar Condicionado Split Hi-Wall")
        assert estado == "FORA_ESCOPO"
        assert "não-mapeável" in reason

    def test_nao_ac_para_acessorio(self):
        # Blocklist do is_valid_product (capa/suporte caem no filtro)
        estado, _ = _residual_heuristic("Película de vidro celular samsung")
        assert estado == "NAO_AC"


class TestValidateLlmItem:
    def test_mapeado_valido_monta_familia_generica(self):
        item = {"i": 0, "estado": "MAPEADO", "marca": "midea", "btu": 12000, "ciclo": "QF"}
        out = _validate_llm_item(item, _CATALOG_BRANDS, _CATALOG_BTUS)
        assert out == ("MAPEADO", "MIDEA-12000-QF", "MIDEA")

    def test_mapeado_marca_fora_do_catalogo_descartado(self):
        item = {"i": 0, "estado": "MAPEADO", "marca": "DAIKIN", "btu": 12000, "ciclo": "F"}
        assert _validate_llm_item(item, _CATALOG_BRANDS, _CATALOG_BTUS) is None

    def test_mapeado_btu_fora_do_catalogo_descartado(self):
        item = {"i": 0, "estado": "MAPEADO", "marca": "LG", "btu": 7500, "ciclo": "F"}
        assert _validate_llm_item(item, _CATALOG_BRANDS, _CATALOG_BTUS) is None

    def test_mapeado_sem_btu_descartado(self):
        item = {"i": 0, "estado": "MAPEADO", "marca": "LG", "btu": None, "ciclo": "F"}
        assert _validate_llm_item(item, _CATALOG_BRANDS, _CATALOG_BTUS) is None

    def test_fora_escopo_aplica_sem_familia(self):
        item = {"i": 0, "estado": "FORA_ESCOPO", "marca": "consul", "btu": None, "ciclo": "F"}
        assert _validate_llm_item(item, _CATALOG_BRANDS, _CATALOG_BTUS) == \
            ("FORA_ESCOPO", None, "CONSUL")

    def test_nao_ac_aplica_sem_marca(self):
        item = {"i": 0, "estado": "NAO_AC", "marca": None, "btu": None, "ciclo": "F"}
        assert _validate_llm_item(item, _CATALOG_BRANDS, _CATALOG_BTUS) == \
            ("NAO_AC", None, None)

    def test_revisar_mantem_na_fila(self):
        item = {"i": 0, "estado": "REVISAR", "marca": None, "btu": None, "ciclo": "F"}
        assert _validate_llm_item(item, _CATALOG_BRANDS, _CATALOG_BTUS) is None

    def test_estado_invalido_descartado(self):
        item = {"i": 0, "estado": "QUALQUER", "marca": "LG", "btu": 9000, "ciclo": "F"}
        assert _validate_llm_item(item, _CATALOG_BRANDS, _CATALOG_BTUS) is None

    def test_ciclo_invalido_cai_para_frio(self):
        item = {"i": 0, "estado": "MAPEADO", "marca": "ELGIN", "btu": 9000, "ciclo": "X"}
        assert _validate_llm_item(item, _CATALOG_BRANDS, _CATALOG_BTUS) == \
            ("MAPEADO", "ELGIN-9000-F", "ELGIN")


class TestBuildStatus:
    def test_todas_ok(self):
        steps = [StepResult("a"), StepResult("b")]
        assert _build_status(steps) == "ok"

    def test_falha_parcial(self):
        steps = [StepResult("a"), StepResult("b", ok=False)]
        assert _build_status(steps) == "partial"

    def test_todas_falharam(self):
        steps = [StepResult("a", ok=False), StepResult("b", ok=False)]
        assert _build_status(steps) == "error"

    def test_sem_etapas_eh_skipped(self):
        assert _build_status([]) == "skipped"


class TestNotificationGate:
    def _report(self, details, errors=0, status="ok"):
        return {
            "trigger": "teste", "status": status, "errors": errors,
            "duration_s": 1.0, "dry_run": False,
            "steps": [{"name": "x", "label": "X", "ok": errors == 0,
                       "summary": "s", "details": details}],
        }

    def test_sem_mudancas_fica_silencioso(self):
        assert _has_changes(self._report({"scanned": 100, "deleted": 0})) is False

    def test_delecao_dispara_notificacao(self):
        assert _has_changes(self._report({"deleted": 3})) is True

    def test_resolucao_depara_dispara_notificacao(self):
        assert _has_changes(self._report({"aplicadas": 12})) is True

    def test_mensagem_contem_status_e_etapas(self):
        msg = _build_telegram_message(self._report({"deleted": 3}))
        assert "Automação Admin" in msg
        assert "OK" in msg
        assert "X: s" in msg


class TestPipelineRegistry:
    def test_ordem_cobre_todas_as_etapas(self):
        assert list(STEP_LABELS.keys()) == STEP_ORDER
        assert set(_STEP_FUNCS.keys()) == set(STEP_ORDER)


class TestShouldRun:
    """should_run ignora dry-runs: decide pelo último run REAL (fix cubic #179)."""

    def _patch_runs(self, monkeypatch, runs):
        import utils.admin_automation as m
        monkeypatch.setattr(m, "_read_local_runs", lambda: runs)
        monkeypatch.setattr(m, "_get_client", lambda: None)
        return m

    def _iso(self, **delta):
        from datetime import datetime, timedelta, timezone
        return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()

    def test_dry_run_recente_nao_adia_nem_forca(self, monkeypatch):
        # Run real há 2h (dentro da janela) + dry-run há 5min → NÃO roda
        m = self._patch_runs(monkeypatch, [
            {"dry_run": False, "status": "ok", "started_at": self._iso(hours=2)},
            {"dry_run": True,  "status": "ok", "started_at": self._iso(minutes=5)},
        ])
        assert m.should_run(min_hours=24) is False

    def test_run_real_antigo_dispara(self, monkeypatch):
        m = self._patch_runs(monkeypatch, [
            {"dry_run": False, "status": "ok", "started_at": self._iso(hours=26)},
            {"dry_run": True,  "status": "ok", "started_at": self._iso(minutes=5)},
        ])
        assert m.should_run(min_hours=24) is True

    def test_sem_historico_dispara(self, monkeypatch):
        m = self._patch_runs(monkeypatch, [])
        assert m.should_run(min_hours=24) is True

    def test_so_dry_runs_dispara(self, monkeypatch):
        m = self._patch_runs(monkeypatch, [
            {"dry_run": True, "status": "ok", "started_at": self._iso(minutes=5)},
        ])
        assert m.should_run(min_hours=24) is True
