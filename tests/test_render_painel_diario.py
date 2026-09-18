"""Testes de scripts/render_painel_diario.py.

Cobre o que quebrou o painel no PASSO 8 antes deste script existir: um
gerador que nunca estava no checkout (achado do cubic em #384). O contrato é
o mesmo do resto do projeto — seção ausente vira "pendente", nunca é
omitida em silêncio nem inventada.
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


class TestRenderPainelDiario:
    def test_all_sections_pendente_when_data_missing(self):
        html_out = render(_minimal_payload())
        assert "Aiven" in html_out
        assert "2026-09-18" in html_out
        # Nenhuma secao com dado deve aparecer - todas pendentes.
        assert html_out.count("pendente") >= 7

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

    def test_preco_pendente_flag_respeitada_mesmo_com_faixas(self):
        payload = _minimal_payload(
            preco={"pendente": True, "faixas": {"9k": [{"marca": "Midea", "modal": 1, "piso": 1, "n": 1}]}}
        )
        html_out = render(payload)
        assert "Preço 9K/12K: pendente." in html_out
        # Regra dura: pendente=true nunca deve vazar a faixa, mesmo presente.
        assert "1" not in html_out.split("Preço 9K/12K")[1].split("</section>")[0]

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

    def test_render_smoke_from_json_file(self, tmp_path: Path):
        payload_path = tmp_path / "saida_painel.json"
        payload_path.write_text(json.dumps(_minimal_payload()), encoding="utf-8")
        data = json.loads(payload_path.read_text(encoding="utf-8"))
        html_out = render(data)
        assert "<html" in html_out and "</html>" in html_out
