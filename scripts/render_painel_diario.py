"""
scripts/render_painel_diario.py — Renderiza o painel diário (HTML estático)
a partir do JSON produzido pelo PASSO 8 de docs/briefing_diario_prompt.md.

Por que este arquivo existe: o PASSO 8 original citava `rac_supabase_painel.py`
(pasta `Midea Digital Trade Marketing/`) e o PASSO F citava
`rac_drive_analise.py`/`rac_drive_painel.py` — nenhum dos três nunca existiu
neste repositório; eram arquivos soltos de uma sessão Cowork antiga. A
rotina agendada, rodando a partir de um `git pull` deste checkout, sempre ia
falhar com "arquivo não encontrado" na hora de regravar o painel — o mesmo
tipo de erro que causou o incidente de 12-17/09/2026 (escrever de memória sem
verificar que existe). Ver achado do cubic no PR que introduziu este arquivo.

Todas as seções são OPCIONAIS no JSON de entrada — uma seção ausente ou vazia
vira "pendente" no HTML, nunca é omitida em silêncio nem preenchida com
suposição (mesma regra de ouro do PASSO 5 do prompt).

Uso:
    python scripts/render_painel_diario.py saida_painel.json > docs/painel/index.html
    python scripts/render_painel_diario.py saida_painel.json --out docs/painel/index.html
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _esc(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def _pendente(label: str) -> str:
    return f'<p class="pendente">{_esc(label)}: pendente.</p>'


def _safe_json(obj: Any) -> str:
    """json.dumps seguro para embutir dentro de <script>.

    Um label/valor contendo literalmente "</script>" fecharia o bloco de
    script mais cedo e permitiria injetar HTML/JS na página pública do
    GitHub Pages. Escapa a barra de fechamento em qualquer sequência
    "</" do JSON serializado — o parser JS ignora o "\\/" (é um escape
    válido e opcional para "/"), então isso não muda o valor decodificado.
    """
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def _table(headers: List[str], rows: List[List[Any]]) -> str:
    if not rows:
        return ""
    thead = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{_esc(c)}</td>" for c in row)
        body_rows.append(f"<tr>{cells}</tr>")
    return (
        f'<table><thead><tr>{thead}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody></table>'
    )


def _section(titulo: str, corpo: str) -> str:
    return f'<section><h2>{_esc(titulo)}</h2>{corpo}</section>'


def render_alertas(alertas: Optional[List[str]]) -> str:
    if alertas is None:
        return _pendente("Alertas do dia")
    if not alertas:
        return "<p>Sem alertas, dia dentro da normalidade.</p>"
    itens = "".join(f"<li>{_esc(a)}</li>" for a in alertas)
    return f"<ul class='alertas'>{itens}</ul>"


def render_cobertura(cobertura: Optional[Dict[str, Any]]) -> str:
    if not cobertura or not cobertura.get("linhas"):
        return _pendente("Cobertura de coleta")
    return _table(
        ["Plataforma", "Linhas hoje", "Média (dias anteriores)", "% da média"],
        [
            [
                linha.get("plataforma"),
                linha.get("linhas_hoje"),
                linha.get("media"),
                linha.get("pct_media"),
            ]
            for linha in cobertura["linhas"]
        ],
    )


def render_presenca(presenca: Optional[Dict[str, Any]]) -> str:
    if not presenca or not presenca.get("marcas"):
        return _pendente("Presença de prateleira")
    partes = [
        f"<p>Total de cards no recorte: {_esc(presenca.get('total_cards'))} · "
        f"Canais presentes: {_esc(', '.join(presenca.get('canais_presentes', [])))}</p>",
        _table(
            ["Marca", "Cards", "Presença"],
            [[m.get("marca"), m.get("cards"), m.get("presenca")] for m in presenca["marcas"]],
        ),
    ]
    por_plataforma = presenca.get("por_plataforma") or {}
    for plataforma, marcas in por_plataforma.items():
        partes.append(f"<h3>{_esc(plataforma)}</h3>")
        partes.append(
            _table(
                ["Marca", "Cards", "Presença"],
                [[m.get("marca"), m.get("cards"), m.get("presenca")] for m in marcas],
            )
        )
    return "".join(partes)


def render_patrocinados(patrocinados: Optional[Dict[str, Any]]) -> str:
    if not patrocinados or not patrocinados.get("marcas"):
        return _pendente("Cards patrocinados")
    return "".join(
        [
            f"<p>Canais: {_esc(patrocinados.get('canais'))} · "
            f"Total: {_esc(patrocinados.get('total'))} · "
            f"Base: {_esc(patrocinados.get('base_linhas'))} linhas</p>",
            _table(
                ["Marca", "Cards patrocinados"],
                [[m.get("marca"), m.get("cards")] for m in patrocinados["marcas"]],
            ),
        ]
    )


def render_profundidade(profundidade: Optional[List[Dict[str, Any]]]) -> str:
    if not profundidade:
        return _pendente("Profundidade de posição")
    return _table(
        ["Canal", "Posição média", "% Top 3", "n"],
        [[p.get("canal"), p.get("posicao_media"), p.get("pct_top3"), p.get("n")] for p in profundidade],
    )


def render_vitrine(vitrine: Optional[Dict[str, Any]]) -> str:
    if not vitrine or not vitrine.get("parceiros"):
        return _pendente("Vitrine própria (Dealers)")
    partes = [
        _table(
            ["Parceiro", "Posição média", "% Top 3", "n"],
            [
                [p.get("parceiro"), p.get("posicao_media"), p.get("pct_top3"), p.get("n")]
                for p in vitrine["parceiros"]
            ],
        )
    ]
    if vitrine.get("fora_amostra"):
        partes.append(
            f"<p class='nota'>Fora da amostra mínima ({vitrine.get('n_minimo')}): "
            f"{_esc(', '.join(vitrine['fora_amostra']))}</p>"
        )
    return "".join(partes)


def render_preco(preco: Optional[Dict[str, Any]]) -> str:
    if not preco or preco.get("pendente"):
        return _pendente("Preço 9K/12K")
    partes = []
    for capacidade in ("9k", "12k"):
        linhas = (preco.get("faixas") or {}).get(capacidade)
        if not linhas:
            partes.append(_pendente(f"Preço {capacidade.upper()}"))
            continue
        partes.append(f"<h3>{capacidade.upper()}</h3>")
        partes.append(
            _table(
                ["Marca", "Modal", "Piso", "n"],
                [[l.get("marca"), l.get("modal"), l.get("piso"), l.get("n")] for l in linhas],
            )
        )
    return "".join(partes)


def render_preco_tiers(preco_tiers: Optional[Dict[str, Any]]) -> str:
    if not preco_tiers or not preco_tiers.get("tiers"):
        return _pendente("Preço por tier de linha")
    partes = []
    if preco_tiers.get("intro"):
        partes.append(f"<p>{_esc(preco_tiers['intro'])}</p>")
    for tier in preco_tiers["tiers"]:
        titulo = tier.get("titulo")
        partes.append(f"<h3>{_esc(titulo)}</h3>")
        rows = tier.get("rows") or []
        if not rows:
            partes.append(_pendente(f"Preço {titulo or 'tier'}"))
            continue
        partes.append(
            _table(
                ["Marca", "Modelo", "BTU", "Mín", "Média", "Moda", "Máx", "n"],
                [
                    [
                        row.get("marca"),
                        row.get("modelo"),
                        row.get("btu"),
                        row.get("minimo"),
                        row.get("media"),
                        row.get("moda"),
                        row.get("maximo"),
                        row.get("n"),
                    ]
                    for row in rows
                ],
            )
        )
        if tier.get("leitura"):
            partes.append(f"<p class='leitura'>{_esc(tier['leitura'])}</p>")
        if tier.get("rodape"):
            partes.append(f"<p class='rodape'>{_esc(tier['rodape'])}</p>")
    if preco_tiers.get("rodape"):
        partes.append(f"<p class='rodape'>{_esc(preco_tiers['rodape'])}</p>")
    return "".join(partes)


def render_preco_tiers_tendencia(tendencia: Optional[Dict[str, Any]]) -> tuple[str, List[str]]:
    if not tendencia or not tendencia.get("tiers"):
        return _pendente("Tendência de preço por tier"), []
    partes = []
    if tendencia.get("intro"):
        partes.append(f"<p>{_esc(tendencia['intro'])}</p>")
    if tendencia.get("resumo"):
        partes.append(
            _table(
                ["Série", "D-15", "D-7", "D0", "Δ7d", "Δ15d", "Peer D0", "Gap"],
                [
                    [
                        r.get("serie"), r.get("d15"), r.get("d7"), r.get("d0"),
                        r.get("delta7"), r.get("delta15"), r.get("peer"), r.get("gap"),
                    ]
                    for r in tendencia["resumo"]
                ],
            )
        )

    chart_scripts = []
    labels = tendencia.get("labels") or []
    for idx, tier in enumerate(tendencia["tiers"]):
        canvas_id = f"tier-chart-{idx}"
        partes.append(f"<h3>{_esc(tier.get('titulo'))}</h3>")
        if tier.get("texto"):
            partes.append(f"<p>{_esc(tier['texto'])}</p>")
        datasets = tier.get("datasets") or []
        if datasets and labels:
            partes.append(f'<canvas id="{canvas_id}" height="120"></canvas>')
            js_datasets = _safe_json(
                [
                    {
                        "label": d.get("label"),
                        "data": d.get("data"),
                        "borderColor": d.get("color", "#666"),
                        "borderDash": [6, 4] if d.get("dash") else [],
                        "fill": False,
                        "tension": 0.2,
                    }
                    for d in datasets
                ]
            )
            chart_scripts.append(
                f"""new Chart(document.getElementById('{canvas_id}'), {{
  type: 'line',
  data: {{ labels: {_safe_json(labels)}, datasets: {js_datasets} }},
  options: {{ responsive: true, plugins: {{ legend: {{ position: 'bottom' }} }} }}
}});"""
            )
        else:
            partes.append(_pendente("Série de tendência"))
    if tendencia.get("rodape"):
        partes.append(f"<p class='rodape'>{_esc(tendencia['rodape'])}</p>")
    return "".join(partes), chart_scripts


def render(data: Dict[str, Any]) -> str:
    origem = data.get("origem", "desconhecida")
    dia = data.get("dia", "?")
    n_particoes = data.get("n_particoes", "?")
    n_linhas = data.get("n_linhas", "?")

    tendencia_html, chart_scripts = render_preco_tiers_tendencia(data.get("preco_tiers_tendencia"))

    sections = [
        _section("Alertas do dia", render_alertas(data.get("alertas"))),
        _section("Cobertura de coleta do dia", render_cobertura(data.get("cobertura"))),
        _section("Presença de prateleira", render_presenca(data.get("presenca"))),
        _section("Cards patrocinados", render_patrocinados(data.get("patrocinados"))),
        _section("Profundidade de posição", render_profundidade(data.get("profundidade"))),
        _section("Vitrine própria (Dealers)", render_vitrine(data.get("vitrine"))),
        _section("Preço 9K/12K", render_preco(data.get("preco"))),
        _section("Preço por tier de linha", render_preco_tiers(data.get("preco_tiers"))),
        _section("Tendência de preço por tier", tendencia_html),
    ]

    rodape_bits = [
        data.get("rodape_de_para"),
        data.get("rodape_oscilacao"),
        "leia direção, não casa decimal",
    ]
    rodape = " · ".join(_esc(b) for b in rodape_bits if b)

    chart_js_tag = (
        '<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>'
        if chart_scripts
        else ""
    )
    chart_init = (
        f"<script>{''.join(chart_scripts)}</script>" if chart_scripts else ""
    )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Painel Trade RAC Midea — {_esc(dia)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
{chart_js_tag}
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
  h1 {{ margin-bottom: 0.25rem; }}
  .meta {{ color: #555; margin-top: 0; }}
  section {{ margin: 2rem 0; padding-top: 1rem; border-top: 1px solid #ddd; }}
  table {{ border-collapse: collapse; width: 100%; margin: 0.5rem 0; }}
  th, td {{ border: 1px solid #ddd; padding: 0.4rem 0.6rem; text-align: left; font-size: 0.9rem; }}
  th {{ background: #f4f4f4; }}
  .pendente {{ color: #a15c00; font-style: italic; }}
  .alertas li {{ margin-bottom: 0.25rem; }}
  .rodape, .leitura {{ color: #555; font-size: 0.85rem; }}
  footer {{ margin-top: 3rem; color: #777; font-size: 0.8rem; }}
</style>
</head>
<body>
  <h1>Painel Trade RAC Midea</h1>
  <p class="meta">Coleta: {_esc(dia)} · Origem: {_esc(origem)} · {_esc(n_particoes)} partições, {_esc(n_linhas)} linhas</p>
  {"".join(sections)}
  <footer>{rodape}</footer>
  {chart_init}
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_path", type=Path, help="JSON de entrada (schema do PASSO 8)")
    parser.add_argument("--out", type=Path, default=None, help="Arquivo de saída (padrão: stdout)")
    args = parser.parse_args()

    data = json.loads(args.json_path.read_text(encoding="utf-8"))
    html_out = render(data)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(html_out, encoding="utf-8")
    else:
        sys.stdout.write(html_out)


if __name__ == "__main__":
    main()
