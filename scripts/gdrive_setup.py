"""
scripts/gdrive_setup.py — Autoriza o projeto a gravar o histórico no Drive.

Roda **uma vez**, no notebook. Abre o consentimento do Google no navegador,
troca o código por um *refresh token* de longa duração e imprime as três linhas
que faltam no `.env`. Também cria (ou encontra) a pasta do histórico e mostra o
`GDRIVE_FOLDER_ID`.

Por que OAuth de usuário e não conta de serviço
-----------------------------------------------
Conta de serviço não tem cota de armazenamento própria no "Meu Drive" — o
upload falha com ``storageQuotaExceeded`` mesmo com a pasta compartilhada.
Numa conta pessoal (@gmail.com) o caminho que funciona é OAuth do usuário: os
arquivos são dele e ocupam os 15 GB gratuitos da conta. Em Google Workspace
com Shared Drive, a conta de serviço funciona — nesse caso use
``GDRIVE_SERVICE_ACCOUNT_JSON`` e pule este script.

Pré-requisito (uma vez, no Google Cloud Console)
------------------------------------------------
1. Crie um projeto em https://console.cloud.google.com/
2. Ative a **Google Drive API**.
3. Em *Credenciais* → *Criar credenciais* → **ID do cliente OAuth** → tipo
   **App para computador**. Baixe o JSON.
4. Rode:  ``python scripts/gdrive_setup.py --client-secrets caminho/do.json``

Uso
---
    python scripts/gdrive_setup.py --client-secrets client_secret.json
    python scripts/gdrive_setup.py --check          # valida o .env atual

O ``--client-secrets`` aceita curinga ou a pasta do download, porque o nome que
o Google dá ao arquivo é comprido e a página do Console mostra o *client id* —
parecido, mas sem o ``.json`` do fim::

    python scripts/gdrive_setup.py --client-secrets "%USERPROFILE%\\Downloads"
    python scripts/gdrive_setup.py --client-secrets "~/Downloads/client_secret*.json"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from loguru import logger

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
_FOLDER_MIME = "application/vnd.google-apps.folder"
_DEFAULT_FOLDER = "RAC Position Tracker - Historico"


def _require_google_libs() -> None:
    """Falha cedo e com instrução, em vez de ImportError cru no meio do fluxo."""
    faltando = []
    try:
        import google_auth_oauthlib  # noqa: F401
    except ImportError:
        faltando.append("google-auth-oauthlib")
    try:
        import googleapiclient  # noqa: F401
    except ImportError:
        faltando.append("google-api-python-client")
    if faltando:
        logger.error(
            f"Dependência ausente: {', '.join(faltando)}. "
            f"Execute: pip install {' '.join(faltando)}"
        )
        sys.exit(1)


def _ensure_folder(service, name: str) -> str:
    """Id da pasta do histórico, criando-a na raiz do Drive se não existir.

    A busca é restrita a ``'root' in parents`` para casar com o local onde a
    pasta é criada: sem isso, uma pasta de mesmo nome em qualquer outro ponto
    do Drive seria escolhida e o histórico iria para o lugar errado.
    """
    query = (
        f"name = '{name}' and mimeType = '{_FOLDER_MIME}' "
        "and 'root' in parents and trashed = false"
    )
    found = service.files().list(
        q=query, fields="files(id, name)", pageSize=10,
    ).execute().get("files", [])
    if found:
        logger.info(f"Pasta encontrada: '{name}' ({found[0]['id']})")
        return found[0]["id"]

    created = service.files().create(
        body={"name": name, "mimeType": _FOLDER_MIME}, fields="id",
    ).execute()
    logger.success(f"Pasta criada: '{name}' ({created['id']})")
    return created["id"]


def _candidatos(pasta: Path) -> list[Path]:
    """JSONs de cliente OAuth numa pasta, mais recentes primeiro."""
    if not pasta.is_dir():
        return []
    achados = list(pasta.glob("client_secret*.json"))
    return sorted(achados, key=lambda p: p.stat().st_mtime, reverse=True)


def _resolver_client_secrets(raw: str) -> Path | None:
    """Aceita caminho, curinga ou pasta e devolve o JSON do cliente OAuth.

    O Google batiza o arquivo de
    ``client_secret_<id>.apps.googleusercontent.com.json`` — comprido o
    bastante para errar ao digitar. Pior: a página do Console exibe o *client
    id*, que termina em ``.apps.googleusercontent.com`` e é o que se copia por
    engano; falta só o ``.json`` do fim, e o erro "arquivo não encontrado"
    parecia dizer que o download tinha falhado.

    Args:
        raw: Valor de ``--client-secrets`` (caminho, curinga ou diretório).

    Returns:
        O arquivo a usar, ou ``None`` se não houver um só candidato — nesse
        caso os candidatos encontrados já foram logados.
    """
    from utils.cli_args import expand_paths

    bruto = Path(raw)

    # Pasta: o caso "baixei em Downloads e não quero digitar o nome".
    if bruto.is_dir():
        achados = _candidatos(bruto)
    elif any(c in raw for c in ("*", "?", "[")):
        achados, _ = expand_paths([raw])
    elif bruto.exists():
        achados = [bruto]
    elif Path(f"{bruto}.json").exists():
        # Faltou só o ".json" (client id copiado do Console). Concatenar a
        # string em vez de with_suffix: o nome tem pontos no meio e with_suffix
        # trocaria o ".com" pelo ".json".
        achados = [Path(f"{bruto}.json")]
        logger.info(f"Faltava o .json no fim — usando: {achados[0].name}")
    else:
        achados = []

    if len(achados) == 1:
        return achados[0]

    if not achados:
        logger.error(f"Arquivo de credenciais não encontrado: {bruto}")
        vizinhos = _candidatos(bruto.parent if bruto.parent != Path("") else Path("."))
        if vizinhos:
            logger.info("Encontrei estes JSONs de cliente OAuth na mesma pasta:")
            for cand in vizinhos:
                logger.info(f"  --client-secrets \"{cand}\"")
        else:
            logger.info(
                "Nenhum client_secret*.json nessa pasta. O nome termina em "
                '".apps.googleusercontent.com.json" — o que o Console mostra '
                "na tela é o client id, sem o .json do fim."
            )
        return None

    logger.error(f"{len(achados)} JSONs casaram — escolha um (o de cima é o mais recente):")
    for cand in achados:
        logger.info(f"  --client-secrets \"{cand}\"")
    logger.info("Cada arquivo é um cliente OAuth diferente, e o refresh token")
    logger.info("vale só para o cliente que autorizou — por isso não escolho por você.")
    return None


def cmd_authorize(args: argparse.Namespace) -> int:
    """Fluxo OAuth completo: consentimento → refresh token → pasta → .env."""
    _require_google_libs()
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    secrets_path = _resolver_client_secrets(args.client_secrets)
    if secrets_path is None:
        return 1

    # Valida o formato antes do consentimento: um JSON de conta de serviço (ou
    # qualquer outro) passaria pelo fluxo e só apareceria como GDRIVE_CLIENT_ID
    # vazio no fim, depois de o usuário já ter autorizado no navegador.
    try:
        client_cfg = json.loads(secrets_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error(f"Não consegui ler {secrets_path.name} como JSON: {exc}")
        return 1
    if not (client_cfg.get("installed") or client_cfg.get("web")):
        logger.error(
            f"{secrets_path.name} não é um JSON de 'ID do cliente OAuth' — falta a "
            "chave 'installed'/'web'."
        )
        if client_cfg.get("type") == "service_account":
            logger.info(
                "Este é um JSON de conta de serviço. Para Shared Drive use "
                "GDRIVE_SERVICE_ACCOUNT_JSON no .env e pule este script; em conta "
                "pessoal (@gmail.com) ele falha com storageQuotaExceeded."
            )
        else:
            logger.info(
                "Baixe em Credenciais → Criar credenciais → ID do cliente OAuth "
                "→ tipo 'App para computador'."
            )
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), _SCOPES)
    logger.info("Abrindo o navegador para o consentimento do Google...")
    # access_type=offline + prompt=consent garantem que o refresh_token venha
    # mesmo se a conta já autorizou este app antes.
    creds = flow.run_local_server(
        port=0, access_type="offline", prompt="consent",
    )

    if not creds.refresh_token:
        logger.error(
            "O Google não devolveu refresh_token. Revogue o acesso do app em "
            "https://myaccount.google.com/permissions e rode de novo."
        )
        return 1

    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    folder_id = _ensure_folder(service, args.folder_name)

    installed = client_cfg.get("installed") or client_cfg.get("web") or {}

    logger.success("Autorização concluída. Adicione estas linhas ao seu .env:\n")
    print("# --- Histórico frio no Google Drive ---")
    print(f"GDRIVE_CLIENT_ID={installed.get('client_id', '')}")
    print(f"GDRIVE_CLIENT_SECRET={installed.get('client_secret', '')}")
    print(f"GDRIVE_REFRESH_TOKEN={creds.refresh_token}")
    print(f"GDRIVE_FOLDER_ID={folder_id}")
    print("RAC_HISTORY_BACKEND=drive")
    print()
    logger.warning(
        "O refresh token é uma credencial — NUNCA comite o .env. "
        "Na VM/GitHub Actions, cadastre os 4 valores como secrets."
    )
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    """Confere se o ambiente atual consegue mesmo gravar e ler no Drive."""
    # O .env é carregado no import de utils.history.store — aqui basta importar.
    from utils.history import get_store, resolve_backend_name

    logger.info(f"Backend resolvido: {resolve_backend_name()}")
    store = get_store()
    logger.info(f"Destino: {store.backend.describe}")

    if "drive" not in store.backend.describe:
        logger.error(
            "O histórico NÃO está apontando para o Drive. Confira "
            "GDRIVE_FOLDER_ID e RAC_HISTORY_BACKEND no .env."
        )
        return 1

    from datetime import date
    marcador = [{"data": date.today().isoformat(), "produto": "__setup_check__"}]
    try:
        store.write_day("_setup_check", date.today(), marcador, run_id="check")
        lido = store.read("_setup_check", start=date.today(), end=date.today())
        if lido.empty:
            logger.error("Gravou mas não conseguiu reler — verifique permissões.")
            return 1
        store.drop_day("_setup_check", date.today())
        # Sem isto sobra uma pasta vazia '_setup_check' no Drive do usuário.
        store.backend.remove_dataset("_setup_check")
        logger.success("Drive OK: gravou, releu e limpou o marcador de teste. ✓")
        return 0
    except Exception as exc:
        logger.error(f"Falha no teste de ida e volta: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Autoriza o histórico do RAC a gravar no Google Drive.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--client-secrets", metavar="JSON",
        help="JSON do 'ID do cliente OAuth' (tipo: App para computador). "
             "Aceita curinga ou a pasta onde o arquivo foi baixado.",
    )
    parser.add_argument(
        "--folder-name", default=_DEFAULT_FOLDER,
        help=f"Nome da pasta no Drive (default: '{_DEFAULT_FOLDER}').",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Não autoriza nada; só testa o .env atual gravando e relendo.",
    )
    args = parser.parse_args()

    if args.check:
        return cmd_check(args)
    if not args.client_secrets:
        parser.error("informe --client-secrets ou use --check")
    return cmd_authorize(args)


if __name__ == "__main__":
    sys.exit(main())
