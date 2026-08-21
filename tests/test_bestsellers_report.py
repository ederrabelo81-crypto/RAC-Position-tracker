"""
tests/test_bestsellers_report.py — resiliência da renderização do brief.

Incidente de 21/08/2026: a coleta rodou, exportou CSV e subiu 51 linhas ao
Supabase, mas o brief diário derrubou o processo inteiro com
`ImportError: tabulate` — `DataFrame.to_markdown` exige `tabulate`, ausente no
ambiente do notebook coletor. O relatório NÃO pode falhar depois de a coleta ter
sido persistida.

Rode: pytest tests/test_bestsellers_report.py
"""
import pandas as pd

from bestsellers import report


def _sem_tabulate(monkeypatch):
    """Força o caminho de fallback simulando `tabulate` ausente."""
    def _boom(self, *a, **k):
        raise ImportError("tabulate ausente (simulado)")

    monkeypatch.setattr(pd.DataFrame, "to_markdown", _boom, raising=True)


class TestTabelaSemTabulate:
    def test_renderiza_sem_tabulate(self, monkeypatch):
        _sem_tabulate(monkeypatch)
        df = pd.DataFrame(
            {"plataforma": ["Amazon", "Leroy"], "pct_topN": [26.0, None]}
        )
        saida = report._tabela(df)
        # Cabeçalho + separador + duas linhas de dados
        assert "| plataforma | pct_topN |" in saida
        assert saida.count("\n") == 3
        # NaN vira célula vazia, não a string "nan"
        assert "nan" not in saida.lower()

    def test_dataframe_vazio_nao_toca_tabulate(self, monkeypatch):
        _sem_tabulate(monkeypatch)
        assert report._tabela(pd.DataFrame()) == "_Sem dados nesta seção._"

    def test_usa_tabulate_quando_disponivel(self):
        """Caminho feliz: com tabulate instalado, ainda produz markdown válido."""
        df = pd.DataFrame({"a": [1], "b": [2]})
        saida = report._tabela(df)
        assert "a" in saida and "b" in saida and saida.startswith("|")
