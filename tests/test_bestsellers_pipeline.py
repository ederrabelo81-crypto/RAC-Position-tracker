"""
tests/test_bestsellers_pipeline.py — Persistência, relatórios e import legado.

Cobre o caminho que vai da coleta ao brief sem tocar em rede:

  * o master histórico é IDEMPOTENTE — recoletar o mesmo dia substitui aquelas
    linhas em vez de duplicá-las (duplicar infla a contagem de posições e o
    KPI do dia sai errado);
  * os briefs diário, semanal e mensal renderizam com histórico real;
  * o importador dos .xlsx do Web Scraper detecta campo por CONTEÚDO, não por
    posição de coluna — foi assim que o rating da Amazon sobreviveu à mudança
    de `data4` para `data5` entre 08/08 e 10/08 de 2026.

Rode: pytest tests/test_bestsellers_pipeline.py
"""

import pandas as pd
import pytest

from bestsellers import metrics, report, storage, validate
from bestsellers.config import SOURCES
from bestsellers.models import COLUNAS, BestSellerItem


def _lista(plataforma: str, data: str, marcas):
    spec = SOURCES[plataforma]
    linhas = []
    for posicao, marca in enumerate(marcas, start=1):
        item = BestSellerItem(
            rank=posicao,
            titulo=f"Ar Condicionado Split Inverter {marca} 12000 BTUs",
            preco=1800.0 + posicao * 10,
            sku_plataforma=f"{plataforma}-{posicao}",
        )
        linhas.append(item.to_row(
            data=data, horario="09:30", run_id="run", spec=spec,
            url_coleta=spec.url_publica, endpoint=spec.url_publica,
        ))
    return storage.to_dataframe(linhas)


_MARCAS_A = ["Midea", "LG", "Springer", "Samsung", "Carrier", "LG", "Gree",
             "Comfee", "LG", "Samsung"]
_MARCAS_B = ["LG", "LG", "Samsung", "Gree", "Midea", "LG", "Samsung",
             "Gree", "Consul", "LG"]


class TestDataFrame:
    def test_ordem_canonica_de_colunas(self):
        df = _lista("amazon", "2026-08-10", _MARCAS_A)
        assert list(df.columns) == COLUNAS

    def test_colunas_ausentes_sao_criadas(self):
        df = storage.to_dataframe([{"data": "2026-08-10", "plataforma": "amazon"}])
        assert list(df.columns) == COLUNAS
        assert df["preco"].isna().all()


class TestHistorico:
    def test_primeira_gravacao(self, tmp_path):
        caminho = str(tmp_path / "master.csv")
        master = storage.atualizar_historico(_lista("amazon", "2026-08-10", _MARCAS_A), caminho)
        assert len(master) == 10
        assert len(storage.carregar_historico(caminho)) == 10

    def test_recoleta_substitui_em_vez_de_duplicar(self, tmp_path):
        caminho = str(tmp_path / "master.csv")
        storage.atualizar_historico(_lista("amazon", "2026-08-10", _MARCAS_A), caminho)
        master = storage.atualizar_historico(
            _lista("amazon", "2026-08-10", _MARCAS_B), caminho
        )
        assert len(master) == 10  # não 20
        # A segunda coleta é a que vale.
        assert master.sort_values("rank")["marca"].iloc[0] == "LG"

    def test_recoleta_de_uma_plataforma_preserva_as_outras(self, tmp_path):
        caminho = str(tmp_path / "master.csv")
        inicial = pd.concat([
            _lista("amazon", "2026-08-10", _MARCAS_A),
            _lista("shopee", "2026-08-10", _MARCAS_B),
        ], ignore_index=True)
        storage.atualizar_historico(inicial, caminho)

        master = storage.atualizar_historico(
            _lista("shopee", "2026-08-10", _MARCAS_A), caminho
        )
        assert len(master) == 20
        assert set(master["plataforma"]) == {"amazon", "shopee"}

    def test_dias_diferentes_se_acumulam(self, tmp_path):
        caminho = str(tmp_path / "master.csv")
        storage.atualizar_historico(_lista("amazon", "2026-08-10", _MARCAS_A), caminho)
        master = storage.atualizar_historico(
            _lista("amazon", "2026-08-17", _MARCAS_B), caminho
        )
        assert master["data"].nunique() == 2

    def test_historico_inexistente_devolve_vazio_tipado(self, tmp_path):
        vazio = storage.carregar_historico(str(tmp_path / "nao_existe.csv"))
        assert not len(vazio)
        assert list(vazio.columns) == COLUNAS

    def test_exportar_csv(self, tmp_path):
        caminho = storage.exportar_csv(
            _lista("amazon", "2026-08-10", _MARCAS_A), "2026-08-10", str(tmp_path)
        )
        assert caminho.exists()
        relido = pd.read_csv(caminho, sep=";", encoding="utf-8-sig")
        assert len(relido) == 10


class TestRelatorios:
    def _historico(self):
        return metrics.preparar(pd.concat([
            _lista("amazon", "2026-08-03", _MARCAS_A),
            _lista("magazineluiza", "2026-08-03", _MARCAS_B),
            _lista("amazon", "2026-08-10", _MARCAS_B),
            _lista("magazineluiza", "2026-08-10", _MARCAS_A),
        ], ignore_index=True))

    def test_brief_diario_com_base_de_comparacao(self):
        historico = self._historico()
        hoje = historico[historico["data"] == pd.Timestamp("2026-08-10")]
        anterior = historico[historico["data"] < pd.Timestamp("2026-08-10")]

        texto = report.brief_diario(hoje, anterior, validate.validar(hoje))
        assert "# Mais Vendidos RAC · 2026-08-10 (segunda)" in texto
        assert "% do top 10 ocupado pelo grupo Midea" in texto
        assert "Estabilidade do ranking" in texto
        assert "converter em share de mercado" in texto

    def test_brief_diario_sem_historico(self):
        hoje = metrics.preparar(_lista("amazon", "2026-08-10", _MARCAS_A))
        vazio = metrics.preparar(storage.to_dataframe([]))
        texto = report.brief_diario(hoje, vazio, [])
        assert "Primeira leitura da série" in texto

    def test_brief_anuncia_quarentena(self):
        hoje = metrics.preparar(_lista("shopee", "2026-08-10", _MARCAS_A))
        hoje["endpoint"] = "https://shopee.com.br/api/v4/search?by=relevancy"
        ocorrencias = validate.validar(hoje)
        texto = report.brief_diario(hoje, metrics.preparar(storage.to_dataframe([])), ocorrencias)
        assert "Em quarentena e fora da análise: shopee" in texto

    def test_brief_marca_base_de_dia_diferente(self):
        """Comparar segunda com sábado é calendário, não competição — o brief
        precisa dizer isso na cara do leitor."""
        hoje = metrics.preparar(_lista("amazon", "2026-08-17", _MARCAS_B))
        anterior = metrics.preparar(_lista("amazon", "2026-08-15", _MARCAS_A))
        texto = report.brief_diario(hoje, anterior, [])
        assert "DIFERENTE" in texto
        assert "não é\ncomparável" in texto or "não é comparável" in texto

    @pytest.mark.parametrize("periodo", [metrics.PERIODO_SEMANAL, metrics.PERIODO_MENSAL])
    def test_relatorio_periodico(self, periodo):
        texto = report.relatorio_periodico(self._historico(), periodo)
        assert "Share de topo de ranking" in texto
        assert "Ganho e perda" in texto
        assert "tendencia_valida" in texto

    def test_relatorio_periodico_sem_historico(self):
        texto = report.relatorio_periodico(
            metrics.preparar(storage.to_dataframe([])), metrics.PERIODO_SEMANAL
        )
        assert "Sem histórico" in texto

    def test_resumo_telegram(self):
        hoje = metrics.preparar(_lista("amazon", "2026-08-10", _MARCAS_A))
        resumo = report.resumo_telegram(hoje, [])
        assert "Amazon" in resumo and "top10" in resumo


class TestImportadorXlsx:
    """
    O Web Scraper renomeia e reordena colunas entre execuções. O importador
    detecta cada campo por CONTEÚDO — chumbar posição de coluna é o defeito
    que estes testes impedem de voltar.
    """

    def _arquivo(self, tmp_path, nome, dados):
        caminho = tmp_path / nome
        pd.DataFrame(dados).to_excel(caminho, index=False)
        return str(caminho)

    def test_amazon_com_colunas_deslocadas(self, tmp_path):
        from bestsellers.importer_xlsx import importar_arquivo

        dados = {
            "web_scraper_order": [f"1754-{i}" for i in range(1, 4)],
            "web_scraper_start_url": ["https://www.amazon.com.br/gp/bestsellers/home/17125373011/"] * 3,
            "ruido": ["", "", ""],
            "data3": ["#1", "#2", "#3"],
            "data4": [
                "Ar Condicionado Split Inverter Midea 12000 BTUs Frio",
                "Ar Condicionado Split LG Dual Inverter 9000 BTUs",
                "Umidificador de Ar Ultrassônico 3L",
            ],
            "data5": ["R$ 1.899,00", "R$ 2.499,00", "R$ 199,00"],
            "data6": ["4,7 de 5 estrelas", "4,5 de 5 estrelas", "4,2 de 5 estrelas"],
            "data7": ["1.226 avaliações", "980 avaliações", "45 avaliações"],
        }
        df = importar_arquivo(
            self._arquivo(tmp_path, "amazon-com-br-2026-08-10.xlsx", dados), "2026-08-10"
        )

        assert len(df) == 3
        assert list(df["plataforma"].unique()) == ["amazon"]
        assert list(df["rank"]) == [1, 2, 3]
        assert df.iloc[0]["marca"] == "Midea"
        assert df.iloc[0]["preco"] == 1899.0
        assert df.iloc[0]["rating"] == 4.7
        assert df.iloc[0]["reviews"] == 1226
        # A contaminação entra na base para o portão poder medi-la.
        assert bool(df.iloc[2]["no_escopo"]) is False

    def test_nome_do_arquivo_casa_por_prefixo(self, tmp_path):
        """`amazon-` virou `amazon-com-br-` em 10/08/2026 sem aviso."""
        from bestsellers.importer_xlsx import _detectar_fonte

        assert _detectar_fonte("amazon-2026-08-08.xlsx") == "amazon"
        assert _detectar_fonte("amazon-com-br-2026-08-10.xlsx") == "amazon"
        assert _detectar_fonte("magazineluiza-2026-08-10.xlsx") == "magazineluiza"
        assert _detectar_fonte("varejista-novo-2026-08-10.xlsx") is None

    def test_shopee_com_unidades_por_mes(self, tmp_path):
        from bestsellers.importer_xlsx import importar_arquivo

        dados = {
            "web_scraper_order": ["1754-1", "1754-2"],
            "web_scraper_start_url": ["https://shopee.com.br/search?keyword=ar%20condicionado&sortBy=sales"] * 2,
            "titulo": [
                "Ar Condicionado Split Inverter Midea 9000 BTUs",
                "Ar Condicionado Split Gree 12000 BTUs",
            ],
            "preco": ["R$ 1.799,00", "R$ 2.099,00"],
            "vendas": ["948 Vendido/Mês", "312 Vendido/Mês"],
        }
        df = importar_arquivo(
            self._arquivo(tmp_path, "shopee-2026-08-10.xlsx", dados), "2026-08-10"
        )
        assert list(df["base_vendidos"].unique()) == ["mes"]
        assert df.iloc[0]["vendidos"] == 948

    def test_arquivo_de_varejista_desconhecido_e_ignorado(self, tmp_path):
        from bestsellers.importer_xlsx import importar_arquivo

        caminho = self._arquivo(
            tmp_path, "varejista-x-2026-08-10.xlsx", {"a": [1], "b": [2]}
        )
        assert not len(importar_arquivo(caminho, "2026-08-10"))

    def test_importar_diretorio(self, tmp_path):
        from bestsellers.importer_xlsx import importar_diretorio

        self._arquivo(tmp_path, "amazon-2026-08-10.xlsx", {
            "web_scraper_start_url": ["https://www.amazon.com.br/gp/bestsellers/home/17125373011/"],
            "titulo": ["Ar Condicionado Split Midea 12000 BTUs"],
            "preco": ["R$ 1.899,00"],
        })
        df = importar_diretorio(str(tmp_path), "2026-08-10")
        assert len(df) == 1
        assert list(df.columns) == COLUNAS


class TestQuarentenaNaoContaminaAHistoria:
    """
    "QUARENTENA é o único nível que remove dados" — e remover só do relatório
    não basta: uma lista de relevância gravada no master contamina a série
    para sempre. Quem a ler meses depois não tem como saber que aquele dia
    mediu outra coisa.
    """

    def _shopee_relevancia(self):
        df = _lista("shopee", "2026-08-10", _MARCAS_A)
        df["endpoint"] = "https://shopee.com.br/api/v4/search/search_items?by=relevancy"
        return df

    def test_historico_recebe_apenas_o_que_passou(self, tmp_path):
        df = pd.concat([self._shopee_relevancia(),
                        _lista("amazon", "2026-08-10", _MARCAS_B)], ignore_index=True)
        analisado = validate.aplicar_quarentena(df, validate.validar(df))

        caminho = str(tmp_path / "master.csv")
        master = storage.atualizar_historico(analisado, caminho)
        assert set(master["plataforma"]) == {"amazon"}

    def test_csv_do_dia_guarda_a_evidencia_bruta(self, tmp_path):
        """O CSV bruto é o arquivo que se abre para entender por que a
        plataforma caiu — ele mantém tudo."""
        df = self._shopee_relevancia()
        caminho = storage.exportar_csv(df, "2026-08-10", str(tmp_path))
        relido = pd.read_csv(caminho, sep=";", encoding="utf-8-sig")
        assert set(relido["plataforma"]) == {"shopee"}


class TestImportadorEscolheAColunaCerta:
    def _arquivo(self, tmp_path, nome, dados):
        caminho = tmp_path / nome
        pd.DataFrame(dados).to_excel(caminho, index=False)
        return str(caminho)

    def test_coluna_de_btu_nao_vira_preco(self, tmp_path):
        """[12000, 9000, 3000] cai inteira na faixa plausível de preço e
        ganharia no critério de cobertura; a evidência monetária (R$ ou
        centavos) é o que desempata."""
        from bestsellers.importer_xlsx import importar_arquivo

        dados = {
            "web_scraper_start_url": ["https://www.amazon.com.br/gp/bestsellers/home/17125373011/"] * 3,
            "titulo": [
                "Ar Condicionado Split Inverter Midea 12000 BTUs",
                "Ar Condicionado Split LG 9000 BTUs",
                "Ar Condicionado Split Gree 12000 BTUs",
            ],
            "capacidade": [12000, 9000, 3000],
            "preco": ["R$ 1.899,00", "R$ 2.199,00", "R$ 1.799,00"],
        }
        df = importar_arquivo(self._arquivo(tmp_path, "amazon-2026-08-10.xlsx", dados), "2026-08-10")
        assert list(df["preco"]) == [1899.0, 2199.0, 1799.0]

    def test_coluna_de_link_nao_vira_titulo(self, tmp_path):
        """O slug do href também contém 'ar-condicionado' e é longuíssimo —
        com desempate de comprimento ilimitado, ele vencia o título."""
        from bestsellers.importer_xlsx import importar_arquivo

        dados = {
            "web_scraper_start_url": ["https://www.amazon.com.br/gp/bestsellers/home/17125373011/"] * 2,
            "link-href": [
                "https://www.amazon.com.br/ar-condicionado-split-inverter-midea-12000-btus-frio-220v/dp/B0AAA00001?ref=zg_bs_home_sccl_1&psc=1",
                "https://www.amazon.com.br/ar-condicionado-split-lg-dual-inverter-9000-btus-frio-220v/dp/B0AAA00002?ref=zg_bs_home_sccl_2&psc=1",
            ],
            "titulo": [
                "Ar Condicionado Split Inverter Midea 12000 BTUs",
                "Ar Condicionado Split LG Dual Inverter 9000 BTUs",
            ],
            "preco": ["R$ 1.899,00", "R$ 2.199,00"],
        }
        df = importar_arquivo(self._arquivo(tmp_path, "amazon-2026-08-10.xlsx", dados), "2026-08-10")
        assert not df["titulo"].str.startswith("http").any()
        assert df.iloc[0]["marca"] == "Midea"


class TestCsvBrutoDoDiaEMesclado:
    """
    Reprocessar uma plataforma não pode apagar as outras cinco da evidência
    bruta do dia — o CSV é o arquivo que se abre para entender o que
    aconteceu, e um `to_csv` cru sobre o mesmo nome sobrescrevia tudo.
    """

    def test_recoleta_de_uma_plataforma_preserva_as_demais(self, tmp_path):
        inicial = pd.concat([
            _lista("amazon", "2026-08-10", _MARCAS_A),
            _lista("shopee", "2026-08-10", _MARCAS_B),
        ], ignore_index=True)
        storage.exportar_csv(inicial, "2026-08-10", str(tmp_path))

        caminho = storage.exportar_csv(
            _lista("shopee", "2026-08-10", _MARCAS_A), "2026-08-10", str(tmp_path)
        )
        relido = pd.read_csv(caminho, sep=";", encoding="utf-8-sig")
        assert set(relido["plataforma"]) == {"amazon", "shopee"}
        assert len(relido) == 20   # não 30

    def test_linhas_da_plataforma_recoletada_sao_as_novas(self, tmp_path):
        storage.exportar_csv(_lista("shopee", "2026-08-10", _MARCAS_A), "2026-08-10", str(tmp_path))
        caminho = storage.exportar_csv(
            _lista("shopee", "2026-08-10", _MARCAS_B), "2026-08-10", str(tmp_path)
        )
        relido = pd.read_csv(caminho, sep=";", encoding="utf-8-sig")
        assert relido.sort_values("rank")["marca"].iloc[0] == "LG"
