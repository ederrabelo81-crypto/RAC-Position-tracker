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
from .texto import extract_brand, extract_btu, get_turno, now_brt, tem_evidencia_ac

# Produto que a busca devolveu mas não é ar condicionado (anúncio de geladeira
# "Inverter", por exemplo). A linha fica — a posição que ele ocupa é fato da
# página — mas com este tipo, para não entrar em preço nem em share de marca.
FORA_ESCOPO = "FORA_ESCOPO"

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
    preço — e marca como ``FORA_ESCOPO`` o que nem é ar condicionado.
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
        "Tipo Anúncio": (
            FORA_ESCOPO if titulo and not tem_evidencia_ac(titulo)
            else classify_offer(titulo, url)
        ),
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
    """Grava o CSV no formato do Excel brasileiro.

    Separador ``;``, UTF-8 com BOM e preço com vírgula decimal. Em pandas,
    leia com ``sep=";", decimal=","``. Nunca sobrescreve um arquivo existente.
    """
    pasta.mkdir(parents=True, exist_ok=True)
    base = f"coleta_{rotulo}_{now_brt():%Y%m%d_%H%M%S}"
    caminho, n = pasta / f"{base}.csv", 1
    while True:
        try:
            # "x" falha se o arquivo existe: uma coleta nunca apaga outra.
            f = caminho.open("x", newline="", encoding="utf-8-sig")
            break
        except FileExistsError:
            n += 1
            caminho = pasta / f"{base}_{n}.csv"
    with f:
        escritor = csv.DictWriter(f, fieldnames=COLUNAS, delimiter=";")
        escritor.writeheader()
        for registro in registros:
            linha = {c: registro.get(c) for c in COLUNAS}
            preco = linha.get("Preço (R$)")
            if isinstance(preco, (int, float)):
                # Vírgula decimal: com ponto o Excel brasileiro lê texto.
                linha["Preço (R$)"] = f"{preco:.2f}".replace(".", ",")
            escritor.writerow(linha)
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
