"""
Configuração do cliente PriceTrack via variáveis de ambiente.

A API key é obrigatória, vem SEMPRE do ambiente (nunca hardcoded) e fica fora
do ``repr`` do dataclass — nenhum log, exceção ou dump de settings a expõe.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

from .exceptions import PriceTrackConfigError

_ENV_PREFIX = "PRICETRACK_"


@dataclass(slots=True)
class PriceTrackSettings:
    """Parâmetros de acesso e política de coleta da API PriceTrack.

    Todos os campos (exceto ``api_key``) têm default de produção e podem ser
    sobrescritos por variável de ambiente ``PRICETRACK_<NOME_UPPER>`` — ver
    ``from_env()``.
    """

    api_key: str = field(repr=False)
    base_url: str = "https://api.pricetrack.com.br"
    # Nome do header de autenticação (ApiKeyAuth). Produção usa "token".
    auth_header: str = "token"

    # HTTP
    timeout_seconds: float = 30.0
    download_timeout_seconds: float = 600.0
    max_retries: int = 5
    backoff_base_seconds: float = 2.0
    backoff_max_seconds: float = 60.0

    # Paginação (endpoints síncronos)
    page_take: int = 100

    # Estratégia de coleta: acima deste nº estimado de linhas, usa export bulk
    export_threshold_rows: int = 50_000

    # Export assíncrono
    max_concurrent_exports: int = 3          # limite da API por organização
    poll_interval_seconds: float = 30.0
    poll_timeout_seconds: float = 7200.0     # 2h por export
    # Margem de segurança sobre o TTL de 1h da downloadUrl: renova aos 50min.
    download_url_ttl_seconds: float = 3000.0

    # Diretório raiz das partições locais (raw NDJSON por collection_date)
    data_dir: Path = Path("imports/pricetrack/api")

    def __post_init__(self) -> None:
        if not self.api_key or not str(self.api_key).strip():
            raise PriceTrackConfigError(
                "PRICETRACK_API_KEY não configurada. Defina no .env ou no "
                "ambiente — a key nunca deve ser hardcoded."
            )
        self.api_key = str(self.api_key).strip()
        self.base_url = self.base_url.rstrip("/")
        self.data_dir = Path(self.data_dir)
        if self.max_concurrent_exports > 3:
            raise PriceTrackConfigError(
                "max_concurrent_exports não pode exceder 3 (limite da API)."
            )

    @classmethod
    def from_env(cls, env: dict | None = None, **overrides) -> "PriceTrackSettings":
        """Monta settings a partir do ambiente.

        Args:
            env: mapeamento a usar no lugar de ``os.environ`` (para testes).
            **overrides: valores que ganham do ambiente (ex.: ``api_key=...``
                quando o chamador já validou o token por conta própria).

        Returns:
            Settings prontos, com tipos convertidos por campo.

        Raises:
            PriceTrackConfigError: api_key ausente ou valor de env inválido.
        """
        env = dict(os.environ if env is None else env)
        kwargs: dict = {}
        if "PRICETRACK_API_KEY" in env:
            kwargs["api_key"] = env["PRICETRACK_API_KEY"]

        for f in fields(cls):
            if f.name == "api_key":
                continue
            raw = env.get(_ENV_PREFIX + f.name.upper())
            if raw is None or raw == "":
                continue
            try:
                if f.type in ("float", float):
                    kwargs[f.name] = float(raw)
                elif f.type in ("int", int):
                    kwargs[f.name] = int(raw)
                elif f.type in ("Path", Path):
                    kwargs[f.name] = Path(raw)
                else:
                    kwargs[f.name] = raw
            except ValueError as e:
                raise PriceTrackConfigError(
                    f"Valor inválido em {_ENV_PREFIX}{f.name.upper()}: {e}"
                ) from e

        kwargs.update(overrides)
        if "api_key" not in kwargs:
            raise PriceTrackConfigError(
                "PRICETRACK_API_KEY não configurada. Defina no .env ou no "
                "ambiente — a key nunca deve ser hardcoded."
            )
        return cls(**kwargs)
