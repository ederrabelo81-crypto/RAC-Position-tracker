"""
Fixtures compartilhadas da suíte.

`data/adaptive_scraper.db` é versionado e guarda o aprendizado do PC coletor.
Qualquer teste que passe por `search()` de um scraper grava nele (via
`AdaptiveScraperConfig.record_result`). Foi assim que o PR #408 subiu ao
`main` um banco sujo de execuções de teste, e o `git pull` do PC coletor
recusou o merge por conflito com a cópia local. Toda a suíte grava num banco
temporário.
"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _adaptive_db_isolado(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redireciona o banco do sistema adaptativo para um arquivo temporário."""
    import utils.adaptive_scraper as adaptive

    monkeypatch.setattr(adaptive, "_DB_PATH", tmp_path / "adaptive_scraper.db")
