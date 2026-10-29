"""`collect_bestsellers.py --relatorio radar` ponta a ponta (sem rede)."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scripts.collect_bestsellers as cb  # noqa: E402
from test_bestsellers_radar import GRUPO_MIDEA, _df, _serie  # noqa: E402


def test_relatorio_radar_grava_md_csv_e_notifica_escapado(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # o log do CLI vai para ./logs — fica no tmp
    df = _df(
        _serie("magazineluiza", ["Philco"], ["Philco", "Philco"]),
        _serie("mercadolivre", ["Philco"], ["Philco", "Philco"]),
        _serie("fastshop", [GRUPO_MIDEA], [GRUPO_MIDEA]),
    )
    df.loc[df["marca"] == "Philco", "marca"] = "Philco <&>"   # força o escape HTML
    master = tmp_path / "master.csv"
    df.to_csv(master, index=False, sep=";", encoding="utf-8-sig")
    enviados = []
    monkeypatch.setattr(cb, "_notificar", enviados.append)

    codigo = cb.main(["--relatorio", "radar", "--historico", str(master),
                      "--output", str(tmp_path / "out"), "--notificar"])

    assert codigo == 0
    md = next((tmp_path / "out").glob("bestsellers_radar_*.md")).read_text(encoding="utf-8")
    assert md.startswith("# 📡 Radar de marcas")
    assert "Philco <&> | SUBINDO" in md
    tabela = pd.read_csv(next((tmp_path / "out").glob("bestsellers_radar_*.csv")), sep=";")
    assert "SUBINDO" in set(tabela["status"])
    assert enviados and "Philco &lt;&amp;&gt; — SUBINDO" in enviados[0]
    assert "SUBINDO" in capsys.readouterr().out
