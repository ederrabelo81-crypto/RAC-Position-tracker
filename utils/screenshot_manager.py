"""
utils/screenshot_manager.py — Captura, deduplicação e retenção de screenshots.

Funcionalidades:
  - Captura via Playwright (sync) em formato WebP comprimido
  - Normalização de URLs (remove UTM / tracking, mantém sku/id)
  - Deduplicação por SHA256 com manifesto JSON + filelock
  - Upload opcional para Supabase Storage com fallback silencioso
  - Limpeza automática por TTL (default 15 dias)

Integração: instanciado em BaseScraper.__init__ quando ENABLE_SCREENSHOTS=True.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from loguru import logger

# ---------------------------------------------------------------------------
# Dependências opcionais — degradam silenciosamente se ausentes
# ---------------------------------------------------------------------------

try:
    from PIL import Image  # type: ignore
    _HAS_PILLOW = True
except ImportError:
    _HAS_PILLOW = False

try:
    from filelock import FileLock, Timeout as FileLockTimeout  # type: ignore
    _HAS_FILELOCK = True
except ImportError:
    import threading
    _HAS_FILELOCK = False

    class FileLockTimeout(Exception):
        pass

    class FileLock:  # type: ignore[no-redef]
        """Fallback em processo único usando threading.Lock."""

        _locks: Dict[str, "threading.Lock"] = {}

        def __init__(self, path: str, timeout: float = 10.0) -> None:
            self._key = str(Path(path).resolve())
            self._timeout = timeout
            self._lock = self._locks.setdefault(self._key, threading.Lock())

        def __enter__(self):
            if not self._lock.acquire(timeout=self._timeout):
                raise FileLockTimeout(self._key)
            return self

        def __exit__(self, *_) -> None:
            self._lock.release()


# Query params que IDENTIFICAM o produto — preservar.
# Tudo o mais (utm_*, gclid, fbclid, _ga, session_id, ref, etc.) é removido.
_KEEP_QUERY_KEYS = {
    "sku", "skuid", "sku_id",
    "id", "productid", "product_id", "pid",
    "variant", "variantid", "variant_id",
    "p",       # algumas lojas usam ?p=<id>
    "q",       # query de busca (preserva keyword)
    "page",    # paginação
}

_FILENAME_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def _safe_slug(value: str, max_len: int = 80) -> str:
    """Normaliza string para uso em nome de arquivo."""
    slug = _FILENAME_SAFE.sub("_", value.strip()).strip("_")
    return slug[:max_len] or "untitled"


def _normalize_url(url: str) -> str:
    """Remove query params de rastreamento, mantendo identificadores do produto."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        kept = [
            (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False)
            if k.lower() in _KEEP_QUERY_KEYS
        ]
        return urlunparse(parsed._replace(query=urlencode(kept), fragment=""))
    except Exception:
        return url


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ScreenshotManager:
    """
    Gerencia captura, persistência e retenção de screenshots.

    Não usa o Supabase quando o client não está disponível ou as credenciais
    estão ausentes — todas as falhas degradam silenciosamente (apenas warning).
    """

    DEFAULT_BUCKET = "rac-screenshots"

    def __init__(
        self,
        base_dir: str = "./screenshots",
        retention_days: int = 15,
        bucket_name: str = DEFAULT_BUCKET,
        viewport: Tuple[int, int] = (1920, 1080),
        upload_enabled: bool = False,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.retention_days = retention_days
        self.bucket_name = bucket_name
        self.viewport = viewport
        self.upload_enabled = upload_enabled

        self._manifest_path = self.base_dir / "screenshot_manifest.json"
        self._lock_path = self.base_dir / "screenshot_manifest.lock"

        # Cache do client supabase — None quando upload desabilitado ou indisponível.
        # Modo local-only: screenshots ficam só no disco (sem custo de Storage).
        if upload_enabled:
            self._supabase = self._init_supabase()
        else:
            logger.info("[Screenshots] Modo local-only — upload para Supabase desativado.")
            self._supabase = None

    # ------------------------------------------------------------------
    # Setup auxiliar
    # ------------------------------------------------------------------

    def _init_supabase(self):
        try:
            from utils.supabase_client import _get_client  # type: ignore
            client = _get_client()
            if client is None:
                logger.info("[Screenshots] Supabase indisponível — backup local apenas.")
            return client
        except Exception as exc:
            logger.warning(f"[Screenshots] Falha ao inicializar Supabase: {exc}")
            return None

    def _acquire_lock(self) -> FileLock:
        return FileLock(str(self._lock_path), timeout=15)

    def _load_manifest(self) -> Dict[str, Dict[str, Any]]:
        if not self._manifest_path.exists():
            return {}
        try:
            with self._manifest_path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"[Screenshots] Manifesto corrompido ({exc}); reiniciando.")
            return {}

    def _save_manifest(self, manifest: Dict[str, Dict[str, Any]]) -> None:
        tmp = self._manifest_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self._manifest_path)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_hash(image_buffer: bytes) -> str:
        return hashlib.sha256(image_buffer).hexdigest()

    def _to_webp(self, png_bytes: bytes, quality: int = 80) -> Tuple[bytes, str]:
        """
        Converte PNG → WebP via Pillow. Se Pillow ausente, devolve o PNG cru e
        registra a extensão correta para que o arquivo continue válido.
        """
        if not _HAS_PILLOW:
            return png_bytes, "png"
        try:
            with Image.open(io.BytesIO(png_bytes)) as im:
                buf = io.BytesIO()
                im.save(buf, format="WEBP", quality=quality, method=4)
                return buf.getvalue(), "webp"
        except Exception as exc:
            logger.warning(f"[Screenshots] Falha ao converter WebP: {exc}")
            return png_bytes, "png"

    def _local_path(self, platform: str, identifier: str, tipo: str, ext: str) -> Path:
        day = _utcnow().strftime("%Y%m%d")
        folder = self.base_dir / day / _safe_slug(platform)
        folder.mkdir(parents=True, exist_ok=True)
        fname = f"{_safe_slug(identifier)}_{_safe_slug(tipo)}.{ext}"
        return folder / fname

    def _remote_path(self, local_path: Path) -> str:
        """Caminho relativo dentro do bucket — mantém estrutura YYYYMMDD/platform/file."""
        try:
            rel = local_path.relative_to(self.base_dir)
        except ValueError:
            rel = Path(local_path.name)
        return str(rel).replace(os.sep, "/")

    def _upload_to_bucket(self, remote_path: str, payload: bytes, ext: str) -> Optional[str]:
        if self._supabase is None:
            return None
        content_type = "image/webp" if ext == "webp" else "image/png"
        try:
            storage = self._supabase.storage.from_(self.bucket_name)
            storage.upload(
                path=remote_path,
                file=payload,
                file_options={"content-type": content_type, "upsert": "true"},
            )
            try:
                public_url = storage.get_public_url(remote_path)
            except Exception:
                public_url = None
            return public_url or remote_path
        except Exception as exc:
            logger.warning(
                f"[Screenshots] Upload Supabase falhou ({remote_path}): {exc} — "
                f"mantendo somente cópia local."
            )
            return None

    def _delete_from_bucket(self, remote_path: str) -> None:
        if self._supabase is None:
            return
        try:
            self._supabase.storage.from_(self.bucket_name).remove([remote_path])
        except Exception as exc:
            logger.debug(f"[Screenshots] Remoção remota falhou ({remote_path}): {exc}")

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def capture(
        self,
        page,
        platform: str,
        identifier: str,
        tipo: str,
        full_page: bool = False,
    ) -> Optional[str]:
        """
        Captura screenshot da página atual.

        Args:
            page:       objeto Playwright Page (sync API).
            platform:   nome da loja (ex: "mercado_livre").
            identifier: keyword ou product_id usado para nomear o arquivo.
            tipo:       "busca" ou "produto".
            full_page:  se True, captura a página inteira (mais lento).

        Returns:
            Caminho remoto (URL pública) ou local do arquivo. None em caso de falha.
        """
        if page is None:
            return None

        try:
            canonical_url = _normalize_url(getattr(page, "url", "") or "")
            png_bytes = page.screenshot(type="png", full_page=full_page)
        except Exception as exc:
            logger.warning(f"[Screenshots] page.screenshot falhou ({platform}/{tipo}): {exc}")
            return None

        payload, ext = self._to_webp(png_bytes)
        digest = self._compute_hash(payload)

        try:
            with self._acquire_lock():
                manifest = self._load_manifest()
                existing = manifest.get(digest)

                # Deduplicação: hash igual capturado nas últimas 24h → ignora gravação.
                if existing:
                    try:
                        captured_at = datetime.fromisoformat(
                            existing["capture_date"].replace("Z", "+00:00")
                        )
                    except Exception:
                        captured_at = _utcnow() - timedelta(days=365)

                    if _utcnow() - captured_at < timedelta(hours=24):
                        logger.debug(
                            f"[Screenshots] Duplicado ({platform}/{tipo}); "
                            f"reutilizando {existing.get('file_path')}"
                        )
                        return existing.get("remote_url") or existing.get("file_path")

                local_path = self._local_path(platform, identifier, tipo, ext)
                local_path.write_bytes(payload)

                remote_path = self._remote_path(local_path)
                remote_url = self._upload_to_bucket(remote_path, payload, ext)

                now = _utcnow()
                manifest[digest] = {
                    "platform": platform,
                    "identifier": identifier,
                    "tipo": tipo,
                    "url_canonica": canonical_url,
                    "capture_date": now.isoformat(),
                    "expires_at": (now + timedelta(days=self.retention_days)).isoformat(),
                    "file_path": str(local_path),
                    "remote_path": remote_path,
                    "remote_url": remote_url,
                    "is_duplicate": False,
                }
                self._save_manifest(manifest)

                return remote_url or str(local_path)
        except FileLockTimeout:
            logger.warning("[Screenshots] Lock do manifesto não obtido — captura ignorada.")
            return None
        except Exception as exc:
            logger.warning(f"[Screenshots] Falha ao persistir screenshot: {exc}")
            return None

    def cleanup_expired(self) -> Dict[str, Any]:
        """
        Remove screenshots vencidos (disco + bucket + manifesto).

        Returns:
            Estatísticas: arquivos removidos, MB liberados, erros.
        """
        stats = {"removed": 0, "freed_mb": 0.0, "errors": 0}
        try:
            with self._acquire_lock():
                manifest = self._load_manifest()
                if not manifest:
                    return stats

                now = _utcnow()
                expired_hashes = []
                for digest, entry in manifest.items():
                    try:
                        expires_at = datetime.fromisoformat(
                            entry["expires_at"].replace("Z", "+00:00")
                        )
                    except Exception:
                        continue
                    if now > expires_at:
                        expired_hashes.append(digest)

                for digest in expired_hashes:
                    entry = manifest[digest]
                    file_path = Path(entry.get("file_path", ""))
                    if file_path.is_file():
                        try:
                            size = file_path.stat().st_size
                            file_path.unlink()
                            stats["freed_mb"] += size / (1024 * 1024)
                        except OSError as exc:
                            stats["errors"] += 1
                            logger.debug(
                                f"[Screenshots] Falha ao remover {file_path}: {exc}"
                            )

                    remote_path = entry.get("remote_path")
                    if remote_path:
                        self._delete_from_bucket(remote_path)

                    manifest.pop(digest, None)
                    stats["removed"] += 1

                if expired_hashes:
                    self._save_manifest(manifest)
                    logger.info(
                        f"[Screenshots] Cleanup TTL: {stats['removed']} arquivo(s) "
                        f"removido(s) ({stats['freed_mb']:.2f} MB liberados)."
                    )
        except FileLockTimeout:
            logger.warning("[Screenshots] Cleanup ignorado — lock indisponível.")
        except Exception as exc:
            logger.warning(f"[Screenshots] Erro no cleanup: {exc}")
        return stats
