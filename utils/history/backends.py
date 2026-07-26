"""
utils/history/backends.py — Backends de armazenamento do histórico frio.

O histórico do RAC Position Tracker vive como arquivos Parquet imutáveis, um
por (dataset, dia, run). Este módulo abstrai *onde* esses bytes ficam:

* ``LocalBackend``  — um diretório no disco. Também serve de **cache** do
  backend do Drive (partições são imutáveis, então cache nunca invalida).
* ``GoogleDriveBackend`` — uma pasta do Google Drive, via API oficial.

O contrato é deliberadamente pequeno (``put``/``get``/``list``/``delete``) para
que trocar de destino (S3, Backblaze, outro Drive) seja escrever ~80 linhas.

Autenticação do Drive
---------------------
Duas formas, nesta ordem de precedência:

1. **Conta de serviço** — ``GDRIVE_SERVICE_ACCOUNT_JSON`` (caminho do arquivo
   ou o JSON inline). Só funciona bem com **Shared Drive** (Workspace): contas
   de serviço não têm cota de armazenamento própria no "Meu Drive".
2. **OAuth de usuário** — ``GDRIVE_CLIENT_ID`` + ``GDRIVE_CLIENT_SECRET`` +
   ``GDRIVE_REFRESH_TOKEN``. É o caminho para conta pessoal (@gmail.com): os
   arquivos são do usuário e contam na cota de 15 GB dele.

Em ambos os casos ``GDRIVE_FOLDER_ID`` aponta a pasta raiz do histórico.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
_FOLDER_MIME = "application/vnd.google-apps.folder"

#: Tentativas por operação no Drive. Falha transitória (429, 5xx, rede) não
#: pode virar buraco permanente no histórico — a coleta do dia não se repete.
_DRIVE_MAX_ATTEMPTS = 3
_DRIVE_BACKOFF_BASE = 2.0  # segundos; dobra a cada tentativa


class HistoryBackendError(RuntimeError):
    """Falha de I/O no backend de histórico (rede, credencial, permissão)."""


class HistoryBackend(ABC):
    """Armazenamento chave→bytes, com chaves no formato ``dataset/arquivo``."""

    @abstractmethod
    def put(self, key: str, payload: bytes) -> None:
        """Grava ``payload`` em ``key``, sobrescrevendo se já existir."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Lê os bytes de ``key``.

        Raises:
            HistoryBackendError: se a chave não existe ou a leitura falha.
        """

    @abstractmethod
    def list(self, dataset: str) -> List[str]:
        """Lista as chaves existentes sob ``dataset`` (ordenadas)."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove ``key``. Não falha se a chave já não existe."""

    def remove_dataset(self, dataset: str) -> None:
        """Remove o contêiner do dataset (pasta/diretório), se vazio.

        Só faz sentido para datasets efêmeros — hoje o marcador de teste do
        ``gdrive_setup.py --check``, que senão deixaria uma pasta órfã no Drive
        do usuário. Silencioso quando o backend não suporta ou não está vazio.
        """
        return None

    def fingerprint(self, key: str) -> Optional[str]:
        """Impressão digital do conteúdo de ``key``, ou ``None`` se desconhecida.

        Valida o cache local: uma partição reprocessada muda de impressão, e é
        assim que outro host percebe que a cópia dele envelheceu. ``None``
        significa "não sei" — aí o cache é considerado válido, porque baixar
        tudo de novo a cada leitura seria pior.

        Usa MD5 do conteúdo, não o tamanho: reprocessar um dia pode trocar
        valores sem mudar o número de bytes, e o tamanho deixaria passar.
        """
        return None

    @property
    def describe(self) -> str:
        """Descrição curta do destino, para log."""
        return self.__class__.__name__


# ---------------------------------------------------------------------------
# Local (disco)
# ---------------------------------------------------------------------------
class LocalBackend(HistoryBackend):
    """Histórico em um diretório local.

    Usado como destino final (modo ``local``) e como cache do Drive.

    Args:
        root: Diretório raiz. Criado sob demanda.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key

    def put(self, key: str, payload: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Escrita atômica: um leitor concorrente nunca vê Parquet truncado.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(payload)
        tmp.replace(path)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise HistoryBackendError(f"Falha lendo {path}: {exc}") from exc

    def list(self, dataset: str) -> List[str]:
        base = self.root / dataset
        if not base.is_dir():
            return []
        return sorted(
            f"{dataset}/{p.name}"
            for p in base.iterdir()
            if p.is_file() and p.suffix == ".parquet"
        )

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        """True se a chave já está no disco (usado pelo cache do Drive)."""
        return self._path(key).is_file()

    def remove_dataset(self, dataset: str) -> None:
        """Remove o diretório do dataset se estiver vazio."""
        base = self.root / dataset
        if base.is_dir() and not any(base.iterdir()):
            base.rmdir()

    def fingerprint(self, key: str) -> Optional[str]:
        """MD5 do arquivo em disco — mesmo algoritmo que o Drive reporta."""
        path = self._path(key)
        if not path.is_file():
            return None
        digest = hashlib.md5()
        with path.open("rb") as fh:
            for bloco in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(bloco)
        return digest.hexdigest()

    @property
    def describe(self) -> str:
        return f"local:{self.root}"


# ---------------------------------------------------------------------------
# Google Drive
# ---------------------------------------------------------------------------
def _load_drive_credentials():
    """Resolve credenciais do Drive a partir do ambiente.

    Returns:
        Objeto de credencial do ``google-auth``.

    Raises:
        HistoryBackendError: se nenhuma credencial utilizável foi encontrada.
    """
    sa_raw = os.getenv("GDRIVE_SERVICE_ACCOUNT_JSON", "").strip()
    if sa_raw:
        try:
            from google.oauth2 import service_account
        except ImportError as exc:  # pragma: no cover - depende do ambiente
            raise HistoryBackendError(
                "google-auth não instalado — pip install google-api-python-client google-auth"
            ) from exc
        try:
            info = (
                json.loads(sa_raw)
                if sa_raw.lstrip().startswith("{")
                else json.loads(Path(sa_raw).read_text(encoding="utf-8"))
            )
        except (OSError, ValueError) as exc:
            raise HistoryBackendError(
                f"GDRIVE_SERVICE_ACCOUNT_JSON inválido: {exc}"
            ) from exc
        return service_account.Credentials.from_service_account_info(
            info, scopes=_DRIVE_SCOPES
        )

    client_id = os.getenv("GDRIVE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GDRIVE_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("GDRIVE_REFRESH_TOKEN", "").strip()
    if client_id and client_secret and refresh_token:
        try:
            from google.oauth2.credentials import Credentials
        except ImportError as exc:  # pragma: no cover - depende do ambiente
            raise HistoryBackendError(
                "google-auth não instalado — pip install google-api-python-client google-auth"
            ) from exc
        return Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=_DRIVE_SCOPES,
        )

    raise HistoryBackendError(
        "Credenciais do Drive ausentes — defina GDRIVE_SERVICE_ACCOUNT_JSON ou "
        "GDRIVE_CLIENT_ID/GDRIVE_CLIENT_SECRET/GDRIVE_REFRESH_TOKEN. "
        "Setup: docs/HISTORICO_DRIVE.md"
    )


class GoogleDriveBackend(HistoryBackend):
    """Histórico numa pasta do Google Drive.

    Layout: uma subpasta por dataset dentro de ``root_folder_id``, e um arquivo
    Parquet por partição. Os ids de pasta e de arquivo são memorizados em
    memória para não repetir busca a cada chamada.

    Args:
        root_folder_id: Id da pasta raiz (``GDRIVE_FOLDER_ID``).
        service: Injetável nos testes; em produção é construído sob demanda.
    """

    def __init__(
        self,
        root_folder_id: str,
        service: Optional[object] = None,
        max_attempts: int = _DRIVE_MAX_ATTEMPTS,
    ) -> None:
        if not root_folder_id:
            raise HistoryBackendError(
                "GDRIVE_FOLDER_ID não definido — sem ele não há onde gravar."
            )
        self.root_folder_id = root_folder_id
        self._service = service
        self._max_attempts = max(1, max_attempts)
        self._folder_ids: Dict[str, str] = {}
        self._file_ids: Dict[str, str] = {}
        self._fingerprints: Dict[str, Optional[str]] = {}

    def _retry(self, what: str, fn):
        """Executa ``fn`` com retentativas limitadas e backoff exponencial.

        Falha transitória do Drive (rede, 429, 5xx) não pode virar buraco
        permanente no histórico — a coleta do dia não se repete.

        Args:
            what: Descrição da operação, para a mensagem de erro.
            fn: Callable sem argumentos que faz a chamada à API.

        Returns:
            O retorno de ``fn``.

        Raises:
            HistoryBackendError: se todas as tentativas falharem.
        """
        ultimo: Optional[Exception] = None
        for tentativa in range(1, self._max_attempts + 1):
            try:
                return fn()
            except Exception as exc:
                ultimo = exc
                if tentativa == self._max_attempts:
                    break
                espera = _DRIVE_BACKOFF_BASE * (2 ** (tentativa - 1))
                logger.warning(
                    f"[Drive] {what} falhou (tentativa {tentativa}/"
                    f"{self._max_attempts}): {exc} — nova tentativa em {espera:.0f}s"
                )
                time.sleep(espera)
        raise HistoryBackendError(f"Falha em {what} após "
                                  f"{self._max_attempts} tentativa(s): {ultimo}") from ultimo

    # -- infraestrutura ----------------------------------------------------
    @property
    def service(self):
        """Cliente da Drive API v3 (construído na primeira utilização)."""
        if self._service is None:
            try:
                from googleapiclient.discovery import build
            except ImportError as exc:  # pragma: no cover - depende do ambiente
                raise HistoryBackendError(
                    "google-api-python-client não instalado — "
                    "pip install google-api-python-client google-auth"
                ) from exc
            self._service = build(
                "drive", "v3",
                credentials=_load_drive_credentials(),
                cache_discovery=False,
            )
        return self._service

    def _folder_id(self, dataset: str) -> str:
        """Id da subpasta do dataset, criando-a se necessário."""
        if dataset in self._folder_ids:
            return self._folder_ids[dataset]

        query = (
            f"name = '{dataset}' and mimeType = '{_FOLDER_MIME}' "
            f"and '{self.root_folder_id}' in parents and trashed = false"
        )
        found = self._list_files(query)
        if found:
            folder_id = found[0]["id"]
        else:
            created = self._retry(
                f"criação da pasta '{dataset}'",
                lambda: self.service.files().create(
                    body={
                        "name": dataset,
                        "mimeType": _FOLDER_MIME,
                        "parents": [self.root_folder_id],
                    },
                    fields="id",
                    supportsAllDrives=True,
                ).execute(),
            )
            folder_id = created["id"]
            logger.info(f"[Drive] Pasta '{dataset}' criada ({folder_id}).")

        self._folder_ids[dataset] = folder_id
        return folder_id

    def _list_files(self, query: str) -> List[dict]:
        """Lista arquivos da Drive API paginando até o fim."""
        items: List[dict] = []
        page_token = None
        while True:
            resp = self._retry(
                "listagem",
                lambda tok=page_token: self.service.files().list(
                    q=query,
                    fields="nextPageToken, files(id, name, size, md5Checksum)",
                    pageSize=1000,
                    pageToken=tok,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                ).execute(),
            )
            items.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return items

    @staticmethod
    def _split(key: str) -> tuple:
        dataset, _, name = key.partition("/")
        if not name:
            raise HistoryBackendError(f"Chave sem dataset: {key!r}")
        return dataset, name

    # -- contrato ----------------------------------------------------------
    def put(self, key: str, payload: bytes) -> None:
        from googleapiclient.http import MediaIoBaseUpload

        dataset, name = self._split(key)
        media = MediaIoBaseUpload(
            io.BytesIO(payload),
            mimetype="application/octet-stream",
            resumable=False,
        )
        # A resolução do id também fala com a API e também pode falhar — fica
        # DENTRO do try para que o chamador veja sempre HistoryBackendError, e
        # nunca um HttpError cru vazando do googleapiclient.
        try:
            existing = self._resolve_file_id(key, missing_ok=True)
            if existing:
                self._retry(
                    f"atualização de {key}",
                    lambda: self.service.files().update(
                        fileId=existing, media_body=media, supportsAllDrives=True,
                    ).execute(),
                )
            else:
                created = self._retry(
                    f"criação de {key}",
                    lambda: self.service.files().create(
                        body={"name": name, "parents": [self._folder_id(dataset)]},
                        media_body=media,
                        fields="id",
                        supportsAllDrives=True,
                    ).execute(),
                )
                self._file_ids[key] = created["id"]
        except HistoryBackendError:
            raise
        except Exception as exc:  # a API levanta HttpError e derivados de socket
            raise HistoryBackendError(f"Falha gravando {key} no Drive: {exc}") from exc
        # A impressão memorizada ficou obsoleta — a próxima listagem a renova.
        self._fingerprints.pop(key, None)

    def get(self, key: str) -> bytes:
        from googleapiclient.http import MediaIoBaseDownload

        buf = io.BytesIO()
        try:
            file_id = self._resolve_file_id(key)

            def _baixar():
                buf.seek(0)
                buf.truncate(0)
                downloader = MediaIoBaseDownload(
                    buf,
                    # supportsAllDrives também no download: sem ele a leitura
                    # falha em Shared Drive, onde escrita/listagem funcionam.
                    self.service.files().get_media(
                        fileId=file_id, supportsAllDrives=True,
                    ),
                )
                done = False
                while not done:
                    _, done = downloader.next_chunk()

            self._retry(f"download de {key}", _baixar)
        except HistoryBackendError:
            raise
        except Exception as exc:
            raise HistoryBackendError(f"Falha lendo {key} do Drive: {exc}") from exc
        return buf.getvalue()

    def list(self, dataset: str) -> List[str]:
        query = (
            f"'{self._folder_id(dataset)}' in parents and trashed = false "
            f"and mimeType != '{_FOLDER_MIME}'"
        )
        keys = []
        for item in self._list_files(query):
            key = f"{dataset}/{item['name']}"
            self._file_ids[key] = item["id"]
            # `md5Checksum` vem de graça na listagem, que roda antes de toda
            # leitura — é o que permite detectar partição reprocessada sem
            # nenhuma chamada extra à API.
            self._fingerprints[key] = item.get("md5Checksum")
            if key.endswith(".parquet"):
                keys.append(key)
        return sorted(keys)

    def delete(self, key: str) -> None:
        try:
            file_id = self._resolve_file_id(key, missing_ok=True)
            if not file_id:
                return
            self._retry(
                f"remoção de {key}",
                lambda: self.service.files().delete(
                    fileId=file_id, supportsAllDrives=True,
                ).execute(),
            )
        except HistoryBackendError:
            raise
        except Exception as exc:
            raise HistoryBackendError(f"Falha removendo {key} do Drive: {exc}") from exc
        self._file_ids.pop(key, None)
        self._fingerprints.pop(key, None)

    def remove_dataset(self, dataset: str) -> None:
        """Apaga a pasta do dataset no Drive, se ela não tiver mais arquivos."""
        try:
            if self.list(dataset):
                return
            folder_id = self._folder_id(dataset)
            self._retry(
                f"remoção da pasta '{dataset}'",
                lambda: self.service.files().delete(
                    fileId=folder_id, supportsAllDrives=True,
                ).execute(),
            )
            self._folder_ids.pop(dataset, None)
        except HistoryBackendError as exc:
            # Pasta órfã é cosmético — não vale falhar a operação do chamador.
            logger.warning(f"[Drive] não removi a pasta '{dataset}': {exc}")

    def fingerprint(self, key: str) -> Optional[str]:
        """MD5 da partição remota, memorizado pela listagem quando possível."""
        return self._fingerprints.get(key)

    def _resolve_file_id(self, key: str, missing_ok: bool = False) -> Optional[str]:
        """Id do arquivo, consultando a API só quando não está memorizado."""
        if key in self._file_ids:
            return self._file_ids[key]

        dataset, name = self._split(key)
        query = (
            f"name = '{name}' and '{self._folder_id(dataset)}' in parents "
            f"and trashed = false"
        )
        found = self._list_files(query)
        if not found:
            if missing_ok:
                return None
            raise HistoryBackendError(f"Chave não encontrada no Drive: {key}")
        self._file_ids[key] = found[0]["id"]
        return found[0]["id"]

    @property
    def describe(self) -> str:
        return f"drive:{self.root_folder_id}"
