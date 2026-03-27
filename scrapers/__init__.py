# scrapers package
from .mercado_livre import MLScraper
from .magalu import MagaluScraper
from .amazon import AmazonScraper
from .shopee import ShopeeScraper
from .casas_bahia import CasasBahiaScraper
from .google_shopping import GoogleShoppingScraper
from .leroy_merlin import LeroyMerlinScraper
from .fast_shop import FastShopScraper

__all__ = [
    "MLScraper",
    "MagaluScraper",
    "AmazonScraper",
    "ShopeeScraper",
    "CasasBahiaScraper",
    "GoogleShoppingScraper",
    "LeroyMerlinScraper",
    "FastShopScraper",
]
