#!/usr/bin/env python3
"""
scripts/collect_bestsellers.py — Coleta e análise diária de "Mais Vendidos" RAC.

Substitui a rotina manual de exportar listas pelo Web Scraper. Coleta as seis
listas ordenadas por vendas, valida, grava a série e escreve o brief.

CADÊNCIA OBRIGATÓRIA: todo dia útil, entre 9h e 10h. O ranking da Amazon é
recalculado de hora em hora — comparar sábado 22h com segunda 9h é ruído, não
movimento competitivo. Coletar sempre as seis plataformas: faltou uma, o brief
registra a ausência em vez de comparar bases desiguais.

Uso:
    # Coleta do dia (todas as plataformas ativas) + brief
    python scripts/collect_bestsellers.py

    # Uma plataforma, browser visível (depuração de seletores)
    python scripts/collect_bestsellers.py --plataformas amazon --no-headless

    # Relatórios agregados (não coletam nada — leem o histórico)
    python scripts/collect_bestsellers.py --relatorio semanal
    python scripts/collect_bestsellers.py --relatorio mensal --ultimos 6

    # Backfill do histórico manual (exports .xlsx do Web Scraper)
    python scripts/collect_bestsellers.py --importar-xlsx ./uploads --data 2026-08-10

Códigos de saída:
    0  coleta concluída (mesmo com avisos)
    1  nenhuma plataforma produziu dados
    2  erro de configuração/argumento
"""

import argparse
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bestsellers import metrics, report, storage, validate  # noqa: E402
from bestsellers.config import (  # noqa: E402
    HISTORICO_PATH,
    OUTPUT_DIR,
    SOURCES,
    TOP_N,
    resolver_keys,
)
from bestsellers.sources import SOURCE_CLASSES  # noqa: E402
from config import BRANDS  # noqa: E402
from utils.text import now_brt  # noqa: E402


def _configurar_log(nivel: str) -> None:
    Path("logs").mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, level=nivel, colorize=True)
    logger.add(
        Path("logs") / f"bestsellers_{now_brt():%Y%m%d_%H%M%S}.log",
        level="DEBUG",
        encoding="utf-8",
        rotation="10 MB",
    )


# ---------------------------------------------------------------------------
# Coleta
# ---------------------------------------------------------------------------

def coletar(
    chaves: List[str], paginas: int, headless: bool, run_id: str, data: str, horario: str
):
    """
    Executa a coleta de cada plataforma.

    Uma plataforma que falha não derruba as outras: ela simplesmente não
    produz linhas e o motivo entra no mapa de falhas, que o relatório
    transforma em AUSÊNCIA explícita.

    Returns:
        Tupla (DataFrame da coleta, mapa plataforma → motivo da falha).
    """
    linhas: List[Dict] = []
    falhas: Dict[str, str] = {}

    for chave in chaves:
        classe = SOURCE_CLASSES.get(chave)
        if classe is None:
            falhas[chave] = "sem coletor implementado"
            logger.error(f"[{chave}] Sem coletor implementado — pulando.")
            continue

        spec = SOURCES[chave]
        logger.info(f"━━━ {spec.nome} · {spec.mecanica} ━━━")
        try:
            with classe(headless=headless) as fonte:
                itens = fonte.coletar(paginas=paginas)
                if not itens:
                    falhas[chave] = fonte.falha or "lista vazia"
                    continue
                for item in itens:
                    linhas.append(item.to_row(
                        data=data,
                        horario=horario,
                        run_id=run_id,
                        spec=spec,
                        url_coleta=fonte.url_coleta(),
                        endpoint=fonte.endpoint or fonte.url_coleta(),
                    ))
        except Exception as exc:
            falhas[chave] = f"{type(exc).__name__}: {exc}"
            logger.error(f"[{spec.nome}] Coleta abortada: {exc}")

    return storage.to_dataframe(linhas), falhas


# ---------------------------------------------------------------------------
# Modos de execução
# ---------------------------------------------------------------------------

def _bater_ponto(status: str, linhas: Optional[int] = None, detalhe: str = "") -> None:
    """Registra a coleta no livro-razão de execução, se ela foi agendada.

    A tarefa RAC_Bestsellers exporta ``RAC_JOB_ID=local_bestsellers``; sem a
    variável a função é no-op, para que uma execução manual não vire promessa
    que ninguém fez. Nunca levanta: observabilidade não derruba coleta.

    Args:
        status: STARTED | SUCCESS | PARTIAL | FAILED.
        linhas: Linhas gravadas no histórico do dia.
        detalhe: Uma linha de diagnóstico (ex.: plataformas que falharam).
    """
    job_id = os.getenv("RAC_JOB_ID", "").strip()
    if not job_id:
        return
    try:
        from utils.heartbeat import bater

        bater(job_id, status, rows=linhas, detail=detalhe)
    except Exception as exc:  # pragma: no cover - best effort
        logger.debug(f"[Heartbeat] Batida '{status}' não registrada: {exc}")


def executar_coleta(args: argparse.Namespace) -> int:
    try:
        chaves = resolver_keys(args.plataformas)
    except ValueError as exc:
        logger.error(str(exc))
        return 2

    _bater_ponto("STARTED", detalhe=f"{len(chaves)} lista(s)")

    agora = now_brt()
    data = args.data or agora.strftime("%Y-%m-%d")
    run_id = str(uuid.uuid4())
    logger.info(
        f"Coleta de mais vendidos · {data} · {len(chaves)} plataforma(s) · "
        f"run_id={run_id}"
    )

    df, falhas = coletar(
        chaves, args.paginas, not args.no_headless, run_id, data, agora.strftime("%H:%M")
    )
    if not len(df):
        motivos = "; ".join(f"{k}: {v}" for k, v in falhas.items()) or "desconhecido"
        logger.error(f"Nenhuma plataforma produziu dados. Motivos: {motivos}")
        _bater_ponto("FAILED", 0, f"nenhuma lista coletou — {motivos}")
        return 1

    # CSV do dia: evidência BRUTA, com tudo o que foi coletado — inclusive o
    # que a validação vai reprovar. É o arquivo que se abre para entender por
    # que uma plataforma caiu, então gravá-lo antes de qualquer outra coisa
    # garante que nada depois daqui custe a coleta do dia.
    storage.exportar_csv(df, data, args.output)

    ocorrencias = validate.validar(df, esperadas=chaves, falhas=falhas)
    for ocorrencia in ocorrencias:
        registrar = {
            validate.NIVEL_QUARENTENA: logger.error,
            validate.NIVEL_ERRO: logger.error,
        }.get(ocorrencia.nivel, logger.warning)
        registrar(str(ocorrencia))

    # Série histórica e banco recebem apenas o que passou nos portões. Uma
    # lista sem o parâmetro de ordenação não é de mais vendidos: guardá-la
    # contamina a série para sempre — quem lê o master meses depois não tem
    # como saber que aquele dia mediu relevância.
    analisado = validate.aplicar_quarentena(df, ocorrencias)
    if not len(analisado):
        logger.error(
            "Todas as plataformas foram reprovadas no portão de ordenação. "
            "Nada gravado no histórico — o CSV bruto do dia ficou salvo para "
            "diagnóstico."
        )
        _bater_ponto("FAILED", 0, "todas as listas reprovadas no portão de ordenação")
        return 1

    master = storage.atualizar_historico(analisado, args.historico)

    if not args.sem_supabase:
        storage.upload_supabase(analisado)

    # O brief compara o dia com o histórico ANTERIOR a ele — o master já
    # contém o dia atual, então ele é removido da base de comparação.
    preparado_hoje = metrics.preparar(analisado)
    preparado_master = metrics.preparar(master)
    historico_anterior = preparado_master[
        preparado_master["data"] < pd.to_datetime(data)
    ] if len(preparado_master) else preparado_master

    texto = report.brief_diario(
        preparado_hoje,
        historico_anterior,
        ocorrencias,
        marcas_monitoradas=BRANDS,
        n=args.top,
    )
    storage.salvar_relatorio(texto, data, args.output)

    if args.notificar:
        _notificar(report.resumo_telegram(preparado_hoje, ocorrencias, n=args.top))

    _resumo_console(preparado_hoje, args.top)

    # PARTIAL quando alguma lista esperada não entrou na série do dia. Duas
    # formas de faltar, e só a primeira aparece em `falhas`:
    #   1. a fonte falhou na coleta (bloqueio, timeout);
    #   2. a fonte coletou e foi REPROVADA no portão de ordenação — nesse caso
    #      `falhas` fica vazio e a série do dia sai incompleta em silêncio.
    # A verdade final é quem sobreviveu à quarentena, então é `analisado` que
    # decide, não a lista de falhas de rede.
    coletadas = set(analisado["plataforma"].dropna().unique())
    ausentes = sorted(set(chaves) - coletadas)
    motivos = []
    if falhas:
        motivos.append("falharam: " + ", ".join(sorted(falhas)))
    if ausentes:
        motivos.append("fora da série: " + ", ".join(ausentes))

    _bater_ponto(
        "PARTIAL" if (falhas or ausentes) else "SUCCESS",
        int(len(analisado)),
        " | ".join(motivos),
    )
    return 0


def executar_relatorio(args: argparse.Namespace) -> int:
    historico = metrics.preparar(storage.carregar_historico(args.historico))
    if not len(historico):
        logger.error(
            f"Histórico vazio ({args.historico}). Rode a coleta ao menos uma vez."
        )
        return 1

    periodo = (
        metrics.PERIODO_SEMANAL if args.relatorio == "semanal" else metrics.PERIODO_MENSAL
    )
    texto = report.relatorio_periodico(
        historico, periodo, ultimos=args.ultimos, n=args.top
    )
    caminho = storage.caminho_relatorio(
        f"bestsellers_{args.relatorio}_{now_brt():%Y%m%d}.md", args.output
    )
    caminho.write_text(texto, encoding="utf-8")
    logger.success(f"Relatório {args.relatorio}: {caminho}")
    print("\n" + texto)
    return 0


def executar_import(args: argparse.Namespace) -> int:
    from bestsellers.importer_xlsx import importar_diretorio

    if not args.data:
        logger.error("--importar-xlsx exige --data YYYY-MM-DD (o Web Scraper não a grava).")
        return 2

    df = importar_diretorio(args.importar_xlsx, args.data)
    if not len(df):
        logger.error("Nenhuma linha importada.")
        return 1

    storage.exportar_csv(df, args.data, args.output)

    # Mesma ordem da coleta: validar antes de gravar. O histórico manual não
    # é exceção — um export cuja URL não prova a ordenação por vendas entra
    # na série como se fosse mais vendidos e some no meio dela.
    ocorrencias = validate.validar(df, esperadas=sorted(set(df["plataforma"])))
    for ocorrencia in ocorrencias:
        logger.warning(str(ocorrencia))

    analisado = validate.aplicar_quarentena(df, ocorrencias)
    if not len(analisado):
        logger.error(
            "Nenhum arquivo passou no portão de ordenação — nada importado. "
            "Confira a URL registrada em `web_scraper_start_url`."
        )
        return 1

    storage.atualizar_historico(analisado, args.historico)
    logger.success(
        f"Importadas {len(analisado)} posições de "
        f"{analisado['plataforma'].nunique()} plataforma(s) para {args.data}."
    )
    return 0


# ---------------------------------------------------------------------------
# Saídas auxiliares
# ---------------------------------------------------------------------------

def _resumo_console(hoje: pd.DataFrame, n: int) -> None:
    kpi = metrics.kpi_topo(hoje, n=n)
    if not len(kpi):
        logger.warning("Nenhuma posição no escopo do KPI (split hi-wall).")
        return
    print(f"\n% do top {n} ocupado pelo grupo Midea:")
    for _, linha in kpi.iterrows():
        nome = SOURCES[linha["plataforma"]].nome if linha["plataforma"] in SOURCES else linha["plataforma"]
        pct = "n/d" if pd.isna(linha["pct_topN"]) else f"{linha['pct_topN']:5.1f}%"
        melhor = "ausente" if pd.isna(linha["melhor_rank"]) else f"#{int(linha['melhor_rank'])}"
        print(f"  {nome:<16} {pct}  (melhor posição: {melhor}, {linha['itens']} itens)")


def _notificar(mensagem: str) -> None:
    try:
        from utils.n8n_notify import _send_direct_telegram

        if _send_direct_telegram(mensagem):
            logger.success("Notificação enviada.")
        else:
            logger.warning("Notificação não enviada (token/chat ausente?).")
    except Exception as exc:
        logger.warning(f"Falha ao notificar: {exc}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _data_iso(valor: str) -> str:
    """
    Valida `--data` como data ISO.

    A data é a CHAVE da série: um valor livre (`ontem`, `10/08/2026`) seria
    gravado no CSV e no master antes de qualquer falha aparecer, criando uma
    partição que nenhuma comparação alcança. O import, então, terminava com
    sucesso.

    Raises:
        argparse.ArgumentTypeError: quando o valor não é YYYY-MM-DD válido.
    """
    try:
        data = datetime.strptime(valor, "%Y-%m-%d")
        # `strftime("%Y")` NÃO preenche com zero à esquerda: um ano abaixo de
        # 1000 sairia como "1-01-01" — exatamente a chave malformada que esta
        # validação existe para impedir.
        return f"{data.year:04d}-{data.month:02d}-{data.day:02d}"
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"data inválida: {valor!r}. Use o formato YYYY-MM-DD (ex: 2026-08-10)."
        )


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Coleta e análise diária das listas de mais vendidos RAC.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Plataformas disponíveis: " + ", ".join(SOURCES) + "\n"
            "Regra dura: ranking é ordinal — não vira share de mercado."
        ),
    )
    parser.add_argument(
        "--plataformas", nargs="+", default=["all"],
        help="Chaves das listas a coletar (padrão: all).",
    )
    parser.add_argument(
        "--paginas", type=int, default=1,
        help="Páginas da lista a percorrer (padrão: 1).",
    )
    parser.add_argument(
        "--data", default=None, type=_data_iso,
        help="Data da coleta (YYYY-MM-DD). Padrão: hoje em BRT.",
    )
    parser.add_argument(
        "--top", type=int, default=TOP_N,
        help=f"Tamanho do topo usado no KPI (padrão: {TOP_N}).",
    )
    parser.add_argument("--no-headless", action="store_true", help="Browser visível.")
    parser.add_argument(
        "--sem-supabase", action="store_true", help="Não enviar ao Supabase."
    )
    parser.add_argument(
        "--notificar", action="store_true", help="Enviar resumo por Telegram."
    )
    parser.add_argument(
        "--relatorio", choices=["semanal", "mensal"], default=None,
        help="Gera o relatório agregado a partir do histórico (não coleta).",
    )
    parser.add_argument(
        "--ultimos", type=int, default=8,
        help="Quantos períodos exibir no relatório agregado (padrão: 8).",
    )
    parser.add_argument(
        "--importar-xlsx", default=None, metavar="DIR",
        help="Importa exports .xlsx do Web Scraper para o histórico.",
    )
    parser.add_argument("--historico", default=HISTORICO_PATH)
    parser.add_argument("--output", default=OUTPUT_DIR)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    _configurar_log(args.log_level)

    if args.importar_xlsx:
        return executar_import(args)
    if args.relatorio:
        return executar_relatorio(args)
    return executar_coleta(args)


if __name__ == "__main__":
    raise SystemExit(main())
