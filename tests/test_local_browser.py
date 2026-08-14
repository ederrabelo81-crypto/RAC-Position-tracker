"""
tests/test_local_browser.py — toggles e resolvers do Chrome local compartilhado.

Cobre a lógica pura de ``scrapers/local_browser.py`` (leitura de env). O fluxo
de browser em si (launch_persistent_context, warm-up, interceptação) exige um
Chrome real e é validado manualmente no notebook — ver
docs/COLETA_LOCAL_AUTENTICADA.md.

Rode: pytest tests/test_local_browser.py
"""
from pathlib import Path

import pytest

from scrapers import local_browser as lb


class TestIsLocalChromeEnabled:
    @pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "sim", "on", "On"])
    def test_truthy_values_enable(self, monkeypatch, val):
        monkeypatch.setenv("RAC_LOCAL_CHROME", val)
        assert lb.is_local_chrome_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "", "off", "qualquer"])
    def test_falsy_values_disable(self, monkeypatch, val):
        monkeypatch.setenv("RAC_LOCAL_CHROME", val)
        assert lb.is_local_chrome_enabled() is False

    def test_unset_is_disabled(self, monkeypatch):
        monkeypatch.delenv("RAC_LOCAL_CHROME", raising=False)
        assert lb.is_local_chrome_enabled() is False


class TestResolveProfileDir:
    def test_default_is_data_chrome_profile(self, monkeypatch):
        monkeypatch.delenv("RAC_CHROME_PROFILE_DIR", raising=False)
        assert lb._resolve_profile_dir() == lb.DEFAULT_PROFILE_DIR
        assert lb.DEFAULT_PROFILE_DIR.name == "chrome_profile"
        assert lb.DEFAULT_PROFILE_DIR.parent.name == "data"

    def test_override_via_env(self, monkeypatch, tmp_path):
        custom = tmp_path / "meu_perfil"
        monkeypatch.setenv("RAC_CHROME_PROFILE_DIR", str(custom))
        assert lb._resolve_profile_dir() == Path(str(custom))


class TestResolvePort:
    def test_default_port(self, monkeypatch):
        monkeypatch.delenv("RAC_CDP_PORT", raising=False)
        assert lb._resolve_port() == 9222

    def test_override_via_env(self, monkeypatch):
        monkeypatch.setenv("RAC_CDP_PORT", "9333")
        assert lb._resolve_port() == 9333

    def test_non_numeric_falls_back(self, monkeypatch):
        monkeypatch.setenv("RAC_CDP_PORT", "abc")
        assert lb._resolve_port() == 9222


class TestKeepChromeOpen:
    def test_default_keeps_open(self, monkeypatch):
        monkeypatch.delenv("RAC_LOCAL_CHROME_KEEP", raising=False)
        assert lb._keep_chrome_open() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off"])
    def test_opt_out_closes(self, monkeypatch, val):
        monkeypatch.setenv("RAC_LOCAL_CHROME_KEEP", val)
        assert lb._keep_chrome_open() is False


class TestChromeArgs:
    """
    31/07: o perfil dedicado está sincronizado com a conta Google do usuário e
    herdou as extensões dele — ao abrir, várias dispararam aba de boas-vindas
    por cima da janela de login. Extensão não serve para nada na coleta.
    """

    def test_sobe_sem_extensoes_por_padrao(self, monkeypatch, tmp_path):
        monkeypatch.delenv("RAC_CHROME_KEEP_EXTENSIONS", raising=False)
        assert "--disable-extensions" in lb._chrome_args(9222, tmp_path)

    @pytest.mark.parametrize("val", ["1", "true", "sim", "on"])
    def test_env_mantem_as_extensoes(self, monkeypatch, tmp_path, val):
        monkeypatch.setenv("RAC_CHROME_KEEP_EXTENSIONS", val)
        assert "--disable-extensions" not in lb._chrome_args(9222, tmp_path)

    def test_url_inicial_fica_por_ultimo(self, monkeypatch, tmp_path):
        # o Chrome trata o último argumento posicional como URL a abrir —
        # uma flag depois dele viraria "página" e a URL não abriria
        monkeypatch.delenv("RAC_CHROME_KEEP_EXTENSIONS", raising=False)
        args = lb._chrome_args(9222, tmp_path, "https://www.mercadolivre.com.br/")
        assert args[-1] == "https://www.mercadolivre.com.br/"

    def test_nunca_liga_flag_de_automacao(self, monkeypatch, tmp_path):
        # o Chrome tem que parecer um browser comum (ver COMMON_MISTAKES #10)
        monkeypatch.delenv("RAC_CHROME_KEEP_EXTENSIONS", raising=False)
        args = " ".join(lb._chrome_args(9222, tmp_path))
        assert "enable-automation" not in args
        assert "AutomationControlled" not in args


class TestChromeExeOverride:
    def test_env_override_when_exists(self, monkeypatch, tmp_path):
        fake = tmp_path / "chrome.exe"
        fake.write_text("x")
        monkeypatch.setenv("RAC_CHROME_EXE", str(fake))
        assert lb.find_chrome_exe() == str(fake)

    def test_env_override_ignored_when_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RAC_CHROME_EXE", str(tmp_path / "nope.exe"))
        # Não deve retornar o caminho inexistente; cai na busca padrão (pode
        # ser None neste ambiente sem Chrome).
        assert lb.find_chrome_exe() != str(tmp_path / "nope.exe")


class TestCdpEndpointProbe:
    def test_returns_none_when_nothing_listening(self):
        # Porta improvável de estar ocupada no sandbox.
        assert lb.cdp_endpoint_if_up(59999) is None


class TestGetLocalBrowserGuard:
    def test_returns_none_when_disabled(self, monkeypatch):
        """Sem RAC_LOCAL_CHROME não deve tentar abrir browser algum."""
        monkeypatch.setenv("RAC_LOCAL_CHROME", "0")
        assert lb.get_local_browser() is None


# ---------------------------------------------------------------------------
# Auto-recuperação (14/08/2026)
#
# O Chrome compartilhado é a janela do usuário: fechá-la no meio da coleta
# bastava para o singleton passar a servir um contexto morto pelo resto da run
# — "BrowserContext.new_page: Target page, context or browser has been closed"
# em TODA keyword seguinte, em todos os scrapers que o usam.
# ---------------------------------------------------------------------------


class _FakeBrowser:
    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.closed = False

    def is_connected(self) -> bool:
        return self.connected

    def close(self) -> None:
        self.closed = True


class _FakePage:
    def __init__(self) -> None:
        self.timeout = None

    def set_default_timeout(self, ms) -> None:
        self.timeout = ms


class _FakeContext:
    """Contexto que pode estar morto (como o de um Chrome fechado)."""

    def __init__(self, morto: bool = False) -> None:
        self.morto = morto
        self.paginas = 0

    def new_page(self):
        if self.morto:
            raise RuntimeError(
                "BrowserContext.new_page: Target page, context or browser "
                "has been closed"
            )
        self.paginas += 1
        return _FakePage()

    def set_default_timeout(self, ms) -> None:
        pass


def _browser_conectado(monkeypatch, *, connected=True, morto=False):
    """LocalBrowser já "conectado" a um CDP falso, sem tocar em Chrome real."""
    inst = lb.LocalBrowser()
    inst._browser = _FakeBrowser(connected=connected)
    inst.context = _FakeContext(morto=morto)
    inst._pw_handle = object()      # referência do runtime (fake)
    monkeypatch.setattr(inst, "_release_handle", lambda: None)
    return inst


class TestIsAlive:
    def test_sem_conexao_nao_esta_vivo(self):
        assert lb.LocalBrowser().is_alive() is False

    def test_browser_desconectado_nao_esta_vivo(self, monkeypatch):
        inst = _browser_conectado(monkeypatch, connected=False)
        assert inst.is_alive() is False

    def test_conectado_esta_vivo(self, monkeypatch):
        assert _browser_conectado(monkeypatch).is_alive() is True

    def test_is_connected_que_explode_conta_como_morto(self, monkeypatch):
        inst = _browser_conectado(monkeypatch)
        monkeypatch.setattr(
            inst._browser, "is_connected",
            lambda: (_ for _ in ()).throw(RuntimeError("conexão caiu")),
        )
        assert inst.is_alive() is False


class TestNewPage:
    def test_aba_normal(self, monkeypatch):
        inst = _browser_conectado(monkeypatch)
        assert inst.new_page() is not None

    def test_falha_dispara_reconexao_e_retry(self, monkeypatch):
        """1ª tentativa falha (contexto morto) → reconecta → 2ª entrega a aba."""
        inst = _browser_conectado(monkeypatch, morto=True)
        tentativas = []

        def _reconecta():
            tentativas.append(1)
            inst.context = _FakeContext()
            return True

        monkeypatch.setattr(inst, "reconnect", _reconecta)
        assert inst.new_page() is not None
        assert tentativas == [1]

    def test_desiste_quando_a_reconexao_falha(self, monkeypatch):
        inst = _browser_conectado(monkeypatch, morto=True)
        monkeypatch.setattr(inst, "reconnect", lambda: False)
        assert inst.new_page() is None

    def test_instancia_guardada_nao_relanca_apos_desistir(self, monkeypatch):
        """
        Quem guardou um LocalBrowser (a Magalu guarda) não pode ressuscitá-lo.

        `new_page()` chama `launch()` sozinho; com o teto conferido apenas no
        `reconnect()`/`get_local_browser()`, o modo local "desligado" voltava
        por essa porta e o Chrome era relançado.
        """
        monkeypatch.setattr(lb, "_LOCAL_MODE_DISABLED", True)
        spawns = []
        monkeypatch.setattr(
            lb, "spawn_chrome", lambda *a, **k: spawns.append(1) or None
        )
        # Nenhum Chrome ouvindo: sem a trava, o launch() partiria para o spawn.
        monkeypatch.setattr(lb, "cdp_endpoint_if_up", lambda _port: None)

        # Instância guardada cujo CDP já caiu (is_alive() False).
        inst = _browser_conectado(monkeypatch, connected=False)

        assert inst.new_page() is None
        assert spawns == [], "abriu Chrome com o modo local desligado"

    def test_teto_de_reconexoes_por_run(self, monkeypatch):
        monkeypatch.setattr(lb, "_RECONNECTS_USED", lb._MAX_RECONNECTS)
        inst = _browser_conectado(monkeypatch)
        monkeypatch.setattr(inst, "launch", lambda: False)
        assert inst.reconnect() is False

    def test_orcamento_de_reconexao_e_da_run_nao_da_instancia(self, monkeypatch):
        """
        Trocar o singleton não pode zerar o teto.

        Com o contador por instância, um Chrome que caísse em série era
        relançado indefinidamente: a cada substituição do singleton o
        orçamento voltava do zero e a degradação para HTTP/browser próprio
        nunca chegava.
        """
        monkeypatch.setattr(lb, "_RECONNECTS_USED", 0)
        monkeypatch.setattr(lb.LocalBrowser, "launch", lambda self: True)

        gastas = 0
        for _ in range(lb._MAX_RECONNECTS + 2):
            # instância NOVA a cada volta, como faz o get_local_browser()
            if lb.LocalBrowser().reconnect():
                gastas += 1

        assert gastas == lb._MAX_RECONNECTS
        assert lb._RECONNECTS_USED == lb._MAX_RECONNECTS


class TestSpawnAbandonado:
    """
    Chrome que subiu mas não abriu a porta de debug: a instância é descartada
    pelo `get_local_browser`, então ninguém mais tem a referência do processo.
    """

    class _Proc:
        def __init__(self) -> None:
            self.terminado = False

        def terminate(self) -> None:
            self.terminado = True

    def test_keep_zero_encerra_o_chrome_orfao(self, monkeypatch):
        monkeypatch.setenv("RAC_LOCAL_CHROME_KEEP", "0")
        inst = lb.LocalBrowser()
        inst._spawned = self._Proc()
        proc = inst._spawned
        inst._abandon_spawned()
        assert proc.terminado is True
        assert inst._spawned is None

    def test_keep_padrao_deixa_o_processo_de_pe(self, monkeypatch):
        """Pode estar só lento — a próxima tentativa reaproveita a porta."""
        monkeypatch.delenv("RAC_LOCAL_CHROME_KEEP", raising=False)
        inst = lb.LocalBrowser()
        inst._spawned = self._Proc()
        proc = inst._spawned
        inst._abandon_spawned()
        assert proc.terminado is False
        assert inst._spawned is None


class _FakeLocalBrowser:
    """Dublê do singleton: controla vivo/reconexão sem Chrome real."""

    def __init__(self, vivo: bool, reconecta: bool = False) -> None:
        self.vivo = vivo
        self.reconecta = reconecta
        self.tentou_reconectar = False
        self.fechado = False

    def is_alive(self) -> bool:
        return self.vivo

    def reconnect(self) -> bool:
        self.tentou_reconectar = True
        self.vivo = self.reconecta
        return self.reconecta

    def close(self) -> None:
        self.fechado = True


class TestGetLocalBrowserAutoCura:
    @pytest.fixture(autouse=True)
    def _ligado(self, monkeypatch):
        monkeypatch.setenv("RAC_LOCAL_CHROME", "1")
        monkeypatch.setattr(lb, "_LOCAL_BROWSER", None)
        monkeypatch.setattr(lb, "_LAUNCH_FAILURES", 0)
        monkeypatch.setattr(lb, "_LOCAL_MODE_DISABLED", False)
        monkeypatch.setattr(lb, "_RECONNECTS_USED", 0)

    def test_singleton_vivo_e_reaproveitado(self, monkeypatch):
        vivo = _FakeLocalBrowser(vivo=True)
        monkeypatch.setattr(lb, "_LOCAL_BROWSER", vivo)
        assert lb.get_local_browser() is vivo
        assert vivo.tentou_reconectar is False

    def test_singleton_morto_reconecta(self, monkeypatch):
        morto = _FakeLocalBrowser(vivo=False, reconecta=True)
        monkeypatch.setattr(lb, "_LOCAL_BROWSER", morto)
        assert lb.get_local_browser() is morto
        assert morto.tentou_reconectar is True

    def test_sem_reconexao_derruba_o_singleton(self, monkeypatch):
        """Devolver None é o contrato que faz os scrapers degradarem."""
        morto = _FakeLocalBrowser(vivo=False, reconecta=False)
        monkeypatch.setattr(lb, "_LOCAL_BROWSER", morto)
        monkeypatch.setattr(lb.LocalBrowser, "launch", lambda self: False)
        assert lb.get_local_browser() is None
        assert morto.fechado is True
        assert lb._LOCAL_BROWSER is None

    def test_teto_de_falhas_para_de_tentar(self, monkeypatch):
        tentativas = []
        monkeypatch.setattr(
            lb.LocalBrowser, "launch",
            lambda self: tentativas.append(1) or False,
        )
        for _ in range(lb._MAX_LAUNCH_FAILURES + 3):
            assert lb.get_local_browser() is None
        assert len(tentativas) == lb._MAX_LAUNCH_FAILURES

    def test_teto_de_reconexoes_tambem_impede_relancar_do_zero(self, monkeypatch):
        """
        Desistir de reconectar tem que desistir do modo local, ponto.

        Antes, o teto só barrava o `reconnect()`: o singleton era descartado e
        um LocalBrowser NOVO era aberto em seguida — o coletor ficava
        relançando Chrome em vez de degradar.
        """
        monkeypatch.setattr(lb, "_RECONNECTS_USED", lb._MAX_RECONNECTS)
        lancamentos = []
        monkeypatch.setattr(
            lb.LocalBrowser, "launch",
            lambda self: lancamentos.append(1) or True,
        )
        morto = _FakeLocalBrowser(vivo=False, reconecta=False)
        monkeypatch.setattr(lb, "_LOCAL_BROWSER", morto)

        assert lb.get_local_browser() is None
        assert lancamentos == [], "abriu um Chrome novo depois de desistir"
        # e continua desistindo nas chamadas seguintes
        assert lb.get_local_browser() is None
        assert lancamentos == []
