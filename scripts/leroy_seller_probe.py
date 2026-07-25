"""
scripts/leroy_seller_probe.py — inspeciona e popula o cache de sellers da Leroy.

Ferramenta de diagnóstico/manutenção para o gap de identificação de seller:
o índice Algolia só devolve o ObjectId do lojista, e o nome só existe no PDP.

Uso:
    # Lista o que já está no cache (data/leroy_sellers.json)
    python scripts/leroy_seller_probe.py --list

    # Resolve o seller a partir de uma URL de PDP (grava no cache)
    python scripts/leroy_seller_probe.py --pdp https://www.leroymerlin.com.br/...

    # Idem, ancorando no ID que apareceu no WARNING da coleta
    python scripts/leroy_seller_probe.py --pdp <url> --seller-id 5e6fd1d9...

    # Testa a extração sobre um HTML já salvo (offline, sem rede)
    python scripts/leroy_seller_probe.py --html logs/pdp_dump.html

    # Roda uma busca Algolia e mostra quais IDs de seller aparecem
    python scripts/leroy_seller_probe.py --scan "ar condicionado lg"

    # Registra um ID manualmente (quando você já sabe o lojista)
    python scripts/leroy_seller_probe.py --set 5e6fd1d90a8aa474fe271e83="Loja X"
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from loguru import logger

from utils.leroy_sellers import (
    LeroySellerCache,
    extract_seller_from_pdp,
    is_leroy_self,
)

_OBJECT_ID_RE = re.compile(r"[0-9a-f]{24}", re.IGNORECASE)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9",
}


def _fetch(url: str, timeout: float = 20.0) -> Optional[str]:
    """Baixa o HTML de uma URL, devolvendo None em erro ou resposta suspeita."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        logger.error(f"Falha ao baixar {url}: {exc}")
        return None
    if resp.status_code != 200 or len(resp.text or "") < 1024:
        logger.error(
            f"Resposta inutilizável: HTTP {resp.status_code}, {len(resp.text or '')} bytes"
        )
        return None
    return resp.text


def cmd_list(cache: LeroySellerCache) -> int:
    """Imprime o conteúdo atual do cache de sellers."""
    sellers = cache.sellers
    if not sellers:
        logger.warning(f"Cache vazio: {cache.path}")
        return 0
    logger.info(f"{len(sellers)} seller(s) em {cache.path}:")
    for sid, name in sorted(sellers.items(), key=lambda kv: kv[1].lower()):
        print(f"  {sid}  →  {name}")
    return 0


def cmd_pdp(cache: LeroySellerCache, url: str, seller_id: Optional[str]) -> int:
    """Resolve o seller a partir de um PDP online e grava no cache."""
    html = _fetch(url)
    if not html:
        return 1
    name = extract_seller_from_pdp(html, seller_id)
    if not name:
        logger.error("Nenhuma estratégia extraiu o seller deste PDP.")
        dump = Path("logs") / "leroy_pdp_probe.html"
        dump.parent.mkdir(parents=True, exist_ok=True)
        dump.write_text(html, encoding="utf-8")
        logger.info(f"HTML salvo para inspeção: {dump}")
        return 1
    logger.success(f"Seller: {name}")
    if not seller_id:
        logger.warning("Sem --seller-id: nada foi gravado no cache.")
        return 0

    # Mesma guarda do scraper: um ID de marketplace jamais deve ser gravado
    # apontando para a própria Leroy — envenenaria o cache de forma persistente,
    # fazendo o produto 3P ser medido como 1P em todas as coletas seguintes.
    if is_leroy_self(name):
        logger.error(
            f"'{name}' é o sortimento 1P da Leroy — não gravado para {seller_id}. "
            "PDP provavelmente genérico/redirecionado; use --set para forçar."
        )
        return 1

    cache.put(seller_id, name)
    cache.save()
    logger.success(f"Gravado no cache: {seller_id} → {name}")
    return 0


def cmd_html(path: str, seller_id: Optional[str]) -> int:
    """Testa a extração sobre um HTML local (offline)."""
    try:
        html = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        logger.error(f"Não foi possível ler {path}: {exc}")
        return 1
    name = extract_seller_from_pdp(html, seller_id)
    if not name:
        logger.error("Nenhuma estratégia extraiu o seller deste HTML.")
        return 1
    logger.success(f"Seller: {name}")
    return 0


def extract_seller_ids(marketplace_sellers: Any) -> list[str]:
    """
    Normaliza `marketplaceSellers` para uma lista de seller IDs.

    Cobre os três shapes que o scraper aceita, para o relatório de lacunas não
    contar um hit como 3P e ao mesmo tempo omitir os IDs dele:

    - lista de strings — ``["5e6fd1d9…"]`` (shape observado em produção)
    - lista de dicts   — ``[{"sellerId": "…", "sellerName": "…"}]``
    - dict indexado    — ``{"5e6fd1d9…": {...}}``

    No shape de dict, uma chave só é aceita como seller ID quando o valor é um
    dict (mesma condição do Caso C do scraper) ou quando a própria chave tem
    cara de ObjectId. Sem isso, um dict que não é indexado por ID — ex.
    ``{"id": "…"}`` — viraria um seller fantasma chamado "id" no relatório,
    inventando uma lacuna de cache e engolindo o aviso de shape novo.

    Args:
        marketplace_sellers: valor bruto do campo, em qualquer formato.

    Returns:
        Lista de IDs como string, vazia se o formato for irreconhecível.

    Example:
        >>> extract_seller_ids([{"sellerId": "abc"}, "def"])
        ['abc', 'def']
        >>> extract_seller_ids({"id": "nao-e-indexado"})
        []
    """
    ids: list[str] = []
    if isinstance(marketplace_sellers, list):
        for entry in marketplace_sellers:
            if isinstance(entry, str) and entry:
                ids.append(entry)
            elif isinstance(entry, dict):
                sid = (
                    entry.get("sellerId")
                    or entry.get("seller_id")
                    or entry.get("id")
                    or entry.get("_id")
                )
                if sid:
                    ids.append(str(sid))
    elif isinstance(marketplace_sellers, dict):
        for key, value in marketplace_sellers.items():
            if not key:
                continue
            if isinstance(value, dict) or _OBJECT_ID_RE.fullmatch(str(key)):
                ids.append(str(key))
    return ids


def cmd_scan(cache: LeroySellerCache, keyword: str, pages: int) -> int:
    """Consulta a Algolia e reporta quais seller IDs aparecem e quais faltam."""
    from scrapers.leroy_merlin import (
        LEROY_SELLER_ID_MAP,
        _ALGOLIA_HEADERS,
        _ALGOLIA_SEARCH_URL,
        _ITEMS_PER_PAGE,
    )

    counts: Counter = Counter()
    urls: dict[str, str] = {}
    n_1p = 0
    n_3p = 0
    n_sem_id = 0   # hits 3P cujo shape não expôs nenhum ID (shape novo do índice)

    for page in range(pages):
        payload = {"query": keyword, "hitsPerPage": _ITEMS_PER_PAGE, "page": page}
        try:
            resp = requests.post(
                _ALGOLIA_SEARCH_URL, headers=_ALGOLIA_HEADERS, json=payload, timeout=15
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error(f"Algolia falhou: {exc}")
            return 1
        hits = resp.json().get("hits", [])
        if not hits:
            break
        for hit in hits:
            ms = hit.get("marketplaceSellers")
            if not ms:
                n_1p += 1
                continue
            n_3p += 1
            slug = hit.get("url") or hit.get("linkText") or hit.get("slug")
            slug = str(slug) if slug else None
            url = (
                slug if not slug or slug.startswith("http")
                else f"https://www.leroymerlin.com.br{slug}"
                if slug.startswith("/")
                else f"https://www.leroymerlin.com.br/{slug.strip('/')}/p"
            )
            # Conta *todos* os IDs do hit, em qualquer shape: o objetivo do scan
            # é achar lacunas no cache, então parar no primeiro (ou ignorar
            # shapes que o scraper aceita) esconderia sellers desconhecidos.
            seller_ids = extract_seller_ids(ms)
            if not seller_ids:
                n_sem_id += 1
            for sid in seller_ids:
                counts[sid] += 1
                if url and sid not in urls:
                    urls[sid] = url

    desconhecidos = [
        sid for sid in counts
        if not (LEROY_SELLER_ID_MAP.get(sid) or cache.get(sid))
    ]
    logger.info(
        f"'{keyword}': {n_1p + n_3p} hits | {n_1p} 1P | {n_3p} 3P | "
        f"{len(counts)} seller ID(s) únicos, {len(desconhecidos)} desconhecido(s)"
    )
    if n_sem_id:
        logger.warning(
            f"{n_sem_id} hit(s) 3P sem nenhum ID extraível — shape novo de "
            "`marketplaceSellers`? Rode com LEROY_DEBUG_HITS=5 para inspecionar."
        )
    for sid, n in counts.most_common():
        known = LEROY_SELLER_ID_MAP.get(sid) or cache.get(sid)
        status = f"✓ {known}" if known else "✗ DESCONHECIDO"
        print(f"  {sid}  ({n:>3} produtos)  {status}")
        if not known and sid in urls:
            print(f"      PDP: {urls[sid]}")
    return 0


def cmd_set(cache: LeroySellerCache, assignment: str) -> int:
    """Grava manualmente um par ``id=nome`` no cache."""
    if "=" not in assignment:
        logger.error("Formato esperado: --set <seller_id>=\"Nome da Loja\"")
        return 1
    sid, name = assignment.split("=", 1)
    sid, name = sid.strip(), name.strip().strip('"').strip("'")
    cache.put(sid, name)
    if cache.save():
        logger.success(f"Gravado: {sid} → {name}")
        return 0
    logger.error("Nada gravado — nome inválido ou já presente com o mesmo valor.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspeciona e popula o cache de sellers da Leroy Merlin."
    )
    parser.add_argument("--list", action="store_true", help="lista o cache atual")
    parser.add_argument("--pdp", metavar="URL", help="resolve o seller de um PDP online")
    parser.add_argument("--html", metavar="ARQUIVO", help="testa a extração num HTML local")
    parser.add_argument("--seller-id", metavar="ID", help="ObjectId a ancorar/gravar")
    parser.add_argument("--scan", metavar="KEYWORD", help="lista os seller IDs de uma busca")
    parser.add_argument("--pages", type=int, default=2, help="páginas no --scan (padrão: 2)")
    parser.add_argument("--set", metavar="ID=NOME", help="grava um par id=nome manualmente")
    args = parser.parse_args()

    cache = LeroySellerCache()

    if args.list:
        return cmd_list(cache)
    if args.pdp:
        return cmd_pdp(cache, args.pdp, args.seller_id)
    if args.html:
        return cmd_html(args.html, args.seller_id)
    if args.scan:
        return cmd_scan(cache, args.scan, max(1, args.pages))
    if args.set:
        return cmd_set(cache, args.set)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
