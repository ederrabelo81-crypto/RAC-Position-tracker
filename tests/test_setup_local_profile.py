"""
tests/test_setup_local_profile.py — navegação até o login do Mercado Livre.

O setup do perfil dedicado é o passo 1 do roteiro de recuperação do login gate;
quando ele quebra, não há como logar e a coleta fica sem antídoto. Em
31/07/2026 a rota fixa `/jms/mlb/lgz/msl/login` virou 404 ("Parece que esta
página no existe") e travou o setup inteiro — por isso a busca pelo formulário
é por CANDIDATOS, testados um a um, e nunca por URL hardcoded.

Rode: pytest tests/test_setup_local_profile.py
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.setup_local_profile import (  # noqa: E402
    _ML_EMAIL_FIELD,
    _ML_LOGIN_LINK_SELECTORS,
    _abrir_login_ml,
)


class _FakeLocator:
    def __init__(self, page, seletor):
        self.page = page
        self.seletor = seletor

    @property
    def first(self):
        return self

    def click(self, timeout=None):
        if self.seletor in self.page.seletores_sem_link:
            raise TimeoutError(f"nenhum elemento para {self.seletor}")
        self.page.clicados.append(self.seletor)

    def wait_for(self, state=None, timeout=None):
        if self.seletor == _ML_EMAIL_FIELD and not self.page.formulario_visivel():
            raise TimeoutError("campo de e-mail ausente")


class _FakePage:
    """
    Página mínima do fluxo de login.

    `revela_formulario` é o conjunto de seletores de link que, ao serem
    clicados, levam ao formulário — os demais representam links mortos
    (ex.: card apontando para a rota velha `/lgz/msl/...`).
    """

    def __init__(self, revela_formulario=(), seletores_sem_link=(), conta_loga=False):
        self.revela_formulario = set(revela_formulario)
        self.seletores_sem_link = set(seletores_sem_link)
        self.conta_loga = conta_loga
        self.gotos = []
        self.clicados = []

    def goto(self, url, **_kwargs):
        self.gotos.append(url)

    def locator(self, seletor):
        return _FakeLocator(self, seletor)

    def wait_for_load_state(self, *_a, **_k):
        pass

    def formulario_visivel(self) -> bool:
        if not self.clicados:
            # ainda na rota de conta: só mostra o form se ela redirecionar
            return self.conta_loga
        return self.clicados[-1] in self.revela_formulario


class TestSeletoresDeLogin:
    def test_nenhum_candidato_aponta_para_a_rota_morta(self):
        # `/lgz/msl/login` é o 404 de 31/07 — só pode aparecer dentro de :not()
        for seletor in _ML_LOGIN_LINK_SELECTORS:
            assert (
                "/msl/" not in seletor
                or ":not([href*='/msl/'])" in seletor
            ), seletor

    def test_candidatos_genericos_excluem_msl(self):
        for seletor in _ML_LOGIN_LINK_SELECTORS:
            if "href*='/lgz/'" in seletor or "href*='login'" in seletor:
                assert ":not([href*='/msl/'])" in seletor

    def test_link_explicito_vem_primeiro(self):
        assert _ML_LOGIN_LINK_SELECTORS[0] == "a[data-link-id='login']"


class TestAbrirLoginML:
    def test_rota_de_conta_resolve_sem_clicar_em_nada(self):
        page = _FakePage(conta_loga=True)
        assert _abrir_login_ml(page) is True
        assert page.clicados == []

    def test_candidato_que_nao_revela_o_form_passa_para_o_proximo(self):
        # 1º link existe mas leva a lugar nenhum (o caso do card morto)
        page = _FakePage(revela_formulario={_ML_LOGIN_LINK_SELECTORS[1]})
        assert _abrir_login_ml(page) is True
        assert page.clicados == list(_ML_LOGIN_LINK_SELECTORS[:2])

    def test_seletor_sem_elemento_nao_interrompe_a_busca(self):
        page = _FakePage(
            revela_formulario={_ML_LOGIN_LINK_SELECTORS[2]},
            seletores_sem_link={_ML_LOGIN_LINK_SELECTORS[0]},
        )
        assert _abrir_login_ml(page) is True
        assert _ML_LOGIN_LINK_SELECTORS[0] not in page.clicados

    def test_sem_nenhum_candidato_valido_cai_para_login_manual(self):
        page = _FakePage()
        assert _abrir_login_ml(page) is False
        assert len(page.clicados) == len(_ML_LOGIN_LINK_SELECTORS)

    def test_goto_falhando_nao_propaga(self):
        page = _FakePage()

        def explode(*_a, **_k):
            raise RuntimeError("DNS")

        page.goto = explode
        assert _abrir_login_ml(page) is False  # degrada para manual, sem estourar


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ---------------------------------------------------------------------------
# Login do ML SEM porta de debug (10/11/2026)
# ---------------------------------------------------------------------------

import scripts.setup_local_profile as slp  # noqa: E402


class _FakeProc:
    def __init__(self, vivo_por=0):
        # quantas chamadas de poll() o processo continua vivo
        self.vivo_por = vivo_por

    def poll(self):
        if self.vivo_por > 0:
            self.vivo_por -= 1
            return None
        return 0


class TestLoginSemDebug:
    def _base(self, monkeypatch, proc, cdp_up=False, fecha_ok=True):
        chamadas = {"spawn": [], "close": 0}
        monkeypatch.setattr(slp, "cdp_endpoint_if_up", lambda port: "x" if cdp_up else None)

        def _close(port):
            chamadas["close"] += 1
            chamadas["close_port"] = port
            return fecha_ok

        monkeypatch.setattr(slp, "close_chrome_via_cdp", _close)

        def _spawn(port, profile_dir, start_url=None, debug=True):
            chamadas["spawn"].append(debug)
            chamadas["spawn_args"] = (port, profile_dir, start_url)
            return proc

        monkeypatch.setattr(slp, "spawn_chrome", _spawn)
        monkeypatch.setattr(slp.time, "sleep", lambda s: None)
        monkeypatch.setattr("builtins.input", lambda *a: "")
        return chamadas

    def test_ml_marcado_para_logar_sem_debug(self):
        assert slp.SITES["mercadolivre"].get("login_sem_debug") is True
        assert not slp.SITES["shopee"].get("login_sem_debug")

    def test_abre_sem_debug_e_espera_fechar(self, monkeypatch, tmp_path):
        proc = _FakeProc(vivo_por=50)  # sobrevive à checagem de 3 s
        ch = self._base(monkeypatch, proc)
        # 3 s de checagem inicial consomem poll(); depois o usuário fecha
        monkeypatch.setattr(slp, "_wait_process_exit", lambda p, s: s != 3)
        assert slp._login_sem_debug(9222, tmp_path, "mercadolivre") is True
        assert ch["spawn"] == [False]
        assert ch["spawn_args"] == (
            9222, tmp_path, slp.SITES["mercadolivre"]["start_url"]
        )
        assert ch["close"] == 0

    def test_fecha_o_chrome_de_debug_antes(self, monkeypatch, tmp_path):
        ch = self._base(monkeypatch, _FakeProc(), cdp_up=True)
        (tmp_path / "DevToolsActivePort").write_text("9222\n/devtools/browser/x")
        monkeypatch.setattr(slp, "_wait_process_exit", lambda p, s: s != 3)
        assert slp._login_sem_debug(9222, tmp_path, "mercadolivre") is True
        assert ch["close"] == 1
        assert ch["close_port"] == 9222
        assert ch["spawn"] == [False]
        assert ch["spawn_args"][:2] == (9222, tmp_path)

    def test_nao_fecha_chrome_de_outro_perfil(self, monkeypatch, tmp_path):
        # porta ocupada por um Chrome de debug que não é o do perfil
        ch = self._base(monkeypatch, _FakeProc(), cdp_up=True)
        (tmp_path / "DevToolsActivePort").write_text("9333\n/devtools/browser/x")
        assert slp._login_sem_debug(9222, tmp_path, "mercadolivre") is False
        assert ch["close"] == 0
        assert ch["spawn"] == []

    def test_nao_abre_se_nao_conseguiu_fechar_o_de_debug(self, monkeypatch, tmp_path):
        ch = self._base(monkeypatch, _FakeProc(), cdp_up=True, fecha_ok=False)
        (tmp_path / "DevToolsActivePort").write_text("9222\n/devtools/browser/x")
        assert slp._login_sem_debug(9222, tmp_path, "mercadolivre") is False
        assert ch["spawn"] == []

    def test_chrome_que_morre_ao_abrir_e_falha(self, monkeypatch, tmp_path):
        # a janela foi parar noutro Chrome do perfil (com debug)
        self._base(monkeypatch, _FakeProc())
        monkeypatch.setattr(slp, "_wait_process_exit", lambda p, s: True)
        assert slp._login_sem_debug(9222, tmp_path, "mercadolivre") is False
