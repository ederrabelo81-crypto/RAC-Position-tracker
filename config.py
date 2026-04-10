"""
config.py — Configurações globais do bot de monitoramento de preços.

Status das plataformas (validado em produção — Mar/2026):
  ✅ Mercado Livre   — funcional (popup de CEP tratado automaticamente)
  ✅ Amazon          — funcional
  ✅ Magalu          — funcional (seletores confirmados via diagnóstico)
  ✅ Google Shopping — funcional
  ✅ Leroy Merlin    — funcional (Algolia API, preço via averagePromotionalPrice)
  ⏸️  Fast Shop       — bloqueio total (PerimeterX bloqueia API + browser timeout)
  ⏸️  Shopee         — em stand by (requer sessão autenticada via session_grabber)
  ⏸️  Casas Bahia    — em stand by (WAF Akamai, requer sessão via session_grabber)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Keywords de busca — organizadas por categoria e prioridade
# Prioridade: 'alta' = diário | 'media' = 3x/semana | 'baixa' = semanal
# ---------------------------------------------------------------------------

@dataclass
class Keyword:
    term: str
    category: str
    priority: str = "alta"  # alta | media | baixa


KEYWORDS_LIST: List[Keyword] = [
    # ── Head terms genéricos ────────────────────────────────────
    # Maior volume absoluto no Google Search BR — base da pirâmide RAC
    Keyword("ar condicionado split",                    "Genérica",          "alta"),
    Keyword("ar condicionado inverter",                 "Genérica",          "alta"),
    Keyword("ar condicionado",                          "Genérica",          "alta"),
    Keyword("ar condicionado split inverter",           "Genérica",          "alta"),

    # ── Capacidade / BTU ────────────────────────────────────────
    # 9k e 12k são os BTUs mais buscados no Brasil (ambientes até 20m²)
    # 18k e 24k = ticket maior, consumidor mais qualificado
    Keyword("ar condicionado 9000 btus",                "Capacidade BTU",    "alta"),
    Keyword("ar condicionado 12000 btus",               "Capacidade BTU",    "alta"),
    Keyword("ar condicionado 18000 btus",               "Capacidade BTU",    "alta"),
    Keyword("ar condicionado 24000 btus",               "Capacidade BTU",    "alta"),
    # Combos BTU+inverter: maior intenção de compra no Google Search
    Keyword("ar condicionado 9000 btus inverter",       "Capacidade + Tipo", "alta"),
    Keyword("ar condicionado 12000 btus inverter",      "Capacidade + Tipo", "alta"),
    Keyword("split 12000 btus inverter",                "Capacidade + Tipo", "alta"),
    Keyword("split 9000 btus inverter",                 "Capacidade + Tipo", "alta"),

    # ── Marca Midea ─────────────────────────────────────────────
    Keyword("ar condicionado midea",                    "Marca",             "alta"),
    Keyword("midea inverter",                           "Marca",             "alta"),
    Keyword("midea 12000 btus",                         "Marca",             "alta"),
    Keyword("ar condicionado midea 12000",              "Marca",             "alta"),
    Keyword("midea ecomaster",                          "Modelo Midea",      "alta"),
    Keyword("midea airvolution",                        "Modelo Midea",      "alta"),

    # ── Concorrentes ────────────────────────────────────────────
    Keyword("ar condicionado lg",                       "Marca",             "alta"),
    Keyword("lg dual inverter",                         "Marca",             "alta"),
    Keyword("ar condicionado lg dual inverter 12000",   "Marca",             "alta"),
    Keyword("ar condicionado samsung",                  "Marca",             "alta"),
    Keyword("samsung windfree",                         "Marca",             "alta"),
    Keyword("ar condicionado gree",                     "Marca",             "media"),
    Keyword("ar condicionado elgin",                    "Marca",             "media"),
    Keyword("ar condicionado philco",                   "Marca",             "media"),
    Keyword("ar condicionado tcl",                      "Marca",             "media"),

    # ── Intenção de compra ──────────────────────────────────────
    # Versões com acento são suficientes — marketplaces normalizam acento
    Keyword("melhor ar condicionado custo benefício",   "Intenção Compra",   "alta"),
    Keyword("melhor ar condicionado 2026",              "Intenção Compra",   "alta"),
    Keyword("comprar ar condicionado",                  "Intenção Compra",   "media"),
    Keyword("ar condicionado em promoção",              "Preço / Promoção",  "media"),
]

# Mantém compatibilidade com o formato dict usado em config legado
KEYWORDS: Dict[str, List[str]] = {}
for kw in KEYWORDS_LIST:
    KEYWORDS.setdefault(kw.category, []).append(kw.term)

# ---------------------------------------------------------------------------
# Filtro de prioridade — None = todas, ["alta"] = só alta prioridade
# ---------------------------------------------------------------------------
PRIORITY_FILTER: Optional[List[str]] = None  # ex: ["alta"] para coletas rápidas

# ---------------------------------------------------------------------------
# Plataformas ativas
# ---------------------------------------------------------------------------
ACTIVE_PLATFORMS = {
    "ml":             True,   # ✅ Mercado Livre — funcional
    "magalu":         True,   # ✅ Magalu — seletores nm-* atualizados
    "amazon":         True,   # ✅ Amazon — extração de seller corrigida
    "shopee":         False,  # ⏸️  Shopee — em stand by
    "casasbahia":     False,  # ⏸️  Casas Bahia — em stand by
    "google_shopping":True,   # ✅ Google Shopping — limpeza de título corrigida
    "leroy":          True,   # ✅ Leroy Merlin — funcional (Algolia API)
    "fast":           False,  # ⏸️  Fast Shop — bloqueio total (PerimeterX + browser timeout)
    "dealers":        True,   # ✅ Dealers/varejistas especializados
}

# ---------------------------------------------------------------------------
# Limites operacionais
# ---------------------------------------------------------------------------
MAX_PAGES: int = 3          # máximo de páginas por keyword
MIN_DELAY: float = 4.0      # delay mínimo entre ações (segundos) — baseado em v5
MAX_DELAY: float = 7.0      # delay máximo entre ações (segundos)
PAGE_TIMEOUT: int = 45_000  # timeout de carregamento de página (ms) — baseado em v5
NETWORK_IDLE_TIMEOUT: int = 8_000
RETRY_ATTEMPTS: int = 2     # tentativas em caso de falha

# ---------------------------------------------------------------------------
# Metadados fixos
# ---------------------------------------------------------------------------
ANALYST_NAME: str = "Bot Automático Python"
TURNO_ABERTURA_MAX_HOUR: int = 12  # até 12h → Abertura; após → Fechamento

# ---------------------------------------------------------------------------
# Mapeamento de plataforma → tipo
# ---------------------------------------------------------------------------
PLATFORM_TYPE: Dict[str, str] = {
    "Mercado Livre":    "Nacional Retail",
    "Magalu":           "Nacional Retail",
    "Amazon":           "Nacional Retail",
    "Shopee":           "Nacional Marketplace",
    "Casas Bahia":      "Nacional Retail",
    "Google Shopping":  "Comparador de Preços",
    "Leroy Merlin":     "Nacional Varejo Especializado",
    "Fast Shop":        "Nacional Varejo Especializado",
    # Dealers/varejistas especializados
    "Frigelar":         "Regional Especializado",
    "CentralAr":        "Regional Especializado",
    "PoloAr":           "Regional Especializado",
    "Belmicro":         "Regional Especializado",
    "GoCompras":        "Nacional Marketplace",
    "FrioPecas":        "Regional Especializado",
    "WebContinental":   "Nacional Varejo Especializado",
    "Dufrio":           "Regional Especializado",
    "Leveros":          "Regional Especializado",
    "ArCerto":          "Regional Especializado",
    "FerreiraCosta":    "Regional Especializado",
    "Climario":         "Regional Especializado",
    "EngageEletro":     "Regional Especializado",
}

# ---------------------------------------------------------------------------
# Lista de marcas monitoradas
# ---------------------------------------------------------------------------
BRANDS: List[str] = [
    "Springer Midea",   # ordem importa: mais específicas primeiro
    "Midea Carrier",
    "Midea",
    "Carrier",
    "Elgin",
    "Electrolux",
    "Agratto",
    "Springer",
    "Consul",
    "Daikin",
    "Fujitsu",
    "Hitachi",
    "York",
    "Gree",
    "TCL",
    "Hisense",
    # Marcas low-tier com presença crescente no segmento RAC sub-R$2K
    "Aufit",
    "Haier",
    "Britânia",         # grafia com acento (mais comum em produtos)
    "Britania",         # sem acento (fallback)
    "Vix",
    "HQ",
    "Colormaq",
    # Outras
    "EOS",
    "Komeco",
    "LG",
    "Samsung",
    "Philco",
    "Panasonic",
    "Trane",
    "Rheem",
    "Lennox",
]

# ---------------------------------------------------------------------------
# User-Agents modernos para rotação
# ---------------------------------------------------------------------------
USER_AGENTS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# ---------------------------------------------------------------------------
# Diretórios de saída
# ---------------------------------------------------------------------------
OUTPUT_DIR: str = "output"
LOGS_DIR: str = "logs"
DIAGNOSTICO_DIR: str = "diagnostico"
