"""
config.py — Configurações globais do bot de inteligência competitiva RAC.

FOCO (a partir de Mai/2026): buy box, sellers e insights de competição.
  Preço continua coletado, porém como campo SECUNDÁRIO. O protagonista agora
  é quem vence a buy box, quantos sellers competem, tipo/reputação do seller,
  share of shelf por marca/seller e orgânico vs patrocinado.

Plataformas monitoradas (7) — dealers/varejistas especializados saíram do foco:
  ✅ Mercado Livre   — funcional (popup de CEP tratado automaticamente)
  ✅ Amazon          — buy box via "Vendido por" / "Enviado por"
  ✅ Magalu          — Python + curl_cffi/browser (Akamai); seller 1P vs 3P
  ✅ Google Shopping — merchants/ofertas por produto
  ✅ Leroy Merlin    — Algolia API (1P Leroy vs 3P marketplace)
  ✅ Casas Bahia     — VTEX intelligent-search API + warm-up de cookies Akamai;
                       array sellers[] expõe vencedor da buy box (sellerDefault)
  🟡 Shopee          — API v4 (search_items) + sessão capturada; flags
                       is_official_shop (Mall) e is_preferred_plus_seller.
                       BEST-EFFORT: instável sem proxy residencial BR; sessão
                       expira em horas → re-capturar com session_grabber.
  ⏸️  Fast Shop       — bloqueio total (PerimeterX bloqueia API + browser timeout)
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Keywords de busca — organizadas por categoria e prioridade
# Prioridade: 'alta' = diário | 'media' = 3x/semana | 'baixa' = semanal
#
# Revisão Jul/2026 — rebalanceamento de marcas + queries conversacionais:
#   • Keywords de marca/modelo retornam SERPs dominadas pela própria marca.
#     A lista anterior tinha 6 termos Midea (alta) vs 1-3 por concorrente, o
#     que inflava mecanicamente o appearance share da Midea nas métricas
#     agregadas. Corrigido: 4 Midea (alta) e cobertura simétrica de
#     concorrentes tier-1 (LG, Samsung, Gree, Consul). Share of shelf neutro
#     deve usar apenas BRAND_NEUTRAL_CATEGORIES (ver abaixo).
#   • "Modelo Midea" → "Modelo / Linha" (agora inclui linhas concorrentes).
#     Registros históricos mantêm o valor antigo — filtrar pelos dois.
#   • Nova categoria "Conversacional IA": queries em linguagem natural no
#     padrão de perguntas feitas a LLMs (ChatGPT/Gemini/Meta AI) e AI
#     Overviews — canal de descoberta em forte crescimento no BR.
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
    # Qualificador nº1 do consumidor BR depois de BTU/inverter (ciclo reverso)
    Keyword("ar condicionado quente e frio",            "Genérica",          "alta"),

    # ── Segmento emergente ──────────────────────────────────────
    # Google Trends BR (verão 2025/26): buscas por "portátil" +~500% no pico
    Keyword("ar condicionado portátil",                 "Segmento",          "media"),

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

    # ── Marca monitorada (Midea) ────────────────────────────────
    # 2 termos alta (era 4+2 modelos) — "ar condicionado midea 12000" removido
    # (quase-duplicata de "midea 12000 btus", que desceu para media)
    Keyword("ar condicionado midea",                    "Marca",             "alta"),
    Keyword("midea inverter",                           "Marca",             "alta"),
    Keyword("midea 12000 btus",                         "Marca",             "media"),

    # ── Modelo / Linha (própria + concorrentes) ─────────────────
    # "lg dual inverter" e "samsung windfree" migraram de "Marca" — são
    # linhas de produto, mesmo nível de granularidade dos modelos Midea
    Keyword("midea ecomaster",                          "Modelo / Linha",    "alta"),
    Keyword("midea airvolution",                        "Modelo / Linha",    "alta"),
    Keyword("lg dual inverter",                         "Modelo / Linha",    "alta"),
    Keyword("samsung windfree",                         "Modelo / Linha",    "alta"),

    # ── Concorrentes ────────────────────────────────────────────
    # Tier-1 em alta (paridade com Midea); tier-2 em media; nicho em baixa
    Keyword("ar condicionado lg",                       "Marca",             "alta"),
    Keyword("ar condicionado samsung",                  "Marca",             "alta"),
    Keyword("ar condicionado gree",                     "Marca",             "alta"),
    Keyword("ar condicionado consul",                   "Marca",             "alta"),
    Keyword("ar condicionado lg dual inverter 12000",   "Marca",             "media"),
    Keyword("ar condicionado electrolux",               "Marca",             "media"),
    Keyword("ar condicionado elgin",                    "Marca",             "media"),
    Keyword("ar condicionado philco",                   "Marca",             "media"),
    Keyword("ar condicionado tcl",                      "Marca",             "media"),
    Keyword("ar condicionado daikin",                   "Marca",             "baixa"),
    Keyword("ar condicionado hisense",                  "Marca",             "baixa"),

    # ── Intenção de compra ──────────────────────────────────────
    # Versões com acento são suficientes — marketplaces normalizam acento
    # ⚠️ "melhor ar condicionado <ano>": atualizar o ano a cada virada
    Keyword("melhor ar condicionado custo benefício",   "Intenção Compra",   "alta"),
    Keyword("melhor ar condicionado 2026",              "Intenção Compra",   "alta"),
    Keyword("comprar ar condicionado",                  "Intenção Compra",   "media"),
    Keyword("ar condicionado em promoção",              "Preço / Promoção",  "media"),

    # ── Conversacional / IA ─────────────────────────────────────
    # Linguagem natural no padrão de prompts a LLMs e AI Overviews:
    # necessidade (quarto), atributo (econômico, silencioso, conectado).
    # Marketplaces normalizam stopwords — as queries retornam SERP válida.
    Keyword("ar condicionado inverter mais econômico",  "Conversacional IA", "media"),
    Keyword("melhor ar condicionado para quarto",       "Conversacional IA", "media"),
    Keyword("ar condicionado silencioso",               "Conversacional IA", "media"),
    Keyword("ar condicionado wifi",                     "Conversacional IA", "baixa"),
]

# ---------------------------------------------------------------------------
# Categorias brand-neutral vs brand-directed — para métricas de share.
#
# Keywords dirigidas a marca ("ar condicionado midea", "samsung windfree")
# retornam SERPs dominadas pela própria marca: incluí-las em share of shelf /
# appearance share agregado infla a marca com mais keywords na lista (viés de
# coleta, não de mercado). Métricas de share neutras devem filtrar
# `Categoria Keyword` ∈ BRAND_NEUTRAL_CATEGORIES; as categorias dirigidas
# servem para monitorar buy box, sortimento e preço da marca específica.
# "Modelo Midea" é o valor legado (registros até Jul/2026), mantido para
# filtros retroativos no histórico.
# ---------------------------------------------------------------------------
BRAND_NEUTRAL_CATEGORIES: List[str] = [
    "Genérica",
    "Segmento",
    "Capacidade BTU",
    "Capacidade + Tipo",
    "Intenção Compra",
    "Preço / Promoção",
    "Conversacional IA",
]
BRAND_DIRECTED_CATEGORIES: List[str] = [
    "Marca",
    "Modelo / Linha",
    "Modelo Midea",  # legado
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
    "amazon":         True,   # ✅ Amazon — buy box via "Vendido por"
    "magalu":         True,   # ✅ Magalu — Python+curl_cffi/browser (Akamai bypass)
    "casasbahia":     True,   # ✅ Casas Bahia — VTEX IS API + warm-up Akamai (sellers[])
    "google_shopping":True,   # ✅ Google Shopping — merchants/ofertas por produto
    "leroy":          True,   # ✅ Leroy Merlin — Algolia API (1P vs 3P marketplace)
    "shopee":         True,   # 🟡 Shopee — API v4 + sessão (best-effort, instável sem proxy)
    "fast":           False,  # ⏸️  Fast Shop — bloqueio total (PerimeterX + browser timeout)
    "dealers":        False,  # ⏸️  Dealers — fora do foco (insights de marketplace)
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
    # Dealers/varejistas especializados — Sprint 0 (originais)
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
    # Sprint 1 — Nacional/Grande Porte
    "Carrefour":        "Nacional Retail",
    # Sprint 2 — Regional Médio Porte
    "GrupoMateus":      "Regional Especializado",
    "Eletrozema":       "Regional Especializado",
    "Angeloni":         "Regional Especializado",
    "ImperioDigital":   "Regional Especializado",
    # Sprint 3 — Regional Pequeno Porte
    "NossoLar":         "Regional Especializado",
    "Bemol":            "Regional Especializado",
    "CasasDAgua":       "Regional Especializado",
    "TVLar":            "Regional Especializado",
    "Zenir":            "Regional Especializado",
    "CenterKennedy":    "Regional Especializado",
    "NorteRefrigeracao": "Regional Especializado",
    "ArmazemParaiba":   "Regional Especializado",
    "ADias":            "Regional Especializado",
    # Sprint 4 — Regional Especializado/Nicho
    "Carajas":          "Regional Especializado",
    "QueroQuero":       "Regional Especializado",
    "Fujioka":          "Regional Especializado",
    "Edimil":           "Regional Especializado",
    "UnicaAR":          "Regional Especializado",
    "TopMoveis":        "Regional Especializado",
    "Martinello":       "Regional Especializado",
    "GBarbosa":         "Regional Retail",
    "Gazin":            "Regional Retail",
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
    # Novas marcas adicionadas Abril 2026
    "AIWA",
    "American Range",
    "Geminis",
    "Fontaine",
    "Luxor",
    "Turbro",
    "Velleman",
    "Whynter",
    "DeLonghi",
    "Kian",
    "Equation",
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

# ---------------------------------------------------------------------------
# Screenshots (página de busca + produto)
# Habilitado por padrão — captura evidência da SERP de cada coleta.
# Para desligar (zero overhead, ScreenshotManager não é instanciado),
# defina ENABLE_SCREENSHOTS=false no .env.
# ---------------------------------------------------------------------------
ENABLE_SCREENSHOTS: bool = os.getenv("ENABLE_SCREENSHOTS", "true").strip().lower() in (
    "1", "true", "yes", "sim", "on"
)
SCREENSHOTS_DIR: str = "screenshots"
SCREENSHOTS_RETENTION_DAYS: int = 15
SCREENSHOTS_BUCKET: str = "rac-screenshots"
SCREENSHOTS_VIEWPORT: tuple = (1920, 1080)

# Upload para o Supabase Storage. Desligado por padrão: o plano free tem
# apenas 1 GB de Storage e 15 dias de coleta estouram esse limite. Os
# screenshots ficam apenas em SCREENSHOTS_DIR (acesso local direto).
# Para reativar (ex: em plano pago), defina SCREENSHOTS_UPLOAD_SUPABASE=true.
SCREENSHOTS_UPLOAD_SUPABASE: bool = os.getenv(
    "SCREENSHOTS_UPLOAD_SUPABASE", "false"
).strip().lower() in ("1", "true", "yes", "sim", "on")
