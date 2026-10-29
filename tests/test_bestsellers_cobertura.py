"""
Fonte de Mais Vendidos parada enquanto as outras seguem gravando.

Caso de origem: Leroy Merlin sem lista desde 07/10/2026, com o job do dia
inteiro (`local_bestsellers`) verde — conferido no banco em 29/10/2026.
"""

from datetime import date

from bestsellers.cobertura import (
    DIAS_PARA_CRONICA,
    DIAS_PARA_MUDA,
    classificar_fontes,
)

# Retrato do banco em 29/10/2026 (rank <= 3, dias distintos desde 29/09), MMDD.
_REAL = {
    "amazon": "0929 0930 1001 1002 1003 1004 1007 1008 1009 1010 1012 1014 1015 1016 1017 1018 1019 1020 1021 1023 1026 1028",
    "bemol": "0929 0930 1001 1002 1003 1004 1007 1008 1009 1010 1012 1014 1015 1016 1017 1018 1019 1020 1021 1026 1028",
    "leroymerlin": "0929 0930 1001 1002 1003 1004 1007",
    "magazineluiza": "0929 1002 1003 1004 1007 1009 1010 1012 1014 1015 1016 1017 1018 1019 1020 1021 1023 1026 1028",
    "webcontinental": "0929 0930 1001 1002 1003 1004 1007 1008 1009 1010 1012 1014 1015 1016 1017 1018 1019 1020 1021 1026 1028",
}


def _leituras(por_fonte):
    for fonte, dias in por_fonte.items():
        for mmdd in dias.split():
            yield date(2026, int(mmdd[:2]), int(mmdd[2:])), fonte


def _nomes(*chaves):
    return {k: k.title() for k in chaves}


class TestRetratoReal:
    def test_so_a_leroy_e_acusada_e_cronica(self):
        fontes = _nomes(*_REAL, "casasbahia")
        rel = classificar_fontes(_leituras(_REAL), fontes, date(2026, 10, 29))

        assert [m.plataforma for m in rel.mudas] == ["leroymerlin"]
        leroy = rel.mudas[0]
        assert leroy.ultimo_dia == date(2026, 10, 7)
        assert leroy.dias_sem_dado == 15
        assert leroy.cronica
        assert rel.nunca_coletaram == ["casasbahia"]

    def test_falta_isolada_no_meio_nao_acusa(self):
        """Web Continental faltou em 23/10 e voltou: não é fonte parada."""
        rel = classificar_fontes(
            _leituras(_REAL), _nomes("webcontinental"), date(2026, 10, 29)
        )
        assert rel.mudas == []


class TestRegra:
    D = [date(2026, 10, d) for d in (19, 20, 21, 22, 23)]

    def test_um_dia_de_falta_ainda_nao_e_alerta(self):
        leituras = [(d, "a") for d in self.D] + [(d, "b") for d in self.D[:-1]]
        rel = classificar_fontes(leituras, _nomes("a", "b"), self.D[-1])
        assert rel.mudas == []

    def test_dois_dias_viram_alerta_nao_cronico(self):
        leituras = [(d, "a") for d in self.D] + [(d, "b") for d in self.D[:-DIAS_PARA_MUDA]]
        rel = classificar_fontes(leituras, _nomes("a", "b"), self.D[-1])
        assert [(m.plataforma, m.dias_sem_dado, m.cronica) for m in rel.mudas] == [
            ("b", DIAS_PARA_MUDA, False)
        ]

    def test_dia_sem_coleta_nenhuma_nao_conta(self):
        """Se nada rodou, o buraco é do job (local_bestsellers), não da fonte."""
        leituras = [(self.D[0], "a"), (self.D[0], "b")]
        rel = classificar_fontes(leituras, _nomes("a", "b"), self.D[-1])
        assert rel.mudas == []

    def test_fonte_fora_da_janela_usa_ultimo_dia_da_historia(self):
        leituras = [(d, "a") for d in self.D]
        rel = classificar_fontes(
            leituras, _nomes("a", "b"), self.D[-1],
            ultimos_dias={"b": date(2026, 8, 1)},
        )
        assert rel.mudas[0].plataforma == "b"
        assert rel.mudas[0].ultimo_dia == date(2026, 8, 1)
        assert rel.mudas[0].dias_sem_dado == len(self.D)
        assert len(self.D) >= DIAS_PARA_CRONICA and rel.mudas[0].cronica

    def test_nunca_coletou_nao_vira_muda(self):
        leituras = [(d, "a") for d in self.D]
        rel = classificar_fontes(leituras, _nomes("a", "b"), self.D[-1],
                                 ultimos_dias={"b": None})
        assert rel.mudas == []
        assert rel.nunca_coletaram == ["b"]

    def test_leitura_depois_do_dia_avaliado_e_ignorada(self):
        leituras = [(d, "a") for d in self.D] + [(date(2026, 10, 30), "b")]
        rel = classificar_fontes(leituras, _nomes("a", "b"), self.D[-1])
        assert rel.nunca_coletaram == ["b"]

    def test_json(self):
        leituras = [(d, "a") for d in self.D] + [(self.D[0], "b")]
        d = classificar_fontes(leituras, _nomes("a", "b"), self.D[-1]).to_dict()
        assert d["fontes_mudas"][0]["plataforma"] == "b"
        assert d["dias_de_coleta_avaliados"] == len(self.D)


class TestRevisaoCubic:
    D = [date(2026, 10, d) for d in (19, 20, 21, 22, 23)]

    def test_ultimo_dado_antes_da_janela_e_piso(self):
        leituras = [(d, "a") for d in self.D]
        rel = classificar_fontes(leituras, _nomes("a", "b"), self.D[-1],
                                 ultimos_dias={"b": date(2026, 6, 1)})
        assert rel.mudas[0].minimo is True
        assert rel.mudas[0].to_dict()["dias_sem_dado_e_minimo"] is True

    def test_ultimo_dado_depois_do_dia_avaliado_nao_conta(self):
        leituras = [(d, "a") for d in self.D]
        rel = classificar_fontes(leituras, _nomes("a", "b"), self.D[2],
                                 ultimos_dias={"b": date(2026, 10, 25)})
        assert rel.nunca_coletaram == ["b"]
