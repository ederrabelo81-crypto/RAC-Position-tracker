"""Testes de scripts/render_painel_diario.py.

Cobre o que quebrou o painel no PASSO 8 antes deste script existir: um
gerador que nunca estava no checkout (achado do cubic em #384). O contrato é
o mesmo do resto do projeto — seção ausente vira "pendente", nunca é
omitida em silêncio nem inventada. Cada seção tem pelo menos um teste no
caminho de DADO real, não só no caminho "pendente" — um bug de mapeamento de
coluna (o mesmo tipo de incidente que motivou este PR) passaria batido numa
suíte que só testa ausência de dado.
"""
import json
from pathlib import Path

import pytest

from scripts.render_painel_diario import render


def _minimal_payload(**overrides):
    payload = {
        "origem": "Aiven",
        "dia": "2026-09-18",
        "n_particoes": 9,
        "n_linhas": 44509,
    }
    payload.update(overrides)
    return payload


def _section_body(html_out: str, titulo: str) -> str:
    """Extrai o corpo de uma <section><h2>titulo</h2>...</section>."""
    return html_out.split(f"<h2>{titulo}</h2>", 1)[1].split("</section>", 1)[0]


class TestRenderPainelDiario:
    def test_all_sections_pendente_when_data_missing(self):
        html_out = render(_minimal_payload())
        assert "Aiven" in html_out
        assert "2026-09-18" in html_out
        # Nenhuma secao com dado deve aparecer - todas pendentes.
        assert html_out.count("pendente") >= 8

    def test_alertas_none_vira_pendente(self):
        # alertas ausente (None) e' diferente de "checou e nao achou nada" ([]).
        html_out = render(_minimal_payload())
        assert "Alertas do dia: pendente." in html_out

    def test_alertas_lista_quando_presente(self):
        html_out = render(_minimal_payload(alertas=["Cobertura suspensa em X"]))
        assert "Cobertura suspensa em X" in html_out
        assert "Sem alertas" not in html_out

    def test_sem_alerta_mostra_normalidade(self):
        html_out = render(_minimal_payload(alertas=[]))
        assert "Sem alertas, dia dentro da normalidade." in html_out

    def test_presenca_renderiza_tabela_por_marca(self):
        payload = _minimal_payload(
            presenca={
                "total_cards": 100,
                "canais_presentes": ["Amazon"],
                "marcas": [{"marca": "Midea", "cards": 18, "presenca": "18%"}],
                "por_plataforma": {},
            }
        )
        html_out = render(payload)
        assert "Midea" in html_out
        assert "18%" in html_out

    def test_cobertura_renderiza_linhas_por_plataforma(self):
        payload = _minimal_payload(
            cobertura={
                "linhas": [
                    {"plataforma": "Amazon", "linhas_hoje": 500, "media": 480, "pct_media": "104%"},
                ]
            }
        )
        html_out = render(payload)
        sec = _section_body(html_out, "Cobertura de coleta do dia")
        assert "Amazon" in sec and "500" in sec and "104%" in sec

    def test_patrocinados_renderiza_por_marca(self):
        payload = _minimal_payload(
            patrocinados={
                "canais": "Amazon, ML",
                "total": 87,
                "base_linhas": 1200,
                "marcas": [{"marca": "Midea", "cards": 87}],
            }
        )
        html_out = render(payload)
        sec = _section_body(html_out, "Cards patrocinados")
        assert "Midea" in sec and "87" in sec

    def test_profundidade_renderiza_por_canal(self):
        payload = _minimal_payload(
            profundidade=[{"canal": "Amazon", "posicao_media": 31.6, "pct_top3": "7,7%", "n": 220}]
        )
        html_out = render(payload)
        sec = _section_body(html_out, "Profundidade de posição")
        assert "Amazon" in sec and "31.6" in sec

    def test_vitrine_renderiza_parceiros_e_fora_da_amostra(self):
        payload = _minimal_payload(
            vitrine={
                "parceiros": [{"parceiro": "GBarbosa", "posicao_media": 2.89, "pct_top3": "66,7%", "n": 18}],
                "fora_amostra": ["Frigelar"],
                "n_minimo": 10,
            }
        )
        html_out = render(payload)
        sec = _section_body(html_out, "Vitrine própria (Dealers)")
        assert "GBarbosa" in sec
        assert "Frigelar" in sec

    def test_preco_pendente_flag_respeitada_mesmo_com_faixas(self):
        payload = _minimal_payload(
            preco={"pendente": True, "faixas": {"9k": [{"marca": "Midea", "modal": 1, "piso": 1, "n": 1}]}}
        )
        html_out = render(payload)
        sec = _section_body(html_out, "Preço 9K/12K")
        assert '<p class="pendente">Preço 9K/12K: pendente.</p>' in sec
        # Regra dura: pendente=true nunca deve vazar a faixa, mesmo presente.
        assert "<table" not in sec

    def test_preco_tiers_renderiza_linha_e_marca_pendente_por_tier_vazio(self):
        payload = _minimal_payload(
            preco_tiers={
                "tiers": [
                    {"titulo": "Low", "rows": [{"marca": "Midea", "modelo": "42EBVCA09M5", "btu": 9000, "n": 5}]},
                    {"titulo": "Mid", "rows": []},
                ]
            }
        )
        html_out = render(payload)
        sec = _section_body(html_out, "Preço por tier de linha")
        assert "42EBVCA09M5" in sec
        assert "Preço Mid: pendente." in sec

    def test_tendencia_gera_chart_apenas_com_labels_e_dataset(self):
        payload = _minimal_payload(
            preco_tiers_tendencia={
                "labels": ["D-2", "D-1", "D0"],
                "resumo": [],
                "tiers": [
                    {
                        "id": "low",
                        "titulo": "Low",
                        "texto": "sobe",
                        "datasets": [{"label": "Midea", "data": [1, 2, 3], "color": "#000"}],
                    }
                ],
            }
        )
        html_out = render(payload)
        assert "new Chart(" in html_out
        assert "chart.js" in html_out

    def test_tendencia_sem_dataset_fica_pendente_sem_chart(self):
        payload = _minimal_payload(
            preco_tiers_tendencia={
                "labels": [],
                "resumo": [],
                "tiers": [{"id": "low", "titulo": "Low", "datasets": []}],
            }
        )
        html_out = render(payload)
        assert "new Chart(" not in html_out
        assert "Série de tendência: pendente." in html_out

    def test_tendencia_escapa_fechamento_de_script(self):
        # Um label/valor com "</script>" nao pode fechar o bloco de script e
        # injetar HTML/JS na pagina publica do GitHub Pages.
        payload = _minimal_payload(
            preco_tiers_tendencia={
                "labels": ["</script><script>alert(1)</script>"],
                "resumo": [],
                "tiers": [
                    {
                        "id": "low",
                        "titulo": "Low",
                        "datasets": [{"label": "</script>evil", "data": [1], "color": "#000"}],
                    }
                ],
            }
        )
        html_out = render(payload)
        assert "</script><script>alert(1)</script>" not in html_out
        assert "<\\/script>" in html_out

    def test_render_smoke_from_json_file(self, tmp_path: Path):
        payload_path = tmp_path / "saida_painel.json"
        payload_path.write_text(json.dumps(_minimal_payload()), encoding="utf-8")
        data = json.loads(payload_path.read_text(encoding="utf-8"))
        html_out = render(data)
        assert "<html" in html_out and "</html>" in html_out
