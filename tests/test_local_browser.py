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


class TestResolveHeadless:
    def test_default_is_headed(self, monkeypatch):
        monkeypatch.delenv("RAC_LOCAL_HEADLESS", raising=False)
        assert lb._resolve_headless() is False

    @pytest.mark.parametrize("val", ["1", "true", "sim", "on"])
    def test_opt_in_headless(self, monkeypatch, val):
        monkeypatch.setenv("RAC_LOCAL_HEADLESS", val)
        assert lb._resolve_headless() is True


class TestGetLocalBrowserGuard:
    def test_returns_none_when_disabled(self, monkeypatch):
        """Sem RAC_LOCAL_CHROME não deve tentar abrir browser algum."""
        monkeypatch.setenv("RAC_LOCAL_CHROME", "0")
        assert lb.get_local_browser() is None
