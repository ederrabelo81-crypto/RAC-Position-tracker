"""
scripts/recover_from_artifacts.py — Resgata coletas presas nos artifacts do Actions.

Para que serve
--------------
Quando a coleta roda mas a gravação falha, o CSV ainda é publicado como
artifact do GitHub Actions. O dado existe — só está num zip com prazo de
validade (``retention-days: 30`` em ``collect.yml``). Este script baixa esses
zips, extrai os CSVs e os carrega no **histórico frio** (e, opcionalmente, no
Supabase), fechando os buracos da série.

Foi escrito para dois incidentes reais e consecutivos:

* **16–25/07/2026** — Supabase restrito por cota (HTTP 402) recusou toda
  escrita. Os jobs terminaram verdes.
* **26–31/07/2026** — ``UnboundLocalError`` em ``main.py`` matava o processo
  logo depois do CSV, antes do histórico e do upload. Doze jobs vermelhos.

Em ambos os casos o CSV foi gerado e sobreviveu no artifact. Sem este caminho,
recuperar 15 dias significaria baixar 24 zips à mão.

⚠️ **Artifact expira.** Os do dia 17/07 somem por volta de 16/08/2026. Rodar
isto é urgente enquanto a janela existe.

Autenticação
------------
Precisa de um token do GitHub com escopo de leitura do repositório, em
``GITHUB_TOKEN`` ou ``GH_TOKEN`` (ou via ``--token``). Um *fine-grained token*
com permissão de leitura em Actions basta.

Uso
---
    # O que existe para resgatar (não baixa nada)
    python scripts/recover_from_artifacts.py --list

    # Resgata um intervalo para o histórico frio
    python scripts/recover_from_artifacts.py --start 2026-07-16 --end 2026-07-31

    # Simula: baixa e conta as linhas, sem gravar
    python scripts/recover_from_artifacts.py --start 2026-07-16 --dry-run

    # Também repõe no Supabase (a janela quente do dashboard)
    python scripts/recover_from_artifacts.py --start 2026-07-16 --supabase

Note:
    A gravação é idempotente: o ``run_id`` da partição é derivado do nome do
    CSV, então reimportar o mesmo artifact sobrescreve a mesma partição em vez
    de duplicar o dia.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from loguru import logger

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from utils.history import get_store, write_records  # noqa: E402
from utils.text import BRT  # noqa: E402

_API = "https://api.github.com"
_DEFAULT_REPO = "ederrabelo81-crypto/RAC-Position-tracker"

#: Só os artifacts da coleta. Os de log (`rac-logs-*`) não têm CSV.
_ARTIFACT_PREFIX = "rac-coleta-"

_TIMEOUT = 60


class RecoveryError(RuntimeError):
    """Falha de configuração ou de comunicação com a API do GitHub."""


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------
def _token(explicit: Optional[str] = None) -> str:
    """Token do GitHub, do argumento ou do ambiente.

    Raises:
        RecoveryError: se nenhum token foi encontrado.
    """
    tok = (explicit or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
    if not tok:
        raise RecoveryError(
            "Token do GitHub ausente — exporte GITHUB_TOKEN (ou use --token). "
            "Um fine-grained token com leitura de Actions é suficiente."
        )
    return tok


def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _list_artifacts(repo: str, token: str) -> List[Dict[str, Any]]:
    """Lista todos os artifacts do repositório, paginando até o fim.

    Returns:
        Lista crua dos artifacts, como a API devolve.
    """
    artifacts: List[Dict[str, Any]] = []
    page = 1
    while True:
        resp = requests.get(
            f"{_API}/repos/{repo}/actions/artifacts",
            headers=_headers(token),
            params={"per_page": 100, "page": page},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            raise RecoveryError(
                f"GET /actions/artifacts devolveu HTTP {resp.status_code}: "
                f"{resp.text[:200]}"
            )
        lote = resp.json().get("artifacts", [])
        artifacts.extend(lote)
        if len(lote) < 100:
            return artifacts
        page += 1


def _artifact_day(artifact: Dict[str, Any]) -> Optional[date]:
    """Dia **BRT** da coleta, convertido do ``created_at`` (UTC) do run.

    A conversão não é cosmética. O turno Fechamento dispara às ``0 0 * * *``
    (00:00 UTC = 21:00 BRT), então o artifact da coleta de 31/07 nasce com
    ``created_at`` já em 01/08. Cortar pela data UTC faria
    ``--end 2026-07-31`` descartar em silêncio justamente o Fechamento do
    último dia do intervalo — e é o dia do incidente que se quer resgatar.

    Args:
        artifact: Artifact como a API devolve (usa ``created_at``).

    Returns:
        A data BRT do run, ou ``None`` se ``created_at`` vier ilegível.
    """
    raw = str(artifact.get("created_at") or "")
    try:
        momento = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(BRT).date()


def _selecionar(
    artifacts: List[Dict[str, Any]],
    start: Optional[date],
    end: Optional[date],
) -> List[Dict[str, Any]]:
    """Filtra artifacts de coleta, não expirados, dentro do intervalo.

    Args:
        artifacts: Lista crua da API.
        start: Primeiro dia (inclusivo) ou ``None``.
        end: Último dia (inclusivo) ou ``None``.

    Returns:
        Os artifacts elegíveis, do mais antigo para o mais novo.
    """
    escolhidos = []
    for art in artifacts:
        if not str(art.get("name", "")).startswith(_ARTIFACT_PREFIX):
            continue
        if art.get("expired"):
            logger.warning(
                f"{art.get('name')}: EXPIRADO em {art.get('expires_at', '?')[:10]} "
                "— esse dia não é mais recuperável por aqui."
            )
            continue
        dia = _artifact_day(art)
        if dia is None:
            continue
        if start is not None and dia < start:
            continue
        if end is not None and dia > end:
            continue
        escolhidos.append(art)
    return sorted(escolhidos, key=lambda a: a.get("created_at", ""))


def _baixar_csvs(art: Dict[str, Any], token: str, destino: Path) -> List[Path]:
    """Baixa o zip do artifact e extrai os CSVs para ``destino``.

    Cada artifact ganha o próprio subdiretório. Dois runs que começam no mesmo
    minuto geram CSVs de nome idêntico (``..._YYYYMMDD_HHMM.csv``); num destino
    compartilhado o segundo sobrescreveria o primeiro **antes** da carga, e a
    coleta mais antiga sumiria sem nenhum aviso.

    Args:
        art: Artifact da API (precisa de ``archive_download_url``).
        token: Token do GitHub.
        destino: Diretório raiz dos CSVs extraídos (criado se preciso).

    Returns:
        Caminhos dos CSVs extraídos, dentro de ``destino/<artifact>/``.

    Raises:
        RecoveryError: se o download falhar ou o zip vier corrompido.
    """
    url = art.get("archive_download_url")
    if not url:
        raise RecoveryError(f"{art.get('name')}: sem archive_download_url.")

    resp = requests.get(url, headers=_headers(token), timeout=_TIMEOUT * 5)
    if resp.status_code != 200:
        raise RecoveryError(
            f"{art.get('name')}: download devolveu HTTP {resp.status_code}."
        )

    # `id` é único por artifact; o nome (`rac-coleta-<turno>-<run_id>`) serve de
    # reserva e também é único por run.
    sub = str(art.get("id") or art.get("name") or "sem-id")
    pasta = destino / f"artifact-{sub}"
    pasta.mkdir(parents=True, exist_ok=True)
    extraidos: List[Path] = []
    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            for nome in zf.namelist():
                if not nome.lower().endswith(".csv"):
                    continue  # o artifact também carrega os .parquet do runner
                # `Path(nome).name` descarta o diretório de dentro do zip, mas
                # preserva o nome do CSV: é dele que sai o run_id determinístico
                # da partição, e ele precisa bater com o de `history_cli
                # import-csv` para reimportar o mesmo dia ser idempotente.
                alvo = pasta / Path(nome).name
                alvo.write_bytes(zf.read(nome))
                extraidos.append(alvo)
    except zipfile.BadZipFile as exc:
        raise RecoveryError(f"{art.get('name')}: zip inválido ({exc}).") from exc

    return extraidos


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------
def _carregar(
    csvs: List[Path],
    dry_run: bool,
    para_supabase: bool,
) -> Dict[str, int]:
    """Carrega os CSVs no histórico frio (e opcionalmente no Supabase).

    A ordem da carga importa: CSVs de mesmo nome derivam o mesmo ``run_id`` e
    caem na mesma partição, então quem grava por último prevalece. Ordenar pelo
    nome do arquivo mantém o log legível dia a dia (o nome carrega data e hora)
    — e, como ``sorted`` é estável, empates preservam a ordem de ``csvs``, que
    ``main`` monta por ``created_at`` crescente. Ordenar pelo caminho inteiro
    quebraria isso: ``artifact-10`` viria antes de ``artifact-9``.

    Args:
        csvs: Arquivos extraídos, em ordem cronológica de artifact.
        dry_run: Só conta as linhas, não grava.
        para_supabase: Também repõe as linhas no banco.

    Returns:
        Contagens ``{linhas, particoes, falhas}``.
    """
    from scripts.history_cli import _derive_run_id
    from scripts.upload_csv import _load_csv

    store = None if dry_run else get_store()
    totais = {"linhas": 0, "particoes": 0, "falhas": 0}

    for csv in sorted(csvs, key=lambda p: p.name):
        # Um CSV torto (zip truncado, coluna faltando) não pode abortar o
        # resgate: os artifacts já foram baixados e os dias seguintes ficariam
        # sem carga nenhuma, com a janela de retenção correndo.
        try:
            registros = _load_csv(csv)
        except Exception as exc:
            logger.error(f"{csv.name}: falha ao ler o CSV ({exc}) — pulado.")
            totais["falhas"] += 1
            continue

        if not registros:
            logger.warning(f"{csv.name}: 0 registros — pulado.")
            continue

        run_id = _derive_run_id(csv)
        if dry_run:
            logger.info(
                f"[DRY-RUN] {csv.name}: {len(registros)} registros "
                f"(run_id={run_id[:8]})"
            )
            totais["linhas"] += len(registros)
            continue

        try:
            keys = write_records(registros, run_id=run_id, store=store)
        except Exception as exc:
            logger.error(f"{csv.name}: falha ao gravar no histórico ({exc}).")
            totais["falhas"] += 1
            continue

        if not keys:
            logger.error(f"{csv.name}: nenhuma partição gravada.")
            totais["falhas"] += 1
            continue
        logger.success(
            f"{csv.name}: {len(registros)} linhas → {len(keys)} partição(ões)"
        )
        totais["linhas"] += len(registros)
        totais["particoes"] += len(keys)

        if para_supabase:
            try:
                from utils.supabase_client import upload_to_supabase

                if not upload_to_supabase(registros, run_id=run_id):
                    logger.warning(
                        f"{csv.name}: histórico OK, Supabase recusou "
                        "(cota/credencial?) — o dado está salvo no frio."
                    )
            except Exception as exc:
                logger.warning(f"{csv.name}: upload ao Supabase falhou: {exc}")

    return totais


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_day(raw: str) -> date:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"data inválida: {raw!r} — use YYYY-MM-DD"
        ) from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resgata coletas dos artifacts do GitHub Actions.",
    )
    parser.add_argument("--repo", default=_DEFAULT_REPO,
                        help=f"owner/repo (padrão: {_DEFAULT_REPO})")
    parser.add_argument("--token", default=None,
                        help="Token do GitHub (padrão: $GITHUB_TOKEN/$GH_TOKEN)")
    parser.add_argument("--start", type=_parse_day, default=None,
                        help="Primeiro dia a resgatar (YYYY-MM-DD)")
    parser.add_argument("--end", type=_parse_day, default=None,
                        help="Último dia a resgatar (YYYY-MM-DD)")
    parser.add_argument("--list", action="store_true", dest="apenas_listar",
                        help="Só lista o que existe, sem baixar")
    parser.add_argument("--dry-run", action="store_true",
                        help="Baixa e conta, mas não grava nada")
    parser.add_argument("--supabase", action="store_true",
                        help="Também repõe as linhas no Supabase")
    parser.add_argument("--dest", default=None,
                        help="Diretório dos CSVs extraídos "
                             "(padrão: output/recuperados)")
    args = parser.parse_args()

    try:
        token = _token(args.token)
        artifacts = _list_artifacts(args.repo, token)
    except RecoveryError as exc:
        logger.error(str(exc))
        return 2
    except requests.RequestException as exc:
        logger.error(f"Falha de rede falando com a API do GitHub: {exc}")
        return 2

    elegiveis = _selecionar(artifacts, args.start, args.end)
    if not elegiveis:
        logger.warning(
            "Nenhum artifact de coleta no intervalo pedido "
            "(já expiraram, ou o filtro está estreito demais)."
        )
        return 1

    logger.info(f"{len(elegiveis)} artifact(s) elegível(is):")
    for art in elegiveis:
        dia = _artifact_day(art)
        logger.info(
            f"  {art['name']} — {dia.isoformat() if dia else '?'} (BRT), "
            f"{art.get('size_in_bytes', 0) / 1024:.0f} KB, "
            f"expira em {art.get('expires_at', '?')[:10]}"
        )
    if args.apenas_listar:
        return 0

    destino = Path(args.dest) if args.dest else _ROOT / "output" / "recuperados"
    csvs: List[Path] = []
    vistos: Dict[str, str] = {}  # nome do CSV → artifact que o trouxe
    for art in elegiveis:
        try:
            baixados = _baixar_csvs(art, token, destino)
        except (RecoveryError, requests.RequestException) as exc:
            # Um artifact ilegível não pode abortar o resgate dos outros 23.
            logger.error(f"{art.get('name')}: {exc}")
            continue
        if not baixados:
            logger.warning(f"{art.get('name')}: nenhum CSV dentro do zip.")
            continue
        for novo in baixados:
            # O run_id sai do nome do CSV: dois artifacts com o mesmo nome caem
            # na mesma partição e o último grava por cima. Os arquivos estão
            # preservados em disco (um subdiretório por artifact), mas o
            # conflito precisa aparecer no log em vez de sumir na carga.
            anterior = vistos.get(novo.name)
            if anterior is not None:
                logger.warning(
                    f"{novo.name}: mesmo nome de CSV em {anterior} e "
                    f"{art.get('name')} — mesma partição, prevalece o artifact "
                    "mais recente. Confira se são a mesma coleta."
                )
            vistos[novo.name] = str(art.get("name"))
        logger.info(f"{art['name']}: {len(baixados)} CSV(s) extraído(s).")
        csvs.extend(baixados)

    if not csvs:
        logger.error("Nenhum CSV recuperado.")
        return 1

    totais = _carregar(csvs, args.dry_run, args.supabase)
    verbo = "seriam gravadas" if args.dry_run else "gravadas"
    logger.success(
        f"{totais['linhas']:,} linhas {verbo} em {totais['particoes']} "
        f"partição(ões) · {totais['falhas']} falha(s). "
        f"CSVs em {destino}".replace(",", ".")
    )
    if not args.dry_run:
        logger.info("Confira o resultado com: python scripts/history_cli.py stats")
    return 1 if totais["falhas"] else 0


if __name__ == "__main__":
    sys.exit(main())
