"""Resolve stored screenshot references to local file paths."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve_screenshot_path(raw) -> Path | None:
    """Resolve uma referência de screenshot armazenada para um Path local.

    No modo local-only os screenshots guardam um caminho de arquivo
    (ex: 'screenshots/20260514/Mercado_Livre/kw_busca.webp'), relativo à raiz
    do projeto. Retorna o Path se o arquivo existir, senão None. URLs http(s)
    retornam None (o chamador trata como imagem remota).
    """
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw or raw.startswith("http://") or raw.startswith("https://"):
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p if p.exists() else None
