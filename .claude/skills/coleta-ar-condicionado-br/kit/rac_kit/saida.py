"""Montagem do registro e gravação em CSV (Excel PT-BR) + relatório de cobertura."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .offer_identity import build_identity
from .offer_kind import classify_offer
from .sites import Site
from .texto import extract_brand, extract_btu, get_turno, now_brt

COLUNAS: List[str] = [
    "Data", "Turno", "Horário", "Plataforma", "Keyword Buscada",
    "Marca Monitorada", "Produto / SKU", "BTU", "Tipo Anúncio",
    "Posição Orgânica", "Posição Patrocinada", "Posição Geral", "Patrocinado?",
    "Buy Box Seller", "Fonte Buy Box", "Seller / Vendedor", "Preço (R$)",
    "URL Produto", "ID Produto Marketplace", "URL Canônica", "Offer Key",
]


def montar_registro(
    card: Dict[str, Any],
    site: Site,
    keyword: str,
    momento: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Transforma um card extraído numa linha do CSV.

    ``Buy Box Seller`` só é preenchido quando foi OBSERVADO (no card ou no
    PDP). ``Tipo Anúncio`` separa o aparelho (UNIDADE) de kit de instalação,
    peça, meia unidade e pacote com 2+ aparelhos, que não entram em série de
    preço.
    """
    agora = momento or now_brt()
    titulo = card.get("titulo") or ""
    url = card.get("url")
    buy_box = card.get("buy_box_seller") or card.get("seller")
    identidade = build_identity(
        platform=site.nome,
        url=url,
        title=titulo,
        marketplace_product_id=card.get("id_produto"),
    )
    return {
        "Data": agora.strftime("%Y-%m-%d"),
        "Turno": get_turno(agora),
        "Horário": agora.strftime("%H:%M"),
        "Plataforma": site.nome,
        "Keyword Buscada": keyword,
        "Marca Monitorada": extract_brand(titulo),
        "Produto / SKU": titulo or None,
        "BTU": extract_btu(titulo),
        "Tipo Anúncio": classify_offer(titulo, url),
        "Posição Orgânica": card.get("posicao_organica"),
        "Posição Patrocinada": card.get("posicao_patrocinada"),
        "Posição Geral": card.get("posicao_geral"),
        "Patrocinado?": "Sim" if card.get("patrocinado") else "Não",
        "Buy Box Seller": buy_box,
        "Fonte Buy Box": card.get("fonte_buy_box") or ("card" if buy_box else None),
        "Seller / Vendedor": card.get("seller"),
        "Preço (R$)": card.get("preco"),
        "URL Produto": url,
        "ID Produto Marketplace": identidade.marketplace_product_id,
        "URL Canônica": identidade.canonical_url,
        "Offer Key": identidade.offer_key,
    }


def gravar_csv(registros: Iterable[Dict[str, Any]], pasta: Path, rotulo: str) -> Path:
    """Grava o CSV (``;`` + UTF-8 com BOM, que o Excel brasileiro abre direto)."""
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / f"coleta_{rotulo}_{now_brt():%Y%m%d_%H%M}.csv"
    with caminho.open("w", newline="", encoding="utf-8-sig") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUNAS, delimiter=";")
        escritor.writeheader()
        for registro in registros:
            escritor.writerow({c: registro.get(c) for c in COLUNAS})
    return caminho


def gravar_cobertura(cobertura: List[Dict[str, Any]], csv_path: Path) -> Path:
    """Grava, ao lado do CSV, o que foi e o que NÃO foi observado.

    Sem este arquivo, uma keyword bloqueada some do CSV e quem lê entende que
    os concorrentes sumiram da busca. Silêncio não é mercado.
    """
    caminho = csv_path.with_suffix(".cobertura.json")
    caminho.write_text(
        json.dumps(cobertura, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return caminho
