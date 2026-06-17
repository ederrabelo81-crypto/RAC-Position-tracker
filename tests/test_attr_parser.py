"""
Testes do attr_parser — fixtures da seção 9 do Anexo A.

Asserts cobrem os ATRIBUTOS que o parser produz (marca, btu, ciclo, tecnologia,
edição, cor, form_factor, sku_no_titulo). A coluna "resultado esperado" (SKU) das
fixtures NÃO é testada aqui: resolução de SKU depende do catálogo vivo e é a
camada seguinte (seção 7). Os casos de fusão são validados pela diferença de
atributos — ex.: Inverter vs On/Off geram tecnologias distintas, então o matcher
nunca pode fundi-los.

Rodar:  pytest -q
"""
import pytest
from utils.attr_parser import (
    parse, norm, strip_sku, parse_btu, parse_ciclo, parse_tec,
)

# (título, atributos_esperados) — só os campos relevantes por caso.
FIXTURES = [
    # ── Ecomaster 12k — títulos REAIS das duas fontes ──────────────────
    ("Ar Condicionado Midea AI Ecomaster 12.000 BTUs Inverter Frio",
     dict(marca="Midea", capacidade_btu=12000, ciclo="Frio",
          tecnologia="Inverter", edicao="Ecomaster", cor=None,
          form_factor="HW_PRESUMIDO")),
    ("Ar Condicionado Midea AI Ecomaster 12.000 BTUs Inverter Frio Preto",
     dict(marca="Midea", capacidade_btu=12000, ciclo="Frio",
          tecnologia="Inverter", edicao="Ecomaster", cor="Preto")),
    ("Ar Condicionado Midea AI Ecomaster 12.000 BTUs Inverter Quente/Frio",
     dict(marca="Midea", capacidade_btu=12000, ciclo="Quente/Frio",
          tecnologia="Inverter", edicao="Ecomaster")),
    ("Ar Condicionado Midea AI Ecomaster Pro 12.000 BTUs Inverter Frio",
     dict(marca="Midea", capacidade_btu=12000, ciclo="Frio",
          tecnologia="Inverter", edicao="Ecomaster Pro")),
    ("AR CONDICIONADO SPLIT 12000 BTU FRIO AI ECOMASTER - INVERTER - MIDEA - 220V - 42EZVCA12M5",
     dict(marca="Midea", capacidade_btu=12000, ciclo="Frio",
          tecnologia="Inverter", edicao="Ecomaster", voltagem="220V",
          form_factor="HW", sku_no_titulo="42EZVCA12M5")),
    ("AR CONDICIONADO SPLIT 12000 BTU QUENTE/FRIO AI ECOMASTER - INVERTER - MIDEA - 220V - 42EZVQA12M5",
     dict(marca="Midea", capacidade_btu=12000, ciclo="Quente/Frio",
          tecnologia="Inverter", edicao="Ecomaster", voltagem="220V",
          form_factor="HW", sku_no_titulo="42EZVQA12M5")),
    # ── Casos de FUSÃO (defeito B) — separados por atributo ────────────
    ("TCL 12.000 Inverter Frio",
     dict(marca="TCL", capacidade_btu=12000, ciclo="Frio",
          tecnologia="Inverter", edicao=None)),
    ("TCL 12.000 On/Off Frio",
     dict(marca="TCL", capacidade_btu=12000, ciclo="Frio",
          tecnologia="On/Off", edicao=None)),     # tec != Inverter -> nunca funde
    ("TCL Elite 9.000 Inverter Frio",
     dict(marca="TCL", capacidade_btu=9000, ciclo="Frio",
          tecnologia="Inverter", edicao="Elite")),
    ("TCL 9.000 Inverter Frio",
     dict(marca="TCL", capacidade_btu=9000, ciclo="Frio",
          tecnologia="Inverter", edicao=None)),    # edicao != Elite -> nunca funde
    ("LG Dual Inverter 18.000 Q/F",
     dict(marca="LG", capacidade_btu=18000, ciclo="Quente/Frio",
          tecnologia="Dual Inverter", edicao=None)),  # Dual != Inverter (R2)
    ("Gree 18.000 Inverter Frio Preto",
     dict(marca="Gree", capacidade_btu=18000, ciclo="Frio",
          tecnologia="Inverter", cor="Preto")),       # cor decide no matcher (R4)
    ("Samsung WindFree 12.000 BTUs Inverter Frio",
     dict(marca="Samsung", capacidade_btu=12000, ciclo="Frio",
          tecnologia="Inverter", edicao="WindFree")),
    # ── Guarda de form factor (R5) ─────────────────────────────────────
    ("Ar Condicionado Cassete Midea 36000 BTUs Inverter Frio",
     dict(marca="Midea", capacidade_btu=36000, form_factor="NAO_HW")),
    ("Ar Condicionado Portatil Midea 12000 BTUs Frio",
     dict(marca="Midea", capacidade_btu=12000, form_factor="NAO_HW",
          tecnologia=None)),                          # sem token de tec -> None
]


@pytest.mark.parametrize("titulo,esperado", FIXTURES,
                         ids=[t[:45] for t, _ in FIXTURES])
def test_atributos(titulo, esperado):
    a = parse(titulo).to_dict()
    for campo, valor in esperado.items():
        assert a[campo] == valor, (
            f"{campo}: esperado {valor!r}, obtido {a[campo]!r}  | {titulo}")


# ── Anti-regressão pontual ─────────────────────────────────────────────
def test_ordem_ciclo_qf_antes_de_frio():
    # "Quente/Frio" contém "Frio": tem de classificar como Quente/Frio.
    assert parse_ciclo(norm("Inverter Quente/Frio")) == "Quente/Frio"

def test_tec_ausente_nao_vira_onoff():
    assert parse_tec(norm("Ar Condicionado 12000 BTUs Frio")) is None

def test_strip_sku_nao_come_palavra_nem_voltagem():
    # não deve remover PRETO, QUENTE/FRIO nem 220V; deve remover o SKU real.
    assert strip_sku(norm("...INVERTER FRIO PRETO"))[1] is None
    assert strip_sku(norm("...QUENTE/FRIO"))[1] is None
    assert strip_sku(norm("... - 220V"))[1] is None
    assert strip_sku(norm("... - 42EZVCA12M5"))[1] == "42EZVCA12M5"

def test_btu_whitelist_ignora_numero_solto():
    assert parse_btu(norm("MODELO X 2345 INVERTER")) is None
    assert parse_btu(norm("9.000 BTUs")) == 9000
    assert parse_btu(norm("12K BTU")) == 12000

def test_inverter_e_onoff_geram_chaves_diferentes():
    # garante que o defeito de fusão é impossível na camada de atributos
    inv = parse("TCL 12.000 Inverter Frio").chave()
    off = parse("TCL 12.000 On/Off Frio").chave()
    assert inv != off
