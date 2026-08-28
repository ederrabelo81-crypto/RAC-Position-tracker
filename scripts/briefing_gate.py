"""
scripts/briefing_gate.py — Portão de frescor do briefing das 07:00.

O incidente que criou este arquivo
----------------------------------
Em 27/08/2026 o import do PriceTrack (agendado para 06:00 BRT) só começou às
16:18 BRT. O briefing das 07:00 rodou no horário, encontrou a tabela
`pricetrack_daily` populada — com o dia **retrasado** — e publicou um cenário
de preço de D-2 apresentado como se fosse de ontem. Nenhum alerta disparou,
porque nada tinha falhado: a tabela tinha dados, só não os do dia certo.

A lição é sobre contrato, não sobre cron: **quem consome dado tem que
verificar a idade do dado antes de publicar**. Um número velho apresentado como
atual é pior que a ausência do número — a ausência todo mundo vê.

Este script é esse contrato, num comando só. O briefing chama antes de montar
o material e decide o que fazer com a resposta.

Uso::

    python scripts/briefing_gate.py              # relatório legível + exit code
    python scripts/briefing_gate.py --json       # JSON puro no stdout
    python scripts/briefing_gate.py --max-idade-dias 2
    python scripts/briefing_gate.py --curar      # tenta consertar antes de reprovar

Exit codes:
    0 — dado de D-1 fresco: pode publicar
    1 — dado VELHO ou ausente: publique com carimbo de defasagem, ou segure
    3 — não deu para verificar (Supabase fora) — trate como 1, mas a causa
        é outra e a mensagem diz qual

Contrato de saída (--json)::

    {
      "apto": false,
      "data_referencia": "2026-08-27",
      "fontes": [
        {"fonte": "pricetrack_daily", "esperado": "2026-08-27",
         "mais_recente": "2026-08-26", "idade_dias": 2, "fresco": false,
         "linhas_d1": 0, "critico": true}
      ],
      "bloqueios": ["pricetrack_daily está em 2026-08-26 (D-2)"],
      "recomendacao": "..."
    }
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from utils.text import now_brt

#: Fontes que o briefing das 07:00 consome, e o quanto cada uma pode envelhecer.
#:
#: `critico=True` reprova a publicação sozinha. `pricetrack_daily` é crítica
#: porque É o cenário de preço do briefing; `coletas` é crítica porque é a
#: performance digital. `bestsellers` entra como não-crítica: a lista não roda
#: fim de semana por desenho, então exigir frescor dela reprovaria todo sábado.
FONTES: tuple = (
    {
        "fonte": "pricetrack_daily",
        "coluna_data": "collection_date",
        "critico": True,
        "descricao": "preços PriceTrack (cenário de preço)",
        "remediacao": "python scripts/pricetrack_api_import.py --start {d1} --end {d1} --force",
    },
    {
        "fonte": "coletas",
        "coluna_data": "data",
        "critico": True,
        "descricao": "coleta de posição/buy box (performance digital)",
        "remediacao": "Actions → RAC Price Collection → Run workflow",
    },
    {
        "fonte": "bestsellers",
        "coluna_data": "data",
        "critico": False,
        "descricao": "Mais Vendidos (não roda fim de semana)",
        "remediacao": "scripts\\collect_bestsellers.bat (no PC coletor)",
    },
)


def _client():
    """Client Supabase ou None."""
    from utils.supabase_client import _get_client

    return _get_client()


def _mais_recente(client, tabela: str, coluna: str, teto: date) -> Optional[date]:
    """Data mais recente presente na tabela, sem olhar além do teto.

    O teto é **D-1**, não hoje. Parece detalhe e não é: uma linha datada do
    próprio dia do briefing (coleta intra-dia, backfill, fuso trocado) tornava
    ``(d1 - recente).days`` NEGATIVO, e a comparação de frescor aprovava a
    publicação mesmo com zero linha de D-1 — o portão diria "fresco" no exato
    cenário que ele existe para barrar. Perguntar só pelo que é ≤ D-1 responde
    a pergunta certa: *qual é o dado mais recente que pode servir de ontem?*

    Args:
        client: Client Supabase.
        tabela: Nome da tabela.
        coluna: Coluna de data.
        teto: Maior data aceitável (D-1).

    Returns:
        A data mais recente ≤ teto, ou None se não houver nenhuma.
    """
    resp = (
        client.table(tabela)
        .select(coluna)
        .lte(coluna, teto.isoformat())
        .order(coluna, desc=True)
        .limit(1)
        .execute()
    )
    linhas = resp.data or []
    if not linhas:
        return None
    valor = linhas[0][coluna]
    return datetime.strptime(str(valor)[:10], "%Y-%m-%d").date()


def _contar(client, tabela: str, coluna: str, dia: date) -> int:
    """Linhas da tabela naquele dia."""
    resp = (
        client.table(tabela)
        .select("id", count="exact")
        .eq(coluna, dia.isoformat())
        .limit(1)
        .execute()
    )
    return resp.count or 0


def avaliar(
    hoje: Optional[date] = None, max_idade_dias: int = 1
) -> Dict[str, Any]:
    """Verifica se cada fonte do briefing tem dado de D-1.

    Args:
        hoje: Dia BRT do briefing (default: hoje).
        max_idade_dias: Quantos dias de defasagem são aceitáveis. 1 = o dado
            mais recente precisa ser de ontem.

    Returns:
        O contrato descrito na docstring do módulo.

    Raises:
        RuntimeError: Supabase indisponível — o portão não conseguiu verificar.
    """
    hoje = hoje or now_brt().date()
    d1 = hoje - timedelta(days=1)

    client = _client()
    if client is None:
        raise RuntimeError(
            "Supabase não configurado — o portão não consegue verificar a "
            "idade do dado. Publicar sem verificar é o que causou o briefing "
            "de D-2 em 27/08."
        )

    resultados: List[Dict[str, Any]] = []
    bloqueios: List[str] = []

    for spec in FONTES:
        try:
            recente = _mais_recente(client, spec["fonte"], spec["coluna_data"], d1)
            linhas = _contar(client, spec["fonte"], spec["coluna_data"], d1)
        except Exception as exc:
            raise RuntimeError(f"consulta a {spec['fonte']} falhou: {exc}") from exc

        idade = (d1 - recente).days + 1 if recente else None
        # `recente` já vem limitada a D-1, então a diferença nunca é negativa.
        fresco = recente is not None and (d1 - recente).days <= (max_idade_dias - 1)

        resultados.append({
            "fonte": spec["fonte"],
            "descricao": spec["descricao"],
            "esperado": d1.isoformat(),
            "mais_recente": recente.isoformat() if recente else None,
            "idade_dias": idade,
            "linhas_d1": linhas,
            "fresco": fresco,
            "critico": spec["critico"],
            "remediacao": spec["remediacao"].format(d1=d1.isoformat()),
        })

        if not fresco and spec["critico"]:
            if recente is None:
                bloqueios.append(f"{spec['fonte']} está vazia")
            else:
                atraso = (d1 - recente).days
                bloqueios.append(
                    f"{spec['fonte']} está em {recente:%d/%m} "
                    f"(D-{atraso + 1}, {atraso} dia(s) de defasagem)"
                )

    apto = not bloqueios
    return {
        "gerado_em": now_brt().isoformat(),
        "data_briefing": hoje.isoformat(),
        "data_referencia": d1.isoformat(),
        "apto": apto,
        "fontes": resultados,
        "bloqueios": bloqueios,
        "recomendacao": (
            "Dado de D-1 completo — pode publicar."
            if apto else
            "NÃO publique número de preço como se fosse de ontem. Ou rode a "
            "remediação da fonte bloqueada e espere, ou publique carimbando "
            "explicitamente a data real do dado no material."
        ),
    }


def _curar(relatorio: Dict[str, Any]) -> bool:
    """Tenta consertar as fontes bloqueadas antes de reprovar a publicação.

    Só executa remediações POSIX idempotentes que não dependem de browser nem
    de sessão logada — na prática, o reimport do PriceTrack. Coleta de
    marketplace exige a máquina certa e não se dispara às cegas de dentro de
    um portão.

    Args:
        relatorio: Saída de ``avaliar()``.

    Returns:
        True se alguma remediação foi executada com sucesso.
    """
    curou = False
    for fonte in relatorio["fontes"]:
        if fonte["fresco"] or not fonte["critico"]:
            continue
        if fonte["fonte"] != "pricetrack_daily":
            logger.info(
                f"[Portão] {fonte['fonte']} precisa de intervenção manual: "
                f"{fonte['remediacao']}"
            )
            continue
        d1 = relatorio["data_referencia"]
        comando = [
            sys.executable, "scripts/pricetrack_api_import.py",
            "--start", d1, "--end", d1, "--force",
        ]
        logger.warning(f"[Portão] Curando {fonte['fonte']}: {' '.join(comando)}")
        try:
            resultado = subprocess.run(comando, timeout=3000, check=False)
            if resultado.returncode == 0:
                curou = True
                logger.success(f"[Portão] {fonte['fonte']} reimportada.")
            else:
                logger.error(f"[Portão] Reimport falhou (exit {resultado.returncode}).")
        except Exception as exc:
            logger.error(f"[Portão] Reimport não executou: {exc}")
    return curou


def _imprimir(relatorio: Dict[str, Any]) -> None:
    """Relatório legível no terminal."""
    linha = "=" * 68
    print(f"\n{linha}")
    print(f"  PORTÃO DO BRIEFING — referência D-1 = {relatorio['data_referencia']}")
    print(linha)
    for fonte in relatorio["fontes"]:
        icone = "✅" if fonte["fresco"] else ("🔴" if fonte["critico"] else "🟡")
        idade = f"{fonte['idade_dias']} dia(s)" if fonte["idade_dias"] else "vazia"
        print(f"  {icone} {fonte['fonte']:<20} mais recente: {fonte['mais_recente'] or '—'}  ({idade})")
        print(f"      {fonte['descricao']} — {fonte['linhas_d1']} linha(s) em D-1")
        if not fonte["fresco"]:
            print(f"      ↳ {fonte['remediacao']}")
    print(f"\n  {'APTO A PUBLICAR' if relatorio['apto'] else 'BLOQUEADO'}")
    for bloqueio in relatorio["bloqueios"]:
        print(f"    • {bloqueio}")
    print(f"  {relatorio['recomendacao']}")
    print(f"{linha}\n")


def main(argv: Optional[List[str]] = None) -> int:
    """Ponto de entrada da CLI."""
    parser = argparse.ArgumentParser(
        description="Verifica se o dado de D-1 está fresco antes do briefing.",
    )
    parser.add_argument("--data", default=None, help="dia do briefing (YYYY-MM-DD, default: hoje)")
    parser.add_argument(
        "--max-idade-dias", type=int, default=1,
        help="defasagem tolerada em dias (1 = precisa ser de ontem)",
    )
    parser.add_argument("--json", action="store_true", help="imprime o contrato JSON no stdout")
    parser.add_argument(
        "--curar", action="store_true",
        help="tenta reimportar a fonte bloqueada e reavalia antes de reprovar",
    )
    args = parser.parse_args(argv)

    try:
        hoje = datetime.strptime(args.data, "%Y-%m-%d").date() if args.data else None
    except ValueError:
        parser.error(f"--data deve estar no formato YYYY-MM-DD (recebi {args.data!r})")
        return 2  # pragma: no cover - parser.error já encerra o processo

    try:
        relatorio = avaliar(hoje, args.max_idade_dias)
        if args.curar and not relatorio["apto"] and _curar(relatorio):
            relatorio = avaliar(hoje, args.max_idade_dias)
            relatorio["curado"] = True
    except RuntimeError as exc:
        if args.json:
            print(json.dumps(
                {"apto": False, "erro": str(exc), "motivo": "verificacao_indisponivel"},
                ensure_ascii=False,
            ))
        else:
            logger.error(f"[Portão] Não deu para verificar: {exc}")
        return 3

    if args.json:
        print(json.dumps(relatorio, ensure_ascii=False, indent=2))
    else:
        _imprimir(relatorio)

    return 0 if relatorio["apto"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
