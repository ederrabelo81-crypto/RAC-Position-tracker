# scrapers package
#
# Magalu e Shopee foram migrados para o projeto Node.js/TypeScript em
# magalu_shopee/ e não fazem mais parte deste pacote Python.
from .mercado_livre import MLScraper
from .amazon import AmazonScraper
from .casas_bahia import CasasBahiaScraper
from .google_shopping import GoogleShoppingScraper
from .leroy_merlin import LeroyMerlinScraper
from .fast_shop import FastShopScraper

__all__ = [
    "MLScraper",
    "AmazonScraper",
    "CasasBahiaScraper",
    "GoogleShoppingScraper",
    "LeroyMerlinScraper",
    "FastShopScraper",
]
