"""
bestsellers/sources — Coletores por plataforma.

O registro abaixo é a única ponte entre a chave da fonte (`config.SOURCES`) e
a classe que a coleta. Adicionar varejista novo:

  1. Achar a URL com a ordenação POR VENDAS e testá-la no navegador. Se a
     ordenação não existir de forma explícita, **não adicionar** — relevância
     não serve e não é comparável com o resto da série.
  2. Registrar a fonte em `bestsellers/config.py` (URL, parâmetro canônico,
     mecânica, base de `vendidos`).
  3. Implementar a classe aqui e registrá-la em `SOURCE_CLASSES`.
  4. Rodar 3 dias antes de usar em decisão.

Prioridade de expansão, por volume de ofertas no painel PriceTrack: Extra,
Ponto, Carrefour, Americanas, Web Continental.
"""

from typing import Dict, Type

from bestsellers.base import BestSellerSource
from bestsellers.sources.amazon import AmazonBestSellers
from bestsellers.sources.casas_bahia import CasasBahiaBestSellers
from bestsellers.sources.leroy_merlin import LeroyMerlinBestSellers
from bestsellers.sources.magalu import MagaluBestSellers
from bestsellers.sources.mercado_livre import MercadoLivreBestSellers
from bestsellers.sources.shopee import ShopeeBestSellers

SOURCE_CLASSES: Dict[str, Type[BestSellerSource]] = {
    "amazon": AmazonBestSellers,
    "mercadolivre": MercadoLivreBestSellers,
    "magazineluiza": MagaluBestSellers,
    "shopee": ShopeeBestSellers,
    "leroymerlin": LeroyMerlinBestSellers,
    "casasbahia": CasasBahiaBestSellers,
}

__all__ = ["SOURCE_CLASSES", "BestSellerSource"]
