"""
tests/test_data_cleanup_price_type.py — `delete_invalid_from_supabase` não pode
quebrar quando `preco` chega como STRING.

Achado em campo (15/09/2026, primeiro run pós-migração para o Postgres novo):
`utils/db.py` imita a fidelidade de tipo do PostgREST — `numeric` sai como
STRING, não `float` (ver CLAUDE.md, "Fidelidade de tipo é obrigatória"). A
etapa `data_cleanup` da automação ADMIN lia `preco` cru da linha e passava
direto para `is_valid_product(produto, preco)`, que faz `price <= 0` — com
`preco` string isso estoura `TypeError: '<=' not supported between instances
of 'str' and 'int'` e a etapa inteira falha, todo run, sem limpar nada.

A varredura de preços suspeitos (`_step_price_validation`, mesmo arquivo) já
fazia o cast defensivo (`float(preco) if preco is not None else None`); esta
etapa nunca ganhou o mesmo tratamento porque, com o Supabase restrito por
cota nos dias anteriores, a automação inteira vinha sendo pulada — o bug
ficou mudo até a persistência voltar.
"""
from utils.supabase_maintenance import delete_invalid_from_supabase


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    """Imita a cadeia fluente `.table().select().order().gt().range().execute()`."""

    def __init__(self, rows):
        self._rows = rows
        self._slice = None

    def select(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def gt(self, *_a, **_k):
        return self

    def range(self, offset, end):
        self._slice = (offset, end)
        return self

    def delete(self):
        return self

    def in_(self, *_a, **_k):
        return self

    def execute(self):
        if self._slice is None:
            return _Result([])
        offset, end = self._slice
        return _Result(self._rows[offset:end + 1])


class _Client:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return _Query(self._rows)


def test_preco_string_dry_run_identifica_invalidos(monkeypatch):
    import utils.supabase_maintenance as maint

    rows = [
        {"id": 1, "produto": "Ar Condicionado Split Inverter 12000 BTU", "preco": "1999.90"},
        {"id": 2, "produto": "iPhone 15 Pro 256GB", "preco": "5999.00"},
        {"id": 3, "produto": "Fralda Pampers XXG 80 unidades", "preco": "0"},
    ]
    monkeypatch.setattr(maint, "_get_client", lambda: _Client(rows))

    resultado = delete_invalid_from_supabase(dry_run=True)

    assert resultado["errors"] == 0
    assert resultado["scanned"] == 3
    # iPhone (blocklist) e Fralda (preço zerado) são inválidos; o AC não.
    assert resultado["invalid"] == 2


def test_preco_nao_numerico_nao_derruba_a_varredura(monkeypatch):
    """Lixo no campo (nunca deveria acontecer, mas não pode ser fatal)."""
    import utils.supabase_maintenance as maint

    rows = [
        {"id": 1, "produto": "Ar Condicionado Split Inverter 12000 BTU", "preco": "não é número"},
    ]
    monkeypatch.setattr(maint, "_get_client", lambda: _Client(rows))

    resultado = delete_invalid_from_supabase(dry_run=True)

    assert resultado["errors"] == 0
    assert resultado["scanned"] == 1
