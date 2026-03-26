"""
config.py — Configurações globais do bot de monitoramento de preços.
Centraliza keywords, limites de paginação, timeouts e mapeamentos de plataforma.
"""

from dataclasses import dataclass, field
from typing import Dict, List

# ---------------------------------------------------------------------------
# Keywords de busca por categoria
# ---------------------------------------------------------------------------
KEYWORDS: Dict[str, List[str]] = {
    "Capacidade": [
        "ar condicionado split 9000 btus",
        "ar condicionado split 12000 btus",
        "ar condicionado split 18000 btus",
        "ar condicionado split 24000 btus",
    ],
    "Tecnologia": [
        "ar condicionado inverter 12000",
        "ar condicionado inverter 18000",
        "ar condicionado inverter 24000",
    ],
    "Portátil": [
        "ar condicionado portatil 12000 btus",
    ],
    "Janela": [
        "ar condicionado janela 12000 btus",
    ],
}

# ---------------------------------------------------------------------------
# Limites operacionais
# ---------------------------------------------------------------------------
MAX_PAGES: int = 3          # máximo de páginas por keyword
MIN_DELAY: float = 2.0      # delay mínimo entre ações (segundos)
MAX_DELAY: float = 7.0      # delay máximo entre ações (segundos)
PAGE_TIMEOUT: int = 30_000  # timeout de carregamento de página (ms)
NETWORK_IDLE_TIMEOUT: int = 8_000  # tempo aguardando rede estabilizar (ms)

# ---------------------------------------------------------------------------
# Metadados fixos
# ---------------------------------------------------------------------------
ANALYST_NAME: str = "Bot Automático Python"

# Turno inferido por faixa horária (hora inteira, 0-23)
TURNO_ABERTURA_MAX_HOUR: int = 13  # até 13h → Abertura; após → Fechamento

# ---------------------------------------------------------------------------
# Mapeamento de plataforma → tipo
# ---------------------------------------------------------------------------
PLATFORM_TYPE: Dict[str, str] = {
    "Mercado Livre": "Nacional Retail",
    "Magalu":        "Nacional Retail",
    "Amazon":        "Nacional Retail",
    "Shopee":        "Nacional Marketplace",
    "Leroy Merlin":  "Nacional Varejo Especializado",
    "Fast Shop":     "Nacional Varejo Especializado",
    # Sellers especializados em ar-condicionado
    "Leveros":         "Regional Especializado",
    "Frio Peças":      "Regional Especializado",
    "Clima Rio":       "Regional Especializado",
    "Frigelar":        "Regional Especializado",
    "DuFrio":          "Regional Especializado",
    "Web Continental": "Nacional Varejo Especializado",
    "Go Compras":      "Nacional Marketplace",
}

# ---------------------------------------------------------------------------
# Lista de marcas monitoradas (ordem importa: mais específicas primeiro)
# ---------------------------------------------------------------------------
BRANDS: List[str] = [
    "Midea",
    "Elgin",
    "Electrolux",
    "Agratto",
    "Springer",
    "Consul",
    "Daikin",
    "Fujitsu",
    "Hitachi",
    "Carrier",
    "York",
    "Gree",
    "Komeco",
    "LG",
    "Samsung",
    "Philco",
    "Panasonic",
    "Trane",
    "Rheem",
    "Lennox",
    "Inverter",   # fallback tecnológico
]

# ---------------------------------------------------------------------------
# User-Agents modernos para rotação
# ---------------------------------------------------------------------------
USER_AGENTS: List[str] = [
    # Chrome 124 / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome 124 / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox 125 / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0",
    # Edge 124 / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # Chrome 124 / Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# ---------------------------------------------------------------------------
# Diretórios de saída
# ---------------------------------------------------------------------------
OUTPUT_DIR: str = "output"
LOGS_DIR: str = "logs"
