# scrapers package
from .mercado_livre import MLScraper
from .magalu import MagaluScraper
from .amazon import AmazonScraper
from .shopee import ShopeeScraper
from .leroy_merlin import LeroyMerlinScraper
from .fast_shop import FastShopScraper

__all__ = [
    "MLScraper",
    "MagaluScraper",
    "AmazonScraper",
    "ShopeeScraper",
    "LeroyMerlinScraper",
    "FastShopScraper",
]
